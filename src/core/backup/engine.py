"""Core Backup Engine for Quartermaster — main class composing all mixins."""

import fnmatch
import logging
import os
import re
import sys
import tarfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.retention_manager import RetentionManager
from ..config_manager import ConfigManager
from ..git_manager import GitManager
from .constants import (
    DEFAULT_MYSQL_HOST,
    DEFAULT_MYSQL_PORT,
    DEFAULT_MYSQL_USER,
    DEFAULT_PROJECT_RETENTION_DAYS,
    GIT_BUNDLE_TIMEOUT,
    GIT_CLONE_TIMEOUT,
    GIT_VERIFY_TIMEOUT,
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    MYSQLDUMP_TIMEOUT,
    MYSQL_RESTORE_TIMEOUT,
    WHITELISTED_DOTFILES,
)
from .database_ops import DatabaseBackupMixin
from .git_ops import GitBackupMixin
from .metadata import MetadataMixin
from .project_ops import ProjectBackupMixin
from .retention import RetentionMixin
from .sync import SyncMixin

# Try to import notifications, but don't fail if not available
try:
    from ...utils.notifications import NotificationManager as _NotificationManager

    NotificationManagerClass: type | None = _NotificationManager
    NOTIFICATIONS_AVAILABLE = True
except (ImportError, ValueError):
    NOTIFICATIONS_AVAILABLE = False
    NotificationManagerClass = None


class BackupEngine(
    ProjectBackupMixin,
    DatabaseBackupMixin,
    GitBackupMixin,
    SyncMixin,
    MetadataMixin,
    RetentionMixin,
):
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
                logging.warning("Failed to initialize notifications: %s", e)

        # Set up tiered retention manager
        self.retention_manager = RetentionManager(self.local_path, config=config_manager)

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
            logging.warning("No mysql_defaults.user configured — falling back to '%s'", DEFAULT_MYSQL_USER)
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

    def _run_retention_cleanup(
        self,
        local_dir: Path,
        sync_subdirectory: str | None,
        retention_days: int,
        cleanup_fn: Callable[[Path, int], None] | None = None,
    ) -> None:
        """Run retention cleanup on local and optionally sync directories.

        Uses tiered retention (hourly/daily/weekly/monthly/yearly) for project
        and database backups. Falls back to simple age-based cleanup for other
        backup types (e.g. git bundles) or when a custom cleanup_fn is provided.

        Args:
            local_dir: Local backup directory
            sync_subdirectory: Relative path under sync root (None to skip sync cleanup)
            retention_days: Number of days to retain (used only for fallback)
            cleanup_fn: Custom cleanup function (bypasses tiered retention)
        """
        # Custom cleanup functions (e.g. git bundles) use the old age-based method
        if cleanup_fn is not None:
            cleanup_fn(local_dir, retention_days)
            if sync_subdirectory and self.sync_path and self.sync_path != self.local_path:
                sync_dir = self.sync_path / sync_subdirectory
                if sync_dir.exists():
                    cleanup_fn(sync_dir, retention_days)
            return

        # Determine item type and name from directory structure
        # Expected paths: .../projects/<name> or .../databases/<name>
        item_name = local_dir.name
        parent_name = local_dir.parent.name
        if parent_name in ("projects", "databases"):
            item_type = "project" if parent_name == "projects" else "database"
        else:
            # Unknown directory structure, fall back to age-based
            self._cleanup_old_backups(local_dir, retention_days)
            return

        # Apply tiered retention on local storage
        try:
            report = self.retention_manager.apply_tiered_retention(
                item_type, item_name, dry_run=False
            )
            deleted_count = len(report.get("deleted", []))
            if deleted_count > 0:
                self.logger.info(
                    "Tiered retention for %s '%s': deleted %d backups",
                    item_type, item_name, deleted_count
                )
        except Exception as e:
            self.logger.warning("Tiered retention failed for '%s', falling back to age-based: %s", item_name, e)
            self._cleanup_old_backups(local_dir, retention_days)

        # Clean sync directory with age-based retention (tiered manager only knows local)
        if sync_subdirectory and self.sync_path and self.sync_path != self.local_path:
            sync_dir = self.sync_path / sync_subdirectory
            if sync_dir.exists():
                sync_retention = RetentionManager(self.sync_path, config=self.config)
                try:
                    sync_retention.apply_tiered_retention(
                        item_type, item_name, dry_run=False
                    )
                except Exception as e:
                    self.logger.warning("Tiered retention failed on sync for '%s': %s", item_name, e)
                    self._cleanup_old_backups(sync_dir, retention_days)

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

        # Create symlink to latest (atomic swap via temp link + rename)
        latest_link = backup_dir / latest_link_name
        tmp_link = backup_dir / f".{latest_link_name}.tmp"
        try:
            try:
                tmp_link.unlink()
            except FileNotFoundError:
                pass
            tmp_link.symlink_to(local_backup_path.name)
            os.replace(tmp_link, latest_link)
        except OSError:
            # Fallback for filesystems that don't support atomic replace on symlinks
            try:
                latest_link.unlink()
            except FileNotFoundError:
                pass
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
                if item.is_symlink():
                    continue
                # Check if any path component starts with _ or . (always exclude)
                # but allow whitelisted dotfiles (e.g. .env, .htaccess)
                rel_path = item.relative_to(project_path)
                path_parts = rel_path.parts
                should_exclude = any(
                    (part.startswith("_") or part.startswith(".")) and part not in WHITELISTED_DOTFILES
                    for part in path_parts
                )

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

    @staticmethod
    def _backup_name_to_meta_name(backup_filename: str) -> str:
        """Convert a backup filename to its companion metadata filename."""
        for ext in (".tar.gz", ".sql.gz", ".bundle"):
            if backup_filename.endswith(ext):
                return backup_filename[: -len(ext)] + ".json"
        # Fallback: strip last extension
        return backup_filename.rsplit(".", 1)[0] + ".json"
