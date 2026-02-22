"""Core Backup Engine for Quartermaster"""

import fnmatch
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config_manager import ConfigManager
from .git_manager import GitManager

# Try to import notifications, but don't fail if not available
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.notifications import NotificationManager as _NotificationManager

    NotificationManagerClass: type | None = _NotificationManager
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False
    NotificationManagerClass = None

# Subprocess timeout constants (seconds)
MYSQLDUMP_TIMEOUT = 3600  # 1 hour for database dumps
MYSQL_RESTORE_TIMEOUT = 7200  # 2 hours for database restores
GIT_BUNDLE_TIMEOUT = 1800  # 30 min for git bundle create
GIT_CLONE_TIMEOUT = 1800  # 30 min for git clone/fetch
GIT_VERIFY_TIMEOUT = 300  # 5 min for git bundle verify

# MySQL connection defaults
DEFAULT_MYSQL_HOST = "localhost"
DEFAULT_MYSQL_PORT = 3306
DEFAULT_MYSQL_USER = "root"

# Default retention periods (days) — must match settings.yaml.example
DEFAULT_PROJECT_RETENTION_DAYS = 30
DEFAULT_DATABASE_RETENTION_DAYS = 14

# Compression and logging constants
ESTIMATED_COMPRESSION_RATIO = 0.7  # tar.gz typically achieves 60-80% compression for code
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB max per log file
LOG_BACKUP_COUNT = 5  # Number of rotated log files to keep
MIN_DB_BACKUP_SPACE_MB = 1024  # 1 GB minimum free space for database backups


class BackupEngine:
    """Main backup orchestrator for projects and databases"""

    def __init__(self, config_manager: ConfigManager, enable_notifications: bool = True):
        self.config = config_manager
        storage_paths = self.config.get_storage_paths()
        local_path = storage_paths.get("local")
        assert local_path is not None, "Local storage path must be configured"
        self.local_path: Path = local_path
        self.sync_path: Path | None = storage_paths.get("sync")
        self.git_manager = GitManager()

        # Set up notifications
        self.notifier = None
        if enable_notifications and NOTIFICATIONS_AVAILABLE and NotificationManagerClass is not None:
            try:
                self.notifier = NotificationManagerClass()
            except Exception as e:
                logging.warning(f"Failed to initialize notifications: {e}")

        # Create storage directories
        self._init_storage()

        # Set up logging
        self.logger = self._setup_logger()

    def _get_timeout(self, name: str) -> int:
        """Get timeout value from config with fallback to module constant."""
        defaults = {
            "mysqldump": MYSQLDUMP_TIMEOUT,
            "mysql_restore": MYSQL_RESTORE_TIMEOUT,
            "git_bundle": GIT_BUNDLE_TIMEOUT,
            "git_clone": GIT_CLONE_TIMEOUT,
            "git_verify": GIT_VERIFY_TIMEOUT,
        }
        return int(self.config.get_setting(f"timeouts.{name}", defaults.get(name, 3600)))

    def _get_mysql_default(self, key: str) -> str | int:
        """Get MySQL connection default from config with fallback to module constant."""
        defaults: dict[str, str | int] = {"host": DEFAULT_MYSQL_HOST, "port": DEFAULT_MYSQL_PORT, "user": DEFAULT_MYSQL_USER}
        value = self.config.get_setting(f"mysql_defaults.{key}")
        if value is not None:
            return value
        if key == "user":
            logging.warning(f"No mysql_defaults.user configured — falling back to '{DEFAULT_MYSQL_USER}'")
        return defaults[key]

    def _init_storage(self) -> None:
        """Initialize storage directories"""
        for path in [self.local_path, self.sync_path]:
            if path is None:
                continue
            path.mkdir(parents=True, exist_ok=True)

            # Create subdirectories
            (path / "projects").mkdir(exist_ok=True)
            (path / "databases").mkdir(exist_ok=True)
            (path / "git").mkdir(exist_ok=True)
            (path / "logs").mkdir(exist_ok=True)

    def _sync_to_secondary(self, local_file: Path, subdirectory: str, backup_name: str) -> None:
        """Sync backup file and its metadata JSON to secondary storage.

        Args:
            local_file: Path to the local backup file
            subdirectory: Relative path under sync root (e.g. 'projects/myapp')
            backup_name: Filename of the backup
        """
        if not (self.sync_path and self.sync_path != self.local_path):
            return

        sync_dir = self.sync_path / subdirectory
        sync_dir.mkdir(parents=True, exist_ok=True)

        # Sync backup file
        if not self._smart_copy(local_file, sync_dir / backup_name):
            self.logger.info(f"Skipped secondary sync for {backup_name} (file unchanged)")

        # Sync companion metadata JSON
        metadata_name = backup_name.replace(".tar.gz", ".json").replace(".sql.gz", ".json").replace(".bundle", ".json")
        metadata_path = local_file.parent / metadata_name
        if metadata_path.exists():
            self._smart_copy(metadata_path, sync_dir / metadata_name)

    def _run_retention_cleanup(
        self,
        local_dir: Path,
        sync_subdirectory: str | None,
        retention_days: int,
        cleanup_fn: Callable[[Path, int], None] | None = None,
    ) -> None:
        """Run retention cleanup on local and optionally sync directories.

        Args:
            local_dir: Local backup directory
            sync_subdirectory: Relative path under sync root (None to skip sync cleanup)
            retention_days: Number of days to retain
            cleanup_fn: Cleanup function to call (defaults to _cleanup_old_backups)
        """
        if cleanup_fn is None:
            cleanup_fn = self._cleanup_old_backups
        cleanup_fn(local_dir, retention_days)

        if sync_subdirectory and self.sync_path and self.sync_path != self.local_path:
            sync_dir = self.sync_path / sync_subdirectory
            if sync_dir.exists():
                cleanup_fn(sync_dir, retention_days)

    def _finalize_backup(
        self,
        local_backup_path: Path,
        backup_name: str,
        item_name: str,
        item_type: str,
        description: str | None,
        sync_subdirectory: str,
        latest_link_name: str,
        retention_days: int,
        extra_metadata: dict[str, Any] | None = None,
        cleanup_fn: Callable[[Path, int], None] | None = None,
    ) -> float:
        """Finalize a backup: permissions, metadata, sync, symlink, retention, notification.

        Args:
            local_backup_path: Path to the created backup file
            backup_name: Filename of the backup
            item_name: Name of the project/database
            item_type: Type of backup (project, database, git, etc.)
            description: Optional backup description
            sync_subdirectory: Relative path under sync root
            latest_link_name: Filename for the 'latest' symlink
            retention_days: Number of days to retain backups
            extra_metadata: Additional metadata to include
            cleanup_fn: Optional custom cleanup function for retention

        Returns:
            Size of the backup in MB
        """
        backup_dir = local_backup_path.parent

        # Set permissions
        os.chmod(local_backup_path, 0o600)

        # Create metadata file with checksum
        self._create_backup_metadata(
            backup_dir,
            backup_name,
            item_name,
            item_type,
            description,
            local_backup_path.stat().st_size,
            local_backup_path,
            extra_metadata,
        )

        # Copy to secondary sync location (with metadata)
        self._sync_to_secondary(local_backup_path, sync_subdirectory, backup_name)

        # Create symlink to latest
        latest_link = backup_dir / latest_link_name
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(local_backup_path.name)

        # Clean old backups (local + sync)
        self._run_retention_cleanup(backup_dir, sync_subdirectory, retention_days, cleanup_fn)

        size_mb = local_backup_path.stat().st_size / (1024 * 1024)
        return size_mb

    def _setup_logger(self) -> logging.Logger:
        """Set up logging with automatic rotation"""
        from logging.handlers import RotatingFileHandler

        logger = logging.getLogger("BackupEngine")
        logger.setLevel(logging.INFO)

        # Rotating file handler (10MB max, keep 5 backup files)
        log_file = self.local_path / "logs" / "backup.log"
        fh = RotatingFileHandler(
            log_file,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(logging.INFO)

        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)

        # Formatter
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)

        if not logger.handlers:
            logger.addHandler(fh)
            logger.addHandler(ch)

        return logger

    def _validate_identifier(self, name: str, identifier_type: str = "name") -> bool:
        """Validate database/project names to prevent injection attacks"""
        # Allow alphanumeric, underscores, hyphens, and dots
        if not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
            raise ValueError(
                f"Invalid {identifier_type}: '{name}'. Only alphanumeric characters, underscores, hyphens, and dots allowed."
            )
        return True

    @staticmethod
    def _validate_backup_filename(filename: str) -> None:
        """Validate backup filename to prevent path traversal.

        Ensures the filename has no directory components (no '..' or '/')
        so it cannot escape the expected backup directory.
        """
        path = Path(filename)
        if path.name != filename or ".." in path.parts:
            raise ValueError(f"Invalid backup filename: '{filename}'. Must be a plain filename with no path components.")

    @staticmethod
    def _validate_restore_target(target_path: Path) -> None:
        """Validate that a restore target path is not a protected system directory."""
        resolved = target_path.resolve()
        protected_prefixes = ("/bin", "/sbin", "/usr", "/etc", "/boot", "/dev", "/proc", "/sys", "/lib", "/lib64")
        for prefix in protected_prefixes:
            if str(resolved) == prefix or str(resolved).startswith(prefix + "/"):
                raise ValueError(f"Restore target '{resolved}' is inside a protected system directory.")

    @staticmethod
    def _safe_extractall(tar: tarfile.TarFile, path: str):
        """Safely extract all members from a tar archive, preventing path traversal.

        Rejects members with absolute paths or '..' components that could write
        files outside the target directory.
        """
        target = Path(path).resolve()
        safe_members = []
        for member in tar.getmembers():
            # Reject absolute paths and '..' components
            if os.path.isabs(member.name) or ".." in Path(member.name).parts:
                raise ValueError(f"Tar member '{member.name}' would extract outside target directory")
            member_path = (target / member.name).resolve()
            if not str(member_path).startswith(str(target) + os.sep) and member_path != target:
                raise ValueError(f"Tar member '{member.name}' would extract outside target directory")
            safe_members.append(member)

        if sys.version_info >= (3, 12):
            tar.extractall(path, members=safe_members, filter="data")  # nosec B202
        else:
            tar.extractall(path, members=safe_members)  # noqa: S202  # nosec B202

    def _create_mysql_config_file(self, db_config: dict[str, Any]) -> str:
        """Create a temporary MySQL configuration file with credentials (secure approach)"""
        # Create temporary file with secure permissions
        fd, temp_path = tempfile.mkstemp(suffix=".cnf", text=True)

        try:
            # Set restrictive permissions (owner read/write only)
            os.chmod(temp_path, 0o600)

            # Write MySQL configuration
            # Password must be quoted to handle special characters like # (comment char)
            password = db_config.get("password", "").replace('"', '\\"')
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

    @staticmethod
    def _compile_exclude_patterns(patterns: list[str]) -> list[re.Pattern]:
        """Convert glob/string exclusion patterns to compiled regexes.

        Args:
            patterns: List of glob or string patterns

        Returns:
            List of compiled regex patterns
        """
        regexes = []
        for pattern in patterns:
            if any(c in pattern for c in ["*", "?", "["]):
                regex_pattern = fnmatch.translate(pattern)
            else:
                regex_pattern = f".*{re.escape(pattern)}.*"
            regexes.append(re.compile(regex_pattern))
        return regexes

    def _has_backup_today(self, backup_dir: Path, name_prefix: str) -> bool:
        """Check if a backup was already created today

        Args:
            backup_dir: Directory to check for backups
            name_prefix: Prefix of backup files (project or database name)

        Returns:
            True if backup exists for today
        """
        if not backup_dir.exists():
            return False

        today = datetime.now().strftime("%Y%m%d")
        pattern = f"{name_prefix}_{today}_*.tar.gz"

        # Also check for .sql.gz (databases) and .bundle (git)
        matches = list(backup_dir.glob(pattern))
        if not matches:
            pattern_sql = f"{name_prefix}_{today}_*.sql.gz"
            matches = list(backup_dir.glob(pattern_sql))
        if not matches:
            pattern_bundle = f"{name_prefix}_{today}_*.bundle"
            matches = list(backup_dir.glob(pattern_bundle))

        return len(matches) > 0

    def backup_project(
        self,
        project_name: str,
        description: str | None = None,
        incremental: bool = False,
        skip_if_exists_today: bool = False,
    ) -> tuple[bool, str]:
        """Backup a single project

        Args:
            project_name: Name of the project to backup
            description: Optional description for the backup
            incremental: Whether to create incremental backup
            skip_if_exists_today: Skip if backup already exists for today
        """
        self._validate_identifier(project_name, "project name")
        project = self.config.get_project(project_name)

        if not project:
            return False, f"Project '{project_name}' not found in configuration"

        if not project.get("backup", {}).get("enabled", True):
            return False, f"Backup disabled for project '{project_name}'"

        project_path = Path(project["path"])

        if not project_path.exists():
            return False, f"Project path does not exist: {project_path}"

        # Check if backup already exists today
        local_backup_dir = self.local_path / "projects" / project_name
        if skip_if_exists_today and self._has_backup_today(local_backup_dir, project_name):
            return True, "Skipped: backup already exists for today"

        # Check disk space before starting backup
        # Merge project-specific excludes with global excludes from settings
        project_excludes = project.get("exclude", [])
        global_excludes = self.config.get_global_excludes()
        exclude_patterns = list(set(project_excludes + global_excludes))
        estimated_size = self._estimate_project_size(project_path, exclude_patterns)

        # Estimate compressed size (tar.gz typically achieves 60-80% compression for code)
        estimated_compressed_size = int(estimated_size * ESTIMATED_COMPRESSION_RATIO)

        local_backup_dir = self.local_path / "projects" / project_name
        space_ok, space_msg = self._check_disk_space(local_backup_dir, estimated_compressed_size)

        if not space_ok:
            self.logger.error(f"Disk space check failed for project '{project_name}': {space_msg}")
            return False, space_msg

        self.logger.debug(f"Disk space check passed: {space_msg}")

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{project_name}_{timestamp}.tar.gz"

            # Local backup path
            local_backup_dir = self.local_path / "projects" / project_name
            local_backup_dir.mkdir(parents=True, exist_ok=True)
            local_backup_path = local_backup_dir / backup_name

            # Get exclusion patterns (already computed above, but refresh in case)
            # This ensures we use the merged project + global excludes

            # Determine if incremental backup is possible
            last_full_backup = None
            snapshot_file = None
            if incremental:
                # Look for the last full backup
                full_backups = sorted(
                    [f for f in local_backup_dir.glob(f"{project_name}_*_full.tar.gz") if not f.is_symlink()],
                    key=lambda x: x.stat().st_mtime,
                    reverse=True,
                )

                if full_backups:
                    last_full_backup = full_backups[0]
                    snapshot_file = local_backup_dir / f".{project_name}_snapshot.json"
                else:
                    # No full backup exists, force full backup
                    incremental = False
                    self.logger.info(f"No full backup found for '{project_name}', performing full backup")

            # Determine backup type and filename
            if incremental and last_full_backup:
                backup_type = "incremental"
                backup_name = f"{project_name}_{timestamp}_incr.tar.gz"
                self.logger.info(f"Starting incremental backup of project '{project_name}'")
            else:
                backup_type = "full"
                backup_name = f"{project_name}_{timestamp}_full.tar.gz"
                self.logger.info(f"Starting full backup of project '{project_name}'")

            # Update local backup path with the correct name
            local_backup_path = local_backup_dir / backup_name

            # Compile exclusion patterns for better performance
            # Add default patterns to always exclude folders starting with _ or .
            default_excludes = [
                "_*/",  # Folders starting with underscore
                ".*/",  # Hidden folders (starting with dot)
            ]
            # Merge defaults with project-specific excludes (avoid duplicates)
            all_exclude_patterns = list(set(default_excludes + exclude_patterns))

            exclude_regexes = self._compile_exclude_patterns(all_exclude_patterns)

            # Load or create snapshot for incremental backup
            file_snapshot = {}
            if incremental and snapshot_file and snapshot_file.exists():
                try:
                    with open(snapshot_file) as f:
                        file_snapshot = json.load(f)
                except (OSError, json.JSONDecodeError):
                    self.logger.warning("Could not load snapshot file, performing full backup")
                    incremental = False
                    backup_type = "full"

            # Create tar archive with exclusions and incremental logic
            with tarfile.open(local_backup_path, "w:gz") as tar:
                new_snapshot = {}
                files_added = 0
                files_skipped = 0

                def filter_func(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
                    nonlocal files_added, files_skipped

                    # Check if any path component starts with _ or . (always exclude)
                    path_parts = tarinfo.name.split("/")
                    for part in path_parts[1:]:  # Skip the root project folder
                        if part.startswith("_") or part.startswith("."):
                            return None

                    # Check if file should be excluded using compiled regexes
                    for regex in exclude_regexes:
                        if regex.match(tarinfo.name):
                            return None

                    # For incremental backup, check if file has changed
                    if incremental and backup_type == "incremental":
                        file_key = tarinfo.name

                        # Always include directories
                        if tarinfo.isdir():
                            return tarinfo

                        # Check if file is new or modified
                        if file_key in file_snapshot:
                            old_mtime = file_snapshot[file_key].get("mtime", 0)
                            old_size = file_snapshot[file_key].get("size", -1)

                            # Compare modification time and size
                            if tarinfo.mtime <= old_mtime and tarinfo.size == old_size:
                                files_skipped += 1
                                # Still update snapshot for unchanged files
                                new_snapshot[file_key] = {
                                    "mtime": tarinfo.mtime,
                                    "size": tarinfo.size,
                                    "mode": tarinfo.mode,
                                }
                                return None  # Skip unchanged file

                    # Add file to backup and snapshot
                    if tarinfo.isfile():
                        files_added += 1
                        new_snapshot[tarinfo.name] = {
                            "mtime": tarinfo.mtime,
                            "size": tarinfo.size,
                            "mode": tarinfo.mode,
                        }

                    return tarinfo

                tar.add(project_path, arcname=project_name, filter=filter_func)

                if incremental:
                    self.logger.info(f"Incremental backup: {files_added} files added, {files_skipped} files unchanged")

            # Save snapshot for next incremental backup
            if backup_type == "full" or (incremental and new_snapshot):
                snapshot_file = local_backup_dir / f".{project_name}_snapshot.json"
                try:
                    # For full backup, build complete snapshot
                    if backup_type == "full":
                        new_snapshot = {}
                        for root, _dirs, files in os.walk(project_path):
                            for file in files:
                                file_path = Path(root) / file
                                rel_path = file_path.relative_to(project_path.parent)

                                # Skip excluded files
                                skip = False
                                for regex in exclude_regexes:
                                    if regex.match(str(rel_path)):
                                        skip = True
                                        break

                                if not skip:
                                    try:
                                        stat = file_path.stat()
                                        new_snapshot[str(rel_path)] = {
                                            "mtime": stat.st_mtime,
                                            "size": stat.st_size,
                                            "mode": stat.st_mode,
                                        }
                                    except (OSError, PermissionError) as stat_err:
                                        self.logger.warning(f"Could not stat {file_path}: {stat_err}")

                    with open(snapshot_file, "w") as f:
                        json.dump(new_snapshot, f, indent=2)
                        self.logger.debug(f"Saved snapshot with {len(new_snapshot)} files")
                except Exception as e:
                    self.logger.warning(f"Failed to save snapshot: {e}")

            # Finalize: permissions, metadata, sync, symlink, retention
            metadata_extra = {
                "backup_type": backup_type,
                "incremental": incremental,
                "files_added": files_added if incremental else None,
                "files_skipped": files_skipped if incremental else None,
                "base_backup": last_full_backup.name if incremental and last_full_backup else None,
            }
            retention_days = project.get("backup", {}).get(
                "retention_days",
                self.config.get_setting("defaults.project.retention_days", DEFAULT_PROJECT_RETENTION_DAYS),
            )
            size_mb = self._finalize_backup(
                local_backup_path, backup_name, project_name, "project", description,
                f"projects/{project_name}", "latest.tar.gz", retention_days, metadata_extra,
            )
            self.logger.info(f"Successfully backed up '{project_name}' ({size_mb:.2f} MB)")

            if self.notifier:
                self.notifier.notify_backup_success(project_name, "project", size_mb)

            return True, f"Backup successful: {backup_name} ({size_mb:.2f} MB)"

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Failed to backup project '{project_name}': {error_msg}")

            # Clean up partial archive on failure
            if "local_backup_path" in locals() and local_backup_path.exists():
                try:
                    local_backup_path.unlink()
                    self.logger.info(f"Cleaned up partial archive: {local_backup_path.name}")
                except OSError as cleanup_err:
                    self.logger.warning(f"Could not remove partial archive {local_backup_path.name}: {cleanup_err}")

            # Send failure notification
            if self.notifier:
                self.notifier.notify_backup_failure(project_name, "project", error_msg)

            return False, f"Backup failed: {error_msg}"

    def backup_project_complete(
        self, project_name: str, description: str | None = None, skip_if_exists_today: bool = False
    ) -> tuple[bool, str]:
        """Create a complete backup of a project including all hidden/config files

        This backup type:
        - Includes .git/, .claude/, _debug/, and all other hidden/underscore folders
        - Only excludes archive files (.zip, .7z, .tar.gz, etc.)
        - Uses a fixed filename (overwrites previous backup)
        - Ideal for preserving all project settings and configs

        Args:
            project_name: Name of the project to backup
            description: Optional description for the backup
            skip_if_exists_today: Skip if backup already exists for today
        """
        self._validate_identifier(project_name, "project name")
        project = self.config.get_project(project_name)

        if not project:
            return False, f"Project '{project_name}' not found in configuration"

        if not project.get("backup", {}).get("enabled", True):
            return False, f"Backup disabled for project '{project_name}'"

        project_path = Path(project["path"])

        if not project_path.exists():
            return False, f"Project path does not exist: {project_path}"

        # Fixed filename for complete backup (no timestamp - overwrites previous)
        backup_name = f"{project_name}_complete.tar.gz"
        local_backup_dir = self.local_path / "projects" / project_name

        # Check if backup already exists today (by checking metadata timestamp)
        if skip_if_exists_today:
            metadata_path = local_backup_dir / f"{project_name}_complete.json"
            if metadata_path.exists():
                try:
                    with open(metadata_path) as f:
                        metadata = json.load(f)
                    last_backup = datetime.fromisoformat(metadata.get("timestamp", ""))
                    if last_backup.date() == datetime.now().date():
                        return True, "Skipped: complete backup already exists for today"
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass  # Proceed with backup if metadata is invalid

        # Archive exclusion patterns only (no hidden/underscore folder exclusions)
        archive_patterns = self.config.get_setting(
            "complete_backup.exclude_archives",
            ["*.zip", "*.7z", "*.tar", "*.tar.gz", "*.tgz", "*.tar.bz2", "*.rar", "*.gz", "*.bz2", "*.xz"],
        )

        # Estimate size (include everything except archives)
        estimated_size = self._estimate_project_size_complete(project_path, archive_patterns)
        estimated_compressed_size = int(estimated_size * ESTIMATED_COMPRESSION_RATIO)

        local_backup_dir.mkdir(parents=True, exist_ok=True)
        space_ok, space_msg = self._check_disk_space(local_backup_dir, estimated_compressed_size)

        if not space_ok:
            self.logger.error(f"Disk space check failed for complete backup '{project_name}': {space_msg}")
            return False, space_msg

        try:
            local_backup_path = local_backup_dir / backup_name
            self.logger.info(f"Starting complete backup of project '{project_name}' (including all configs)")

            # Compile archive exclusion patterns
            exclude_regexes = []
            for pattern in archive_patterns:
                regex_pattern = fnmatch.translate(pattern)
                exclude_regexes.append(re.compile(regex_pattern))

            # Create tar archive - only exclude archives, include everything else
            with tarfile.open(local_backup_path, "w:gz") as tar:

                def filter_func(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
                    # Only exclude archive files - include all folders including hidden ones
                    filename = os.path.basename(tarinfo.name)
                    for regex in exclude_regexes:
                        if regex.match(filename):
                            return None
                    return tarinfo

                tar.add(project_path, arcname=project_name, filter=filter_func)

            # Finalize: permissions, metadata, sync, symlink, retention
            retention_days = project.get("backup", {}).get(
                "retention_days",
                self.config.get_setting("defaults.project.retention_days", DEFAULT_PROJECT_RETENTION_DAYS),
            )
            size_mb = self._finalize_backup(
                local_backup_path, backup_name, project_name, "project_complete",
                description or "Complete backup (all files including configs)",
                f"projects/{project_name}", "latest_complete.tar.gz", retention_days,
                {"backup_type": "complete", "includes_hidden": True, "includes_git": True},
            )
            self.logger.info(f"Successfully created complete backup of '{project_name}' ({size_mb:.2f} MB)")

            if self.notifier:
                self.notifier.notify_backup_success(project_name, "complete", size_mb)

            return True, f"Complete backup successful: {backup_name} ({size_mb:.2f} MB)"

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Failed to create complete backup of '{project_name}': {error_msg}")

            # Clean up partial archive
            if "local_backup_path" in locals() and local_backup_path.exists():
                try:
                    local_backup_path.unlink()
                    self.logger.info(f"Cleaned up partial archive: {local_backup_path.name}")
                except OSError as cleanup_err:
                    self.logger.warning(f"Could not remove partial archive {local_backup_path.name}: {cleanup_err}")

            if self.notifier:
                self.notifier.notify_backup_failure(project_name, "complete", error_msg)

            return False, f"Complete backup failed: {error_msg}"

    def _estimate_project_size_complete(self, project_path: Path, archive_patterns: list[str]) -> int:
        """Estimate project size for complete backup (includes hidden folders)

        Args:
            project_path: Path to project directory
            archive_patterns: List of archive patterns to exclude

        Returns:
            Estimated size in bytes
        """
        total_size = 0

        exclude_regexes = self._compile_exclude_patterns(archive_patterns)

        try:
            for item in project_path.rglob("*"):
                if item.is_file():
                    # Only check if it's an archive file
                    filename = item.name
                    is_archive = any(regex.match(filename) for regex in exclude_regexes)

                    if not is_archive:
                        try:
                            total_size += item.stat().st_size
                        except (PermissionError, OSError):
                            pass

        except Exception as e:
            self.logger.warning(f"Error estimating complete backup size: {e}")

        return total_size

    def backup_all_projects_complete(
        self, parallel: bool = True, skip_if_exists_today: bool = False
    ) -> dict[str, tuple[bool, str]]:
        """Create complete backups for all enabled projects

        Args:
            parallel: If True, run backups in parallel
            skip_if_exists_today: Skip projects that already have a complete backup today
        """
        results = {}
        project_names = list(self.config.get_all_projects())

        if not parallel or len(project_names) <= 1:
            for project_name in project_names:
                results[project_name] = self.backup_project_complete(
                    project_name, skip_if_exists_today=skip_if_exists_today
                )
        else:
            max_workers = self.config.get_setting("system.max_parallel_backups", 4)
            self.logger.info(f"Starting parallel complete backup of {len(project_names)} projects")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_project = {
                    executor.submit(
                        self.backup_project_complete, project_name, None, skip_if_exists_today
                    ): project_name
                    for project_name in project_names
                }

                for future in as_completed(future_to_project):
                    project_name = future_to_project[future]
                    try:
                        results[project_name] = future.result()
                    except Exception as e:
                        self.logger.error(f"Complete backup failed for '{project_name}': {e}")
                        results[project_name] = (False, f"Complete backup failed: {e!s}")

        return results

    def _calculate_file_checksum(self, file_path: Path, algorithm: str = "sha256") -> str:
        """Calculate checksum of a file

        Args:
            file_path: Path to file
            algorithm: Hash algorithm (default: sha256)

        Returns:
            Hexadecimal checksum string
        """
        hash_obj = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            # Read in chunks to handle large files efficiently
            for chunk in iter(lambda: f.read(8192), b""):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()

    def _smart_copy(self, source: Path, destination: Path) -> bool:
        """Copy file only if different (checksum-based)

        Args:
            source: Source file path
            destination: Destination file path

        Returns:
            True if file was copied, False if skipped (identical)
        """
        # If destination doesn't exist, copy
        if not destination.exists():
            shutil.copy2(source, destination)
            self.logger.debug(f"Copied {source.name} to {destination} (new file)")
            return True

        # Quick size check
        source_size = source.stat().st_size
        dest_size = destination.stat().st_size

        if source_size != dest_size:
            shutil.copy2(source, destination)
            self.logger.debug(f"Copied {source.name} to {destination} (size changed)")
            return True

        # Checksum comparison
        source_checksum = self._calculate_file_checksum(source)
        dest_checksum = self._calculate_file_checksum(destination)

        if source_checksum != dest_checksum:
            shutil.copy2(source, destination)
            self.logger.debug(f"Copied {source.name} to {destination} (checksum mismatch)")
            return True

        self.logger.debug(f"Skipped copying {source.name} (identical)")
        return False

    def _check_disk_space(self, path: Path, required_bytes: int, safety_margin: float = 1.2) -> tuple[bool, str]:
        """Check if sufficient disk space is available

        Args:
            path: Path to check disk space for
            required_bytes: Required space in bytes
            safety_margin: Multiply required space by this factor for safety (default: 1.2 = 20% margin)

        Returns:
            Tuple of (success, message)
        """
        try:
            stat = os.statvfs(path)
            available_bytes = stat.f_bavail * stat.f_frsize
            required_with_margin = required_bytes * safety_margin

            if available_bytes < required_with_margin:
                available_gb = available_bytes / (1024**3)
                required_gb = required_with_margin / (1024**3)
                return (
                    False,
                    f"Insufficient disk space: {available_gb:.2f} GB available, {required_gb:.2f} GB required (with {int((safety_margin - 1) * 100)}% safety margin)",
                )

            return True, f"Sufficient disk space available ({available_bytes / (1024**3):.2f} GB)"

        except Exception as e:
            self.logger.warning(f"Could not check disk space: {e}")
            return True, "Disk space check skipped (error occurred)"

    def _estimate_project_size(self, project_path: Path, exclude_patterns: list[str]) -> int:
        """Estimate the size of a project directory

        Args:
            project_path: Path to project directory
            exclude_patterns: List of patterns to exclude

        Returns:
            Estimated size in bytes
        """
        total_size = 0

        exclude_regexes = self._compile_exclude_patterns(exclude_patterns)

        try:
            for item in project_path.rglob("*"):
                # Check if any path component starts with _ or . (always exclude)
                rel_path = item.relative_to(project_path)
                path_parts = rel_path.parts
                should_exclude = any(part.startswith("_") or part.startswith(".") for part in path_parts)

                # Also check compiled regex patterns
                if not should_exclude:
                    item_str = str(item.relative_to(project_path.parent))
                    should_exclude = any(regex.match(item_str) for regex in exclude_regexes)

                if not should_exclude and item.is_file():
                    try:
                        total_size += item.stat().st_size
                    except (PermissionError, OSError):
                        pass  # Skip files we can't read

        except Exception as e:
            self.logger.warning(f"Error estimating project size: {e}")

        return total_size

    def _create_backup_metadata(
        self,
        backup_dir: Path,
        backup_name: str,
        item_name: str,
        item_type: str,
        description: str | None,
        size_bytes: int,
        backup_file_path: Path | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create metadata file for backup with checksum verification"""
        metadata_name = backup_name.replace(".tar.gz", ".json").replace(".sql.gz", ".json").replace(".bundle", ".json")
        metadata_path = backup_dir / metadata_name

        # Calculate checksum - MANDATORY for new backups
        checksum = None
        if backup_file_path and backup_file_path.exists():
            try:
                checksum = self._calculate_file_checksum(backup_file_path)
                self.logger.info(f"Calculated SHA256 checksum for {backup_name}: {checksum[:8]}...")
            except Exception as e:
                # This is now a critical error - we MUST have checksums for data integrity
                self.logger.error(f"CRITICAL: Failed to calculate checksum for {backup_name}: {e}")
                raise RuntimeError(f"Failed to calculate backup checksum: {e}") from e
        else:
            # This should never happen with current code but let's be explicit
            if backup_file_path:
                self.logger.error(f"CRITICAL: Backup file does not exist for checksum calculation: {backup_file_path}")
                raise FileNotFoundError(f"Cannot calculate checksum - backup file not found: {backup_file_path}")

        metadata: dict[str, Any] = {
            "backup_name": backup_name,
            "item_name": item_name,
            "item_type": item_type,
            "description": description,
            "timestamp": datetime.now().isoformat(),
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "checksum_sha256": checksum,
            "created_by": "Quartermaster",
            "version": "1.0",
            # New tagging fields
            "tags": [],  # e.g., ['production', 'stable', 'pre-release']
            "importance": "normal",  # critical/high/normal/low
            "keep_forever": False,  # If True, never auto-delete
            "pinned": False,  # Alternative to keep_forever
        }

        # Add any extra metadata
        if extra_metadata:
            metadata.update(extra_metadata)

        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
            self.logger.info(f"Created metadata file with checksum: {metadata_name}")
        except Exception as e:
            self.logger.error(f"Failed to create metadata file: {e!s}")
            raise RuntimeError(f"Failed to create backup metadata: {e}") from e

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
                raise Exception("mysqldump failed (check logs for details)")

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

    def backup_git(
        self, project_name: str, description: str | None = None, skip_if_exists_today: bool = False
    ) -> tuple[bool, str]:
        """Create a git bundle backup for a project

        Git bundles are portable, single-file archives containing git history.
        They can be cloned from or fetched into existing repositories.

        Args:
            project_name: Name of the project to backup
            description: Optional description for the backup
            skip_if_exists_today: Skip if backup already exists for today
        """
        self._validate_identifier(project_name, "project name")
        project = self.config.get_project(project_name)

        if not project:
            return False, f"Project '{project_name}' not found in configuration"

        project_path = Path(project["path"])

        if not project_path.exists():
            return False, f"Project path does not exist: {project_path}"

        # Check if it's a git repository
        if not self.git_manager.is_git_repo(str(project_path)):
            return False, f"Project '{project_name}' is not a git repository"

        # Check disk space
        local_backup_dir = self.local_path / "git" / project_name
        local_backup_dir.mkdir(parents=True, exist_ok=True)

        # Check if backup already exists today
        if skip_if_exists_today and self._has_backup_today(local_backup_dir, project_name):
            return True, "Skipped: git backup already exists for today"

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{project_name}_{timestamp}.bundle"
            local_backup_path = local_backup_dir / backup_name

            self.logger.info(f"Starting git bundle backup of project '{project_name}'")

            # Get current branch info
            git_status = self.git_manager.get_repo_status(str(project_path))
            current_branch = git_status.get("branch", "main")

            # Create git bundle with all refs
            cmd = ["git", "bundle", "create", str(local_backup_path), "--all"]
            result = subprocess.run(
                cmd, cwd=str(project_path), capture_output=True, text=True, timeout=self._get_timeout("git_bundle")
            )

            if result.returncode != 0:
                self.logger.debug(f"git bundle create stderr: {result.stderr}")
                raise Exception("git bundle create failed (check logs for details)")

            # Get commit count and latest commit info
            commit_count = git_status.get("commit_count", 0)
            latest_commit = git_status.get("commits", [{}])[0] if git_status.get("commits") else {}

            # Finalize: permissions, metadata, sync, symlink, retention
            metadata_extra = {
                "backup_type": "git_bundle",
                "branch": current_branch,
                "commit_count": commit_count,
                "latest_commit_hash": latest_commit.get("hash", "unknown"),
                "latest_commit_message": latest_commit.get("message", "")[:100],
                "latest_commit_date": latest_commit.get("date", ""),
                "has_uncommitted_changes": git_status.get("is_dirty", False),
                "uncommitted_files": git_status.get("total_changes", 0),
            }
            retention_days = project.get("backup", {}).get(
                "retention_days",
                self.config.get_setting("defaults.project.retention_days", DEFAULT_PROJECT_RETENTION_DAYS),
            )
            size_mb = self._finalize_backup(
                local_backup_path, backup_name, project_name, "git",
                description or f"Git bundle backup - {current_branch}",
                f"git/{project_name}", "latest.bundle", retention_days,
                metadata_extra, lambda d, r: self._cleanup_old_backups(d, r, patterns=["*.bundle"]),
            )
            self.logger.info(
                f"Successfully backed up git history for '{project_name}' ({size_mb:.2f} MB, {commit_count} commits)"
            )

            if self.notifier:
                self.notifier.notify_backup_success(project_name, "git", size_mb)

            return True, f"Git backup successful: {backup_name} ({size_mb:.2f} MB, {commit_count} commits)"

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Failed to backup git for '{project_name}': {error_msg}")

            # Clean up partial bundle
            if "local_backup_path" in locals() and local_backup_path.exists():
                try:
                    local_backup_path.unlink()
                    self.logger.info(f"Cleaned up partial bundle: {local_backup_path.name}")
                except OSError:
                    pass

            if self.notifier:
                self.notifier.notify_backup_failure(project_name, "git", error_msg)

            return False, f"Git backup failed: {error_msg}"

    def backup_all_git(self, parallel: bool = True, skip_if_exists_today: bool = False) -> dict[str, tuple[bool, str]]:
        """Backup git history for all projects that are git repositories

        Args:
            parallel: If True, run backups in parallel
            skip_if_exists_today: Skip projects that already have a git backup today
        """
        results = {}

        # Filter to only git-enabled projects
        git_projects = []
        for name, project in self.config.get_all_projects().items():
            if self.git_manager.is_git_repo(project["path"]):
                git_projects.append(name)

        if not git_projects:
            return {"error": (False, "No git repositories found among projects")}

        if not parallel or len(git_projects) <= 1:
            for project_name in git_projects:
                results[project_name] = self.backup_git(project_name, skip_if_exists_today=skip_if_exists_today)
        else:
            max_workers = self.config.get_setting("system.max_parallel_backups", 4)
            self.logger.info(f"Starting parallel git backup of {len(git_projects)} projects")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_project = {
                    executor.submit(self.backup_git, project_name, None, skip_if_exists_today): project_name
                    for project_name in git_projects
                }

                for future in as_completed(future_to_project):
                    project_name = future_to_project[future]
                    try:
                        results[project_name] = future.result()
                    except Exception as e:
                        self.logger.error(f"Git backup failed for '{project_name}': {e}")
                        results[project_name] = (False, f"Git backup failed: {e!s}")

        return results

    def restore_git(
        self, project_name: str, backup_file: str, target_path: str | None = None, mode: str = "clone"
    ) -> tuple[bool, str]:
        """Restore a git repository from a bundle backup

        Args:
            project_name: Name of the project
            backup_file: Bundle filename to restore from
            target_path: Target directory (optional, defaults to original location)
            mode: 'clone' (create new repo) or 'fetch' (update existing repo)
        """
        self._validate_identifier(project_name, "project name")
        self._validate_backup_filename(backup_file)
        backup_path = self.local_path / "git" / project_name / backup_file

        if not backup_path.exists():
            return False, f"Git backup file not found: {backup_file}"

        # Determine target path
        if target_path:
            restore_path = Path(target_path)
            self._validate_restore_target(restore_path)
        else:
            project = self.config.get_project(project_name)
            if not project:
                return False, f"Project '{project_name}' not found and no target path provided"
            restore_path = Path(project["path"])

        try:
            if mode == "clone":
                # Clone from bundle - creates new repository
                if restore_path.exists():
                    # Backup existing directory
                    backup_existing = (
                        restore_path.parent / f"{restore_path.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    )
                    shutil.move(str(restore_path), str(backup_existing))
                    self.logger.info(f"Existing directory moved to: {backup_existing}")

                cmd = ["git", "clone", str(backup_path), str(restore_path)]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=self._get_timeout("git_clone"))

                if result.returncode != 0:
                    self.logger.debug(f"git clone stderr: {result.stderr}")
                    raise Exception("git clone failed (check logs for details)")

                self.logger.info(f"Successfully cloned git backup to {restore_path}")
                return True, f"Git repository restored to {restore_path}"

            elif mode == "fetch":
                # Fetch from bundle into existing repository
                if not restore_path.exists() or not self.git_manager.is_git_repo(str(restore_path)):
                    return False, "Target path is not a git repository. Use mode='clone' instead."

                # Verify the bundle
                verify_cmd = ["git", "bundle", "verify", str(backup_path)]
                verify_result = subprocess.run(
                    verify_cmd,
                    cwd=str(restore_path),
                    capture_output=True,
                    text=True,
                    timeout=self._get_timeout("git_verify"),
                )

                if verify_result.returncode != 0:
                    self.logger.debug(f"git bundle verify stderr: {verify_result.stderr}")
                    return False, "Bundle verification failed (check logs for details)"

                # Fetch from bundle
                fetch_cmd = ["git", "fetch", str(backup_path), "*:*"]
                fetch_result = subprocess.run(
                    fetch_cmd,
                    cwd=str(restore_path),
                    capture_output=True,
                    text=True,
                    timeout=self._get_timeout("git_clone"),
                )

                if fetch_result.returncode != 0:
                    # Try alternative fetch syntax
                    fetch_cmd = ["git", "pull", str(backup_path), "HEAD"]
                    fetch_result = subprocess.run(
                        fetch_cmd,
                        cwd=str(restore_path),
                        capture_output=True,
                        text=True,
                        timeout=self._get_timeout("git_clone"),
                    )

                    if fetch_result.returncode != 0:
                        self.logger.debug(f"git fetch stderr: {fetch_result.stderr}")
                        raise Exception("git fetch failed (check logs for details)")

                self.logger.info(f"Successfully fetched git backup into {restore_path}")
                return True, f"Git history fetched into existing repository at {restore_path}"

            else:
                return False, f"Invalid mode: {mode}. Use 'clone' or 'fetch'."

        except Exception as e:
            self.logger.error(f"Failed to restore git backup: {e!s}")
            return False, f"Git restore failed: {e!s}"

    def backup_all_projects(
        self, parallel: bool = True, skip_if_exists_today: bool = False, incremental: bool = False
    ) -> dict[str, tuple[bool, str]]:
        """Backup all enabled projects (optionally in parallel)

        Args:
            parallel: If True, run backups in parallel using ThreadPoolExecutor
            skip_if_exists_today: Skip projects that already have a backup today
            incremental: Whether to create incremental backups
        """
        results = {}
        project_names = list(self.config.get_all_projects())

        if not parallel or len(project_names) <= 1:
            # Sequential execution
            for project_name in project_names:
                results[project_name] = self.backup_project(
                    project_name, incremental=incremental, skip_if_exists_today=skip_if_exists_today
                )
        else:
            # Parallel execution
            max_workers = self.config.get_setting("system.max_parallel_backups", 4)
            self.logger.info(f"Starting parallel backup of {len(project_names)} projects with {max_workers} workers")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all backup tasks
                future_to_project = {
                    executor.submit(
                        self.backup_project, project_name, None, incremental, skip_if_exists_today
                    ): project_name
                    for project_name in project_names
                }

                # Collect results as they complete
                for future in as_completed(future_to_project):
                    project_name = future_to_project[future]
                    try:
                        results[project_name] = future.result()
                    except Exception as e:
                        self.logger.error(f"Parallel backup failed for '{project_name}': {e}", exc_info=True)
                        results[project_name] = (False, f"Backup failed: {e!s}")

        return results

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

    @staticmethod
    def _backup_name_to_meta_name(backup_filename: str) -> str:
        """Convert a backup filename to its companion metadata filename."""
        for ext in (".tar.gz", ".sql.gz", ".bundle"):
            if backup_filename.endswith(ext):
                return backup_filename[: -len(ext)] + ".json"
        # Fallback: strip last extension
        return backup_filename.rsplit(".", 1)[0] + ".json"

    def _cleanup_old_backups(self, directory: Path, retention_days: int, patterns: list[str] | None = None):
        """Remove backups older than retention period (respecting tags and importance).

        Args:
            directory: Directory containing the backup files
            retention_days: Number of days to retain backups
            patterns: Glob patterns to match backup files (default: tar.gz and sql.gz)
        """
        if not directory.exists():
            return

        cutoff_date = datetime.now() - timedelta(days=retention_days)

        if patterns is None:
            patterns = ["*.tar.gz", "*.sql.gz"]

        for pattern in patterns:
            for backup_file in directory.glob(pattern):
                if backup_file.is_symlink():
                    continue  # Skip symlinks

                # Check if backup should be preserved based on metadata
                metadata_name = self._backup_name_to_meta_name(backup_file.name)
                metadata_path = directory / metadata_name

                should_preserve = False
                preserve_reason = None

                if metadata_path.exists():
                    try:
                        with open(metadata_path) as f:
                            metadata = json.load(f)

                        # Check preservation criteria
                        if metadata.get("keep_forever", False) or metadata.get("pinned", False):
                            should_preserve = True
                            preserve_reason = "pinned/keep_forever"
                        elif metadata.get("importance") in ["critical", "high"]:
                            should_preserve = True
                            preserve_reason = f"importance={metadata.get('importance')}"
                        elif metadata.get("tags"):
                            # Preserve if has important tags
                            configured_tags = self.config.get_setting(
                                "retention.important_tags", ["production", "release", "stable", "live", "deployed"]
                            )
                            important_tags = set(configured_tags)
                            if any(tag in important_tags for tag in metadata.get("tags", [])):
                                should_preserve = True
                                preserve_reason = f"tags={metadata.get('tags')}"
                    except Exception as e:
                        self.logger.warning(f"Could not read metadata for {backup_file.name}: {e}")

                # Skip if backup should be preserved
                if should_preserve:
                    self.logger.debug(f"Preserving {backup_file.name} ({preserve_reason})")
                    continue

                # Remove if older than retention period
                if backup_file.stat().st_mtime < cutoff_date.timestamp():
                    backup_file.unlink()

                    # Also remove metadata file
                    if metadata_path.exists():
                        metadata_path.unlink()

                    self.logger.info(f"Removed old backup: {backup_file.name}")

    def get_backup_status(self, item_type: str, item_name: str) -> dict[str, Any]:
        """Get backup status for a project, database, or git backup"""
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            backup_dir = self.local_path / "databases" / item_name

        if not backup_dir.exists():
            return {"exists": False, "backup_count": 0, "total_size": 0, "latest_backup": None}

        # Different file patterns for different types
        if item_type == "git":
            backups = list(backup_dir.glob("*.bundle"))
        else:
            backups = list(backup_dir.glob("*.tar.gz")) + list(backup_dir.glob("*.sql.gz"))
        backups = [b for b in backups if not b.is_symlink()]

        if not backups:
            return {"exists": True, "backup_count": 0, "total_size": 0, "latest_backup": None}

        backups.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        latest = backups[0]

        total_size = sum(b.stat().st_size for b in backups)

        return {
            "exists": True,
            "backup_count": len(backups),
            "total_size": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "latest_backup": {
                "name": latest.name,
                "size": latest.stat().st_size,
                "size_mb": latest.stat().st_size / (1024 * 1024),
                "modified": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
            },
            "all_backups": [
                {
                    "name": b.name,
                    "size_mb": b.stat().st_size / (1024 * 1024),
                    "modified": datetime.fromtimestamp(b.stat().st_mtime).isoformat(),
                }
                for b in backups[:10]  # Last 10 backups
            ],
        }

    def restore_project(self, project_name: str, backup_file: str, target_path: str | None = None) -> tuple[bool, str]:
        """Restore a project from backup"""
        self._validate_identifier(project_name, "project name")
        self._validate_backup_filename(backup_file)
        backup_path = self.local_path / "projects" / project_name / backup_file

        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        project = self.config.get_project(project_name)
        if not project and not target_path:
            return False, f"Project '{project_name}' not found in configuration and no target path provided"

        if target_path:
            restore_path = Path(target_path)
            self._validate_restore_target(restore_path)
        else:
            assert project is not None
            restore_path = Path(project["path"])

        try:
            # Verify backup integrity before restoring
            verify_ok, verify_msg = self.verify_backup("project", project_name, backup_file)
            if not verify_ok:
                self.logger.warning(f"Backup verification failed: {verify_msg}")
                return False, f"Restore aborted - backup verification failed: {verify_msg}"

            # Create restore directory if it doesn't exist
            restore_path.parent.mkdir(parents=True, exist_ok=True)

            # If directory exists, rename it as backup
            if restore_path.exists():
                backup_existing = (
                    restore_path.parent / f"{restore_path.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )
                shutil.move(str(restore_path), str(backup_existing))
                self.logger.info(f"Existing directory moved to: {backup_existing}")

            # Extract backup (with path traversal protection)
            with tarfile.open(backup_path, "r:gz") as tar:
                self._safe_extractall(tar, str(restore_path.parent))

            self.logger.info(f"Successfully restored '{project_name}' from {backup_file}")
            return True, f"Project restored successfully to {restore_path}"

        except Exception as e:
            self.logger.error(f"Failed to restore project '{project_name}': {e!s}")
            return False, f"Restore failed: {e!s}"

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
                raise Exception("mysql restore failed (check logs for details)")

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

    def quick_snapshot(
        self, project_name: str, message: str | None = None, backup_databases: bool = True
    ) -> dict[str, Any]:
        """Create a complete snapshot: Git commit + Project backup + Database backups (if configured)

        This is a convenient command for daily workflow that:
        1. Creates a Git savepoint (if project is a Git repo)
        2. Backs up the project
        3. Backs up associated databases (if any configured for this project)

        Args:
            project_name: Name of the project to snapshot
            message: Commit/backup message
            backup_databases: Whether to backup associated databases (default: True)

        Returns:
            Dictionary with results for each operation
        """
        results: dict[str, Any] = {}

        # Get project configuration
        project = self.config.get_project(project_name)
        if not project:
            return {"error": (False, f"Project '{project_name}' not found in configuration")}

        project_path = Path(project["path"])

        # Step 1: Git savepoint (if it's a Git repo)
        if self.git_manager.is_git_repo(str(project_path)):
            git_msg = message or f"Snapshot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            git_success, git_result = self.git_manager.create_savepoint(str(project_path), git_msg)
            results["git_savepoint"] = (git_success, git_result)
            self.logger.info(f"Git savepoint: {git_result}")
        else:
            results["git_savepoint"] = (False, "Not a Git repository (skipped)")
            self.logger.info(f"Project '{project_name}' is not a Git repository, skipping Git savepoint")

        # Step 2: Project backup
        backup_description = message or f"Quick snapshot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        backup_success, backup_result = self.backup_project(project_name, backup_description)
        results["project_backup"] = (backup_success, backup_result)
        self.logger.info(f"Project backup: {backup_result}")

        # Step 3: Database backups (if configured and requested)
        if backup_databases:
            # Check if project has associated databases configured
            associated_dbs = project.get("databases", [])

            if associated_dbs:
                db_results = {}
                for db_name in associated_dbs:
                    db_success, db_result = self.backup_database(db_name, backup_description)
                    db_results[db_name] = (db_success, db_result)
                    self.logger.info(f"Database '{db_name}' backup: {db_result}")

                results["database_backups"] = db_results
            else:
                results["database_backups"] = (False, "No databases configured for this project")
                self.logger.info(f"No databases configured for project '{project_name}'")

        # Create summary
        success_count = sum(1 for k, v in results.items() if k != "database_backups" and isinstance(v, tuple) and v[0])
        if "database_backups" in results and isinstance(results["database_backups"], dict):
            success_count += sum(1 for v in results["database_backups"].values() if v[0])

        total_operations = len([k for k in results if k != "database_backups" and isinstance(results[k], tuple)])
        if "database_backups" in results and isinstance(results["database_backups"], dict):
            total_operations += len(results["database_backups"])

        results["summary"] = (True, f"Snapshot complete: {success_count}/{total_operations} operations successful")

        # Send snapshot notification
        if self.notifier:
            self.notifier.notify_snapshot_complete(project_name, success_count, total_operations)

        return results

    def verify_backup(self, item_type: str, item_name: str, backup_file: str) -> tuple[bool, str]:
        """Verify backup integrity by comparing checksums

        Args:
            item_type: 'project', 'database', or 'git'
            item_name: Name of the project or database
            backup_file: Backup filename to verify

        Returns:
            Tuple of (success, message)
        """
        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            return False, f"Invalid item type: {item_type}"

        backup_path = backup_dir / backup_file
        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        # Load metadata
        metadata_name = self._backup_name_to_meta_name(backup_file)
        metadata_path = backup_dir / metadata_name

        if not metadata_path.exists():
            return False, f"Metadata file not found: {metadata_name}"

        try:
            with open(metadata_path) as f:
                metadata = json.load(f)

            stored_checksum = metadata.get("checksum_sha256")
            if not stored_checksum:
                return False, "No checksum found in metadata (backup created before verification feature)"

            # Calculate current checksum
            self.logger.info(f"Verifying backup: {backup_file}")
            current_checksum = self._calculate_file_checksum(backup_path)

            if current_checksum == stored_checksum:
                self.logger.info(f"Verification successful for {backup_file}")
                return True, "✓ Backup verified successfully (checksum matches)"
            else:
                self.logger.error(f"Verification failed for {backup_file}: checksum mismatch")
                return (
                    False,
                    f"✗ Backup corrupted! Checksum mismatch.\nExpected: {stored_checksum}\nActual: {current_checksum}",
                )

        except Exception as e:
            self.logger.error(f"Failed to verify backup {backup_file}: {e!s}", exc_info=True)
            return False, f"Verification failed: {e!s}"

    def tag_backup(
        self,
        item_type: str,
        item_name: str,
        backup_file: str,
        tags: list[str] | None = None,
        importance: str | None = None,
        keep_forever: bool | None = None,
        description: str | None = None,
    ) -> tuple[bool, str]:
        """Tag a backup with metadata for preservation and organization

        Args:
            item_type: 'project', 'database', or 'git'
            item_name: Name of the project or database
            backup_file: Backup filename to tag
            tags: List of tags to add (e.g., ['production', 'stable'])
            importance: Importance level ('critical', 'high', 'normal', 'low')
            keep_forever: If True, backup will never be auto-deleted
            description: Update or add description

        Returns:
            Tuple of (success, message)
        """
        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            return False, f"Invalid item type: {item_type}"

        backup_path = backup_dir / backup_file
        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        # Load or create metadata
        metadata_name = self._backup_name_to_meta_name(backup_file)
        metadata_path = backup_dir / metadata_name

        if metadata_path.exists():
            try:
                with open(metadata_path) as f:
                    metadata = json.load(f)
            except Exception as e:
                return False, f"Failed to read metadata: {e}"
        else:
            # Create basic metadata if it doesn't exist
            file_stats = backup_path.stat()
            metadata = {
                "backup_name": backup_file,
                "item_name": item_name,
                "item_type": item_type,
                "timestamp": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                "size_bytes": file_stats.st_size,
                "size_mb": round(file_stats.st_size / (1024 * 1024), 2),
                "created_by": "Manual Tag Operation",
                "version": "1.0",
            }

        # Update metadata with new values
        if tags is not None:
            existing_tags = set(metadata.get("tags", []))
            existing_tags.update(tags)
            metadata["tags"] = sorted(existing_tags)

        if importance is not None:
            if importance not in ["critical", "high", "normal", "low"]:
                return False, f"Invalid importance level: {importance}"
            metadata["importance"] = importance

        if keep_forever is not None:
            metadata["keep_forever"] = keep_forever
            metadata["pinned"] = keep_forever  # Set both for compatibility

        if description is not None:
            metadata["description"] = description

        # Add tagging metadata
        metadata["last_modified"] = datetime.now().isoformat()
        metadata["last_modified_by"] = "Tag Operation"

        # Save updated metadata
        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)

            tag_summary = []
            if tags:
                tag_summary.append(f"tags={metadata['tags']}")
            if importance:
                tag_summary.append(f"importance={importance}")
            if keep_forever:
                tag_summary.append("pinned")
            if description:
                tag_summary.append("description updated")

            self.logger.info(f"Tagged {backup_file}: {', '.join(tag_summary)}")
            return True, f"Successfully tagged {backup_file}: {', '.join(tag_summary)}"

        except Exception as e:
            self.logger.error(f"Failed to save metadata for {backup_file}: {e}")
            return False, f"Failed to tag backup: {e}"

    def list_tagged_backups(self, item_type: str | None = None, item_name: str | None = None) -> list[dict[str, Any]]:
        """List all tagged backups

        Args:
            item_type: Filter by 'project', 'database', or 'git' (optional)
            item_name: Filter by specific project/database name (optional)

        Returns:
            List of tagged backup metadata
        """
        tagged_backups = []

        # Determine directories to search
        search_dirs = []
        if item_type in ["project", None]:
            projects_dir = self.local_path / "projects"
            if item_name:
                specific_dir = projects_dir / item_name
                if specific_dir.exists():
                    search_dirs.append(("project", item_name, specific_dir))
            else:
                for project_dir in projects_dir.glob("*/"):
                    if project_dir.is_dir():
                        search_dirs.append(("project", project_dir.name, project_dir))

        if item_type in ["database", None]:
            databases_dir = self.local_path / "databases"
            if item_name:
                specific_dir = databases_dir / item_name
                if specific_dir.exists():
                    search_dirs.append(("database", item_name, specific_dir))
            else:
                for db_dir in databases_dir.glob("*/"):
                    if db_dir.is_dir():
                        search_dirs.append(("database", db_dir.name, db_dir))

        if item_type in ["git", None]:
            git_dir = self.local_path / "git"
            if item_name:
                specific_dir = git_dir / item_name
                if specific_dir.exists():
                    search_dirs.append(("git", item_name, specific_dir))
            else:
                for repo_dir in git_dir.glob("*/"):
                    if repo_dir.is_dir():
                        search_dirs.append(("git", repo_dir.name, repo_dir))

        # Search for tagged backups
        for type_name, name, directory in search_dirs:
            for metadata_file in directory.glob("*.json"):
                try:
                    with open(metadata_file) as f:
                        metadata = json.load(f)

                    # Check if backup is tagged
                    is_tagged = (
                        metadata.get("tags")
                        or metadata.get("keep_forever", False)
                        or metadata.get("pinned", False)
                        or metadata.get("importance") not in [None, "normal"]
                    )

                    if is_tagged:
                        metadata["item_type"] = type_name
                        metadata["item_name"] = name
                        tagged_backups.append(metadata)

                except Exception as e:
                    self.logger.warning(f"Could not read metadata file {metadata_file}: {e}")

        # Sort by timestamp (newest first)
        tagged_backups.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return tagged_backups

    def backfill_checksums(self, item_type: str, item_name: str) -> tuple[int, int]:
        """Add checksums to old backups that don't have them

        Args:
            item_type: 'project' or 'database'
            item_name: Name of the project or database

        Returns:
            Tuple of (updated_count, total_count)
        """
        updated = 0
        total = 0

        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
            pattern = "*.tar.gz"
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
            pattern = "*.sql.gz"
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
            pattern = "*.bundle"
        else:
            return 0, 0

        if not backup_dir.exists():
            return 0, 0

        # Find all backup files
        backup_files = [f for f in backup_dir.glob(pattern) if not f.is_symlink()]
        total = len(backup_files)

        for backup_file in backup_files:
            # Check if metadata exists
            metadata_name = backup_file.name.replace(".tar.gz", ".json").replace(".sql.gz", ".json")
            metadata_path = backup_dir / metadata_name

            if metadata_path.exists():
                try:
                    with open(metadata_path) as f:
                        metadata = json.load(f)

                    # Check if checksum is missing or None
                    if not metadata.get("checksum_sha256"):
                        self.logger.info(f"Calculating checksum for {backup_file.name}...")

                        # Calculate checksum
                        checksum = self._calculate_file_checksum(backup_file)

                        # Update metadata
                        metadata["checksum_sha256"] = checksum
                        metadata["checksum_added"] = datetime.now().isoformat()
                        metadata["checksum_added_by"] = "Backfill Operation"

                        # Save updated metadata
                        with open(metadata_path, "w") as f:
                            json.dump(metadata, f, indent=2)

                        self.logger.info(f"Added checksum to {metadata_name}: {checksum[:8]}...")
                        updated += 1

                except Exception as e:
                    self.logger.error(f"Failed to update metadata for {backup_file.name}: {e}")
            else:
                # Create metadata if it doesn't exist
                try:
                    self.logger.info(f"Creating metadata for {backup_file.name}...")

                    # Calculate checksum
                    checksum = self._calculate_file_checksum(backup_file)
                    file_stats = backup_file.stat()

                    # Create new metadata
                    metadata = {
                        "backup_name": backup_file.name,
                        "item_name": item_name,
                        "item_type": item_type,
                        "description": None,
                        "timestamp": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                        "size_bytes": file_stats.st_size,
                        "size_mb": round(file_stats.st_size / (1024 * 1024), 2),
                        "checksum_sha256": checksum,
                        "created_by": "Backfill Operation",
                        "version": "1.0",
                    }

                    # Save metadata
                    with open(metadata_path, "w") as f:
                        json.dump(metadata, f, indent=2)

                    self.logger.info(f"Created metadata for {backup_file.name} with checksum: {checksum[:8]}...")
                    updated += 1

                except Exception as e:
                    self.logger.error(f"Failed to create metadata for {backup_file.name}: {e}")

        return updated, total

    def list_backup_contents(
        self, item_type: str, item_name: str, backup_file: str, pattern: str | None = None
    ) -> list[dict[str, Any]]:
        """List contents of a backup archive

        Args:
            item_type: 'project' or 'database'
            item_name: Name of the project or database
            backup_file: Backup filename to list
            pattern: Optional pattern to filter files (e.g., '*.py', 'src/*')

        Returns:
            List of file information dictionaries
        """
        # Get backup path
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            # Database backups are single SQL files, not much to list
            return []
        else:
            return []

        backup_path = backup_dir / backup_file
        if not backup_path.exists():
            self.logger.error(f"Backup file not found: {backup_file}")
            return []

        files = []
        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                for member in tar.getmembers():
                    # Apply pattern filter if provided
                    if pattern and not fnmatch.fnmatch(member.name, pattern):
                        continue

                    files.append(
                        {
                            "name": member.name,
                            "type": "dir" if member.isdir() else "file",
                            "size": member.size,
                            "mode": oct(member.mode),
                            "mtime": datetime.fromtimestamp(member.mtime).isoformat(),
                            "uid": member.uid,
                            "gid": member.gid,
                        }
                    )

        except Exception as e:
            self.logger.error(f"Failed to list backup contents: {e}")

        return files

    def selective_restore(
        self,
        item_type: str,
        item_name: str,
        backup_file: str,
        files_to_restore: list[str],
        target_path: str | None = None,
        preserve_structure: bool = True,
    ) -> tuple[bool, str]:
        """Restore specific files from a backup

        Args:
            item_type: 'project' or 'database'
            item_name: Name of the project
            backup_file: Backup filename to restore from
            files_to_restore: List of file paths to restore
            target_path: Target directory (optional, defaults to original location)
            preserve_structure: Keep directory structure (True) or flatten (False)

        Returns:
            Tuple of (success, message)
        """
        if item_type != "project":
            return False, "Selective restore only supported for projects"

        self._validate_backup_filename(backup_file)

        # Get backup path
        backup_dir = self.local_path / "projects" / item_name
        backup_path = backup_dir / backup_file

        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        # Determine target directory
        if target_path:
            restore_dir = Path(target_path)
            self._validate_restore_target(restore_dir)
        else:
            project = self.config.get_project(item_name)
            if not project:
                return False, f"Project '{item_name}' not found and no target path specified"
            restore_dir = Path(project["path"])

        # Check if this is an incremental backup
        is_incremental = "_incr.tar.gz" in backup_file
        restored_files = []

        try:
            # If incremental, we need to check the full backup chain
            files_found = set()
            backups_to_check = [backup_path]

            if is_incremental:
                # Load metadata to find base backup
                metadata_name = backup_file.replace(".tar.gz", ".json")
                metadata_path = backup_dir / metadata_name

                if metadata_path.exists():
                    with open(metadata_path) as f:
                        metadata = json.load(f)

                    base_backup = metadata.get("base_backup")
                    if base_backup:
                        base_path = backup_dir / base_backup
                        if base_path.exists():
                            # Check incremental first, then base
                            backups_to_check = [backup_path, base_path]

            # Process backups (incremental first if applicable)
            for backup in backups_to_check:
                with tarfile.open(backup, "r:gz") as tar:
                    for file_pattern in files_to_restore:
                        # Find matching members
                        for member in tar.getmembers():
                            if fnmatch.fnmatch(member.name, file_pattern):
                                # Skip if already restored from incremental
                                if member.name in files_found:
                                    continue

                                files_found.add(member.name)

                                # Determine extraction path
                                if preserve_structure:
                                    extract_path = restore_dir
                                else:
                                    # Flatten structure - extract to target dir directly
                                    member.name = Path(member.name).name
                                    extract_path = restore_dir

                                # Validate extraction path (prevent path traversal)
                                resolved = (Path(extract_path) / member.name).resolve()
                                if not str(resolved).startswith(str(Path(extract_path).resolve()) + os.sep):
                                    self.logger.warning(f"Skipping unsafe tar member: {member.name}")
                                    continue
                                # Skip symlinks and hardlinks
                                if member.issym() or member.islnk():
                                    self.logger.warning(f"Skipping symlink/hardlink: {member.name}")
                                    continue
                                if sys.version_info >= (3, 12):
                                    tar.extract(member, extract_path, filter="data")
                                else:
                                    tar.extract(member, extract_path)
                                restored_files.append(member.name)
                                self.logger.info(f"Restored: {member.name}")

            if not restored_files:
                return False, f"No files matching patterns: {files_to_restore}"

            self.logger.info(f"Selectively restored {len(restored_files)} files from {backup_file}")
            return True, f"Restored {len(restored_files)} files successfully"

        except Exception as e:
            self.logger.error(f"Failed to restore files: {e}", exc_info=True)
            return False, f"Restore failed: {e!s}"

    def preview_file(
        self, item_type: str, item_name: str, backup_file: str, file_path: str, max_lines: int = 100
    ) -> tuple[bool, str]:
        """Preview a specific file from backup without extracting

        Args:
            item_type: 'project' only
            item_name: Name of the project
            backup_file: Backup filename
            file_path: Path of file to preview within backup
            max_lines: Maximum lines to return (default 100)

        Returns:
            Tuple of (success, content or error message)
        """
        if item_type != "project":
            return False, "Preview only supported for project backups"

        # Get backup path
        backup_dir = self.local_path / "projects" / item_name
        backup_path = backup_dir / backup_file

        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                # Find the file
                member = None
                for m in tar.getmembers():
                    if m.name == file_path:
                        member = m
                        break

                if not member:
                    return False, f"File not found in backup: {file_path}"

                if member.isdir():
                    return False, f"Cannot preview directory: {file_path}"

                # Extract and read file content
                file_obj = tar.extractfile(member)
                if not file_obj:
                    return False, f"Could not extract file: {file_path}"

                # Try to decode as text
                try:
                    content = file_obj.read().decode("utf-8")
                    lines = content.split("\n")

                    if len(lines) > max_lines:
                        preview = "\n".join(lines[:max_lines])
                        preview += f"\n\n... ({len(lines) - max_lines} more lines) ..."
                    else:
                        preview = content

                    return True, preview

                except UnicodeDecodeError:
                    return False, f"File appears to be binary: {file_path}"

        except Exception as e:
            self.logger.error(f"Failed to preview file: {e}")
            return False, f"Preview failed: {e!s}"

    def verify_all_backups(self, item_type: str, item_name: str) -> dict[str, tuple[bool, str]]:
        """Verify all backups for a project, database, or git repo

        Args:
            item_type: 'project', 'database', or 'git'
            item_name: Name of the project or database

        Returns:
            Dictionary mapping backup filenames to (success, message) tuples
        """
        results = {}

        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            return {"error": (False, f"Invalid item type: {item_type}")}

        if not backup_dir.exists():
            return {"error": (False, f"No backups found for {item_name}")}

        # Find all backup files
        backup_files = []
        if item_type == "project":
            backup_files = list(backup_dir.glob("*.tar.gz"))
        elif item_type == "database":
            backup_files = list(backup_dir.glob("*.sql.gz"))
        elif item_type == "git":
            backup_files = list(backup_dir.glob("*.bundle"))

        backup_files = [b for b in backup_files if not b.is_symlink()]

        if not backup_files:
            return {"error": (False, f"No backups found for {item_name}")}

        self.logger.info(f"Verifying {len(backup_files)} backups for {item_name}...")

        for backup_file in backup_files:
            success, message = self.verify_backup(item_type, item_name, backup_file.name)
            results[backup_file.name] = (success, message)

        return results
