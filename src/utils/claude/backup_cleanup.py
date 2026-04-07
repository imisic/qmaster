"""
Backup cleanup manager — manages cleanup of old backup files on local and sync storage.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any


def _is_broken_symlink(path: Path) -> bool:
    """Check if path is a symlink whose target no longer exists."""
    return path.is_symlink() and not path.exists()


class BackupCleanupManager:
    """Manages cleanup of old backup files on local and sync storage locations"""

    def __init__(self, local_path: Path | None = None, sync_path: Path | None = None):
        self.local_path = local_path or Path.home() / "backups" / "qm"
        self.sync_path = sync_path
        self.backup_base_path = self.local_path
        self.logger = logging.getLogger("BackupCleanupManager")

    def _get_path(self, location: str) -> Path | None:
        """Get the path for a given location (returns None if sync not configured)"""
        if location == "sync":
            return self.sync_path
        return self.local_path

    def get_backup_stats(self, location: str = "local") -> dict[str, Any]:
        """
        Get statistics about all backups for a specific location

        Args:
            location: 'local' or 'sync'
        """
        backup_path = self._get_path(location)

        stats: dict[str, Any] = {
            "exists": False,
            "location": location,
            "path": str(backup_path),
            "total_size_mb": 0,
            "projects": {"count": 0, "size_mb": 0, "files": 0},
            "databases": {"count": 0, "size_mb": 0, "files": 0},
            "old_30d": {"size_mb": 0, "files": 0},
            "old_60d": {"size_mb": 0, "files": 0},
            "old_90d": {"size_mb": 0, "files": 0},
        }

        if backup_path is None or not backup_path.exists():
            return stats

        stats["exists"] = True
        now = datetime.now().timestamp()

        # Check projects
        projects_dir = backup_path / "projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if project_dir.is_dir():
                    stats["projects"]["count"] += 1
                    for backup_file in project_dir.glob("*.tar.gz"):
                        if _is_broken_symlink(backup_file):
                            continue
                        stat = backup_file.stat()
                        size = stat.st_size
                        mtime = stat.st_mtime
                        age_days = (now - mtime) / 86400

                        stats["projects"]["size_mb"] += size / (1024 * 1024)
                        stats["projects"]["files"] += 1
                        stats["total_size_mb"] += size / (1024 * 1024)

                        if age_days > 30:
                            stats["old_30d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_30d"]["files"] += 1
                        if age_days > 60:
                            stats["old_60d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_60d"]["files"] += 1
                        if age_days > 90:
                            stats["old_90d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_90d"]["files"] += 1

        # Check databases
        databases_dir = backup_path / "databases"
        if databases_dir.exists():
            for db_dir in databases_dir.iterdir():
                if db_dir.is_dir():
                    stats["databases"]["count"] += 1
                    for backup_file in db_dir.glob("*.sql.gz"):
                        if _is_broken_symlink(backup_file):
                            continue
                        stat = backup_file.stat()
                        size = stat.st_size
                        mtime = stat.st_mtime
                        age_days = (now - mtime) / 86400

                        stats["databases"]["size_mb"] += size / (1024 * 1024)
                        stats["databases"]["files"] += 1
                        stats["total_size_mb"] += size / (1024 * 1024)

                        if age_days > 30:
                            stats["old_30d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_30d"]["files"] += 1
                        if age_days > 60:
                            stats["old_60d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_60d"]["files"] += 1
                        if age_days > 90:
                            stats["old_90d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_90d"]["files"] += 1

        # Round all values
        for key in ["total_size_mb"]:
            stats[key] = round(stats[key], 2)
        for section in ["projects", "databases", "old_30d", "old_60d", "old_90d"]:
            if "size_mb" in stats[section]:
                stats[section]["size_mb"] = round(stats[section]["size_mb"], 2)

        return stats

    def clean_old_backups(
        self, max_age_days: int, backup_type: str = "all", keep_minimum: int = 15, location: str = "local"
    ) -> tuple[bool, str, dict]:
        """
        Clean backups older than specified days

        Args:
            max_age_days: Delete backups older than this many days
            backup_type: 'projects', 'databases', or 'all'
            keep_minimum: Always keep at least this many backups per project/database
            location: 'local', 'sync', or 'both'
        """
        # Handle 'both' locations
        if location == "both":
            local_result = self.clean_old_backups(max_age_days, backup_type, keep_minimum, "local")
            sync_result = self.clean_old_backups(max_age_days, backup_type, keep_minimum, "sync")

            total_deleted = local_result[2].get("deleted", 0) + sync_result[2].get("deleted", 0)
            total_freed = local_result[2].get("size_freed_mb", 0) + sync_result[2].get("size_freed_mb", 0)

            return (
                True,
                f"Deleted {total_deleted} backups from both locations, freed {total_freed:.2f} MB",
                {
                    "deleted": total_deleted,
                    "size_freed_mb": total_freed,
                    "local": local_result[2],
                    "sync": sync_result[2],
                },
            )

        backup_path = self._get_path(location)

        if backup_path is None or not backup_path.exists():
            return True, f"No backup directory found at {location}", {"deleted": 0, "size_freed_mb": 0}

        now = datetime.now().timestamp()
        cutoff_time = now - (max_age_days * 86400)

        deleted_count = 0
        size_freed = 0
        deleted_files = []

        dirs_to_check = []
        if backup_type in ["projects", "all"]:
            projects_dir = backup_path / "projects"
            if projects_dir.exists():
                for d in projects_dir.iterdir():
                    if d.is_dir():
                        dirs_to_check.append((d, "*.tar.gz"))

        if backup_type in ["databases", "all"]:
            databases_dir = backup_path / "databases"
            if databases_dir.exists():
                for d in databases_dir.iterdir():
                    if d.is_dir():
                        dirs_to_check.append((d, "*.sql.gz"))

        for backup_dir, pattern in dirs_to_check:
            # Get all backup files sorted by date (newest first)
            backup_files = sorted(
                (f for f in backup_dir.glob(pattern) if not _is_broken_symlink(f)),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )

            # Keep at least keep_minimum backups
            files_to_consider = backup_files[keep_minimum:] if len(backup_files) > keep_minimum else []

            for backup_file in files_to_consider:
                mtime = backup_file.stat().st_mtime
                if mtime < cutoff_time:
                    try:
                        size = backup_file.stat().st_size
                        backup_file.unlink()

                        # Also delete metadata file if exists
                        metadata_file = backup_file.parent / backup_file.name.replace(".tar.gz", ".json").replace(
                            ".sql.gz", ".json"
                        )
                        try:
                            metadata_file.unlink()
                        except FileNotFoundError:
                            pass

                        deleted_count += 1
                        size_freed += size
                        deleted_files.append(backup_file.name)
                        self.logger.info(f"Deleted old backup: {backup_file}")

                    except Exception as e:
                        self.logger.error(f"Failed to delete {backup_file}: {e}")

        size_mb = round(size_freed / (1024 * 1024), 2)

        return (
            True,
            f"Deleted {deleted_count} backups older than {max_age_days} days, freed {size_mb} MB",
            {
                "deleted": deleted_count,
                "size_freed_mb": size_mb,
                "files": deleted_files[:20],  # Limit to first 20 for display
            },
        )

    def get_backup_details(self, location: str = "local") -> list[dict]:
        """
        Get detailed list of all backups grouped by project/database

        Args:
            location: 'local' or 'sync'
        """
        details: list[dict[str, Any]] = []
        backup_path = self._get_path(location)

        if backup_path is None or not backup_path.exists():
            return details

        now = datetime.now().timestamp()

        # Projects
        projects_dir = backup_path / "projects"
        if projects_dir.exists():
            for project_dir in sorted(projects_dir.iterdir()):
                if project_dir.is_dir():
                    backups = [
                        f for f in project_dir.glob("*.tar.gz")
                        if not _is_broken_symlink(f)
                    ]
                    if backups:
                        total_size = sum(f.stat().st_size for f in backups)
                        oldest = min(backups, key=lambda x: x.stat().st_mtime)
                        newest = max(backups, key=lambda x: x.stat().st_mtime)

                        oldest_age = int((now - oldest.stat().st_mtime) / 86400)
                        newest_age = int((now - newest.stat().st_mtime) / 86400)

                        details.append(
                            {
                                "name": project_dir.name,
                                "type": "project",
                                "count": len(backups),
                                "size_mb": round(total_size / (1024 * 1024), 2),
                                "oldest_days": oldest_age,
                                "newest_days": newest_age,
                            }
                        )

        # Databases
        databases_dir = backup_path / "databases"
        if databases_dir.exists():
            for db_dir in sorted(databases_dir.iterdir()):
                if db_dir.is_dir():
                    backups = [
                        f for f in db_dir.glob("*.sql.gz")
                        if not _is_broken_symlink(f)
                    ]
                    if backups:
                        total_size = sum(f.stat().st_size for f in backups)
                        oldest = min(backups, key=lambda x: x.stat().st_mtime)
                        newest = max(backups, key=lambda x: x.stat().st_mtime)

                        oldest_age = int((now - oldest.stat().st_mtime) / 86400)
                        newest_age = int((now - newest.stat().st_mtime) / 86400)

                        details.append(
                            {
                                "name": db_dir.name,
                                "type": "database",
                                "count": len(backups),
                                "size_mb": round(total_size / (1024 * 1024), 2),
                                "oldest_days": oldest_age,
                                "newest_days": newest_age,
                            }
                        )

        # Sort by size descending
        details.sort(key=lambda x: x["size_mb"], reverse=True)

        return details
