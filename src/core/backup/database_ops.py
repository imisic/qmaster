"""Database backup and restore operations."""

import gzip
import logging
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config_manager import ConfigManager
from .constants import (
    DEFAULT_DATABASE_RETENTION_DAYS,
    MIN_DB_BACKUP_SPACE_MB,
)


class DatabaseBackupMixin:
    """Mixin providing database backup and restore methods.

    Expects the following attributes on the composing class:
        config: ConfigManager instance
        logger: logging.Logger instance
        local_path: Path to local backup storage
        notifier: NotificationManager | None
    """

    config: ConfigManager
    logger: logging.Logger
    local_path: Path
    notifier: Any

    def _create_mysql_config_file(self, db_config: dict[str, Any]) -> str:
        """Create a temporary MySQL configuration file with credentials (secure approach)"""
        # Create temporary file with secure permissions
        old_umask = os.umask(0o077)
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".cnf", text=True)
        finally:
            os.umask(old_umask)

        try:
            # Permissions already restricted via umask; belt-and-suspenders chmod
            os.chmod(temp_path, 0o600)

            # Write MySQL configuration
            # Password must be quoted to handle special characters like # (comment char)
            password = db_config.get("password", "")
            password = password.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\0", "")
            config_content = f"""[client]
host={db_config.get("host", self._get_mysql_default("host"))}
port={db_config.get("port", self._get_mysql_default("port"))}
user={db_config.get("user", self._get_mysql_default("user"))}
password="{password}"
"""

            os.write(fd, config_content.encode("utf-8"))
            os.close(fd)

            return temp_path

        except Exception as e:
            os.close(fd)
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def backup_database(
        self, db_name: str, description: str | None = None, skip_if_exists_today: bool = False
    ) -> tuple[bool, str]:
        """Backup a single database

        Args:
            db_name: Name of the database to backup
            description: Optional description for the backup
            skip_if_exists_today: Skip if backup already exists for today
        """
        db_config = self.config.get_database(db_name)

        if not db_config:
            return False, f"Database '{db_name}' not found in configuration"

        if not db_config.get("backup", {}).get("enabled", True):
            return False, f"Backup disabled for database '{db_name}'"

        # Validate database name to prevent injection
        try:
            self._validate_identifier(db_name, "database name")
        except ValueError as e:
            return False, str(e)

        # Check disk space before starting backup (conservative estimate: 1GB minimum)
        local_backup_dir = self.local_path / "databases" / db_name
        local_backup_dir.mkdir(parents=True, exist_ok=True)

        # Check if backup already exists today
        if skip_if_exists_today and self._has_backup_today(local_backup_dir, db_name):
            return True, "Skipped: backup already exists for today"

        min_required_space = self.config.get_setting("min_db_backup_space_mb", MIN_DB_BACKUP_SPACE_MB) * 1024 * 1024
        space_ok, space_msg = self._check_disk_space(local_backup_dir, min_required_space, safety_margin=1.1)

        if not space_ok:
            self.logger.error(f"Disk space check failed for database '{db_name}': {space_msg}")
            return False, space_msg

        self.logger.debug(f"Disk space check passed: {space_msg}")

        mysql_config_file = None
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{db_name}_{timestamp}.sql"

            # Local backup path
            local_backup_path = local_backup_dir / backup_name

            self.logger.info(f"Starting backup of database '{db_name}'")

            # Create secure MySQL configuration file
            mysql_config_file = self._create_mysql_config_file(db_config)

            # Build mysqldump command using config file (secure - no password in process list)
            cmd = [
                "mysqldump",
                f"--defaults-extra-file={mysql_config_file}",
            ]

            # Add custom options (validated against explicit allowlist)
            allowed_mysqldump_options = {
                "--single-transaction", "--routines", "--triggers", "--events",
                "--add-drop-database", "--add-drop-table", "--no-tablespaces",
                "--no-data", "--no-create-info", "--skip-lock-tables",
                "--quick", "--extended-insert", "--hex-blob", "--set-gtid-purged",
                "--column-statistics", "--skip-column-statistics",
                "--complete-insert", "--compress", "--databases",
                "--skip-add-drop-table", "--skip-triggers", "--skip-routines",
            }
            options = db_config.get("backup", {}).get("options", [])
            for opt in options:
                opt_name = opt.split("=")[0]
                if opt_name not in allowed_mysqldump_options:
                    return False, f"Disallowed mysqldump option: {opt_name}. Check settings for allowed options."
            cmd.extend(options)

            # Add database name
            cmd.append(db_name)

            # Execute mysqldump
            with open(local_backup_path, "w") as f:
                result = subprocess.run(
                    cmd, stdout=f, stderr=subprocess.PIPE, text=True, timeout=self._get_timeout("mysqldump")
                )

            if result.returncode != 0:
                self.logger.debug(f"mysqldump stderr: {result.stderr}")
                raise RuntimeError("mysqldump failed (check logs for details)")

            # Compress if requested
            if db_config.get("backup", {}).get("compress", True):
                with open(local_backup_path, "rb") as f_in, gzip.open(f"{local_backup_path}.gz", "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

                os.remove(local_backup_path)
                local_backup_path = Path(f"{local_backup_path}.gz")
                backup_name = f"{backup_name}.gz"

            # Finalize: permissions, metadata, sync, symlink, retention
            latest_ext = ".sql.gz" if db_config.get("backup", {}).get("compress", True) else ".sql"
            retention_days = db_config.get("backup", {}).get(
                "retention_days",
                self.config.get_setting("defaults.database.retention_days", DEFAULT_DATABASE_RETENTION_DAYS),
            )
            size_mb = self._finalize_backup(
                local_backup_path, backup_name, db_name, "database", description,
                f"databases/{db_name}", f"latest{latest_ext}", retention_days,
            )
            self.logger.info(f"Successfully backed up database '{db_name}' ({size_mb:.2f} MB)")

            if self.notifier:
                self.notifier.notify_backup_success(db_name, "database", size_mb)

            return True, f"Backup successful: {backup_name} ({size_mb:.2f} MB)"

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Failed to backup database '{db_name}': {error_msg}", exc_info=True)

            # Clean up partial backup files on failure
            if "local_backup_path" in locals():
                for partial in [local_backup_path, Path(f"{local_backup_path}.gz")]:
                    if partial.exists():
                        try:
                            partial.unlink()
                            self.logger.info(f"Cleaned up partial backup: {partial.name}")
                        except OSError:
                            pass

            # Send failure notification
            if self.notifier:
                self.notifier.notify_backup_failure(db_name, "database", error_msg)

            return False, f"Backup failed: {error_msg}"

        finally:
            # Always clean up the temporary MySQL config file
            if mysql_config_file and os.path.exists(mysql_config_file):
                try:
                    os.remove(mysql_config_file)
                    self.logger.debug("Removed temporary MySQL config file")
                except Exception as e:
                    self.logger.warning(f"Failed to remove temporary config file: {e}")

    def backup_all_databases(
        self, parallel: bool = True, skip_if_exists_today: bool = False
    ) -> dict[str, tuple[bool, str]]:
        """Backup all enabled databases (optionally in parallel)

        Args:
            parallel: If True, run backups in parallel using ThreadPoolExecutor
            skip_if_exists_today: Skip databases that already have a backup today
        """
        results = {}
        db_names = list(self.config.get_all_databases())

        if not parallel or len(db_names) <= 1:
            # Sequential execution
            for db_name in db_names:
                results[db_name] = self.backup_database(db_name, skip_if_exists_today=skip_if_exists_today)
        else:
            # Parallel execution
            max_workers = self.config.get_setting("system.max_parallel_backups", 4)
            self.logger.info(f"Starting parallel backup of {len(db_names)} databases with {max_workers} workers")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all backup tasks
                future_to_db = {
                    executor.submit(self.backup_database, db_name, None, skip_if_exists_today): db_name
                    for db_name in db_names
                }

                # Collect results as they complete
                for future in as_completed(future_to_db):
                    db_name = future_to_db[future]
                    try:
                        results[db_name] = future.result()
                    except Exception as e:
                        self.logger.error(f"Parallel backup failed for database '{db_name}': {e}", exc_info=True)
                        results[db_name] = (False, f"Backup failed: {e!s}")

        return results

    def restore_database(self, db_name: str, backup_file: str) -> tuple[bool, str]:
        """Restore a database from backup"""
        self._validate_backup_filename(backup_file)
        backup_path = self.local_path / "databases" / db_name / backup_file

        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        db_config = self.config.get_database(db_name)
        if not db_config:
            return False, f"Database '{db_name}' not found in configuration"

        # Validate database name to prevent injection
        try:
            self._validate_identifier(db_name, "database name")
        except ValueError as e:
            return False, str(e)

        # Verify backup integrity before restoring
        verify_ok, verify_msg = self.verify_backup("database", db_name, backup_file)
        if not verify_ok:
            self.logger.warning(f"Backup verification failed: {verify_msg}")
            return False, f"Restore aborted - backup verification failed: {verify_msg}"

        mysql_config_file = None
        sql_file = None
        try:
            # Decompress if needed
            if backup_file.endswith(".gz"):
                with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
                    with gzip.open(backup_path, "rb") as f_in:
                        shutil.copyfileobj(f_in, tmp)
                    sql_file = tmp.name
            else:
                sql_file = str(backup_path)

            # Create secure MySQL configuration file
            mysql_config_file = self._create_mysql_config_file(db_config)

            # Build mysql command using config file (secure - no password in process list)
            cmd = ["mysql", f"--defaults-extra-file={mysql_config_file}", db_name]

            # Execute restore
            with open(sql_file) as f:
                result = subprocess.run(
                    cmd, stdin=f, stderr=subprocess.PIPE, text=True, timeout=self._get_timeout("mysql_restore")
                )

            if result.returncode != 0:
                self.logger.debug(f"mysql restore stderr: {result.stderr}")
                raise RuntimeError("mysql restore failed (check logs for details)")

            self.logger.info(f"Successfully restored database '{db_name}' from {backup_file}")
            return True, "Database restored successfully"

        except Exception as e:
            self.logger.error(f"Failed to restore database '{db_name}': {e!s}", exc_info=True)
            return False, f"Restore failed: {e!s}"

        finally:
            # Clean up temporary files
            if sql_file and backup_file.endswith(".gz") and os.path.exists(sql_file):
                try:
                    os.remove(sql_file)
                except Exception as e:
                    self.logger.warning(f"Failed to remove temporary SQL file: {e}")

            if mysql_config_file and os.path.exists(mysql_config_file):
                try:
                    os.remove(mysql_config_file)
                    self.logger.debug("Removed temporary MySQL config file")
                except Exception as e:
                    self.logger.warning(f"Failed to remove temporary config file: {e}")
