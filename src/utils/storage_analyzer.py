"""Storage analyzer for backup space management and cleanup preview"""

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.config_manager import ConfigManager

# Threshold constants for storage analysis recommendations
DISK_USAGE_CRITICAL_PERCENT = 80
DEDUP_SAVINGS_THRESHOLD_MB = 500
MAX_BACKUP_COUNT_WARNING = 50
HIGH_DUPLICATION_RATIO = 0.3


class StorageAnalyzer:
    """Analyze backup storage usage and provide cleanup recommendations"""

    def __init__(self, storage_path: Path, default_retention: dict[str, int] | None = None, config: "ConfigManager | None" = None):
        self.storage_path = Path(storage_path)
        self.projects_dir = self.storage_path / "projects"
        self.databases_dir = self.storage_path / "databases"
        self.default_retention = default_retention or {"project": 30, "database": 14}
        self.config = config
        if config:
            self.disk_usage_critical = config.get_setting(
                "storage_thresholds.disk_usage_critical_percent", DISK_USAGE_CRITICAL_PERCENT
            )
            self.dedup_savings_threshold = config.get_setting(
                "storage_thresholds.dedup_savings_threshold_mb", DEDUP_SAVINGS_THRESHOLD_MB
            )
            self.max_backup_count_warning = config.get_setting(
                "storage_thresholds.max_backup_count_warning", MAX_BACKUP_COUNT_WARNING
            )
            self.high_duplication_ratio = config.get_setting(
                "storage_thresholds.high_duplication_ratio", HIGH_DUPLICATION_RATIO
            )
        else:
            self.disk_usage_critical = DISK_USAGE_CRITICAL_PERCENT
            self.dedup_savings_threshold = DEDUP_SAVINGS_THRESHOLD_MB
            self.max_backup_count_warning = MAX_BACKUP_COUNT_WARNING
            self.high_duplication_ratio = HIGH_DUPLICATION_RATIO

    def get_total_usage(self) -> dict[str, Any]:
        """Get total storage usage statistics"""
        total_size = 0
        file_count = 0
        metadata_count = 0

        for root, _dirs, files in os.walk(self.storage_path):
            for file in files:
                file_path = Path(root) / file
                try:
                    size = file_path.stat().st_size
                    total_size += size
                    file_count += 1

                    if file.endswith(".json"):
                        metadata_count += 1
                except (OSError, PermissionError):
                    pass

        # Get disk usage for the storage path
        disk_usage = shutil.disk_usage(self.storage_path)

        return {
            "total_size": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "total_size_gb": total_size / (1024 * 1024 * 1024),
            "file_count": file_count,
            "backup_count": (file_count - metadata_count) // 2 if metadata_count > 0 else file_count,
            "metadata_count": metadata_count,
            "disk_total": disk_usage.total,
            "disk_used": disk_usage.used,
            "disk_free": disk_usage.free,
            "disk_percent": (disk_usage.used / disk_usage.total) * 100,
            "storage_percent": (total_size / disk_usage.total) * 100 if disk_usage.total > 0 else 0,
        }

    def analyze_by_type(self) -> dict[str, dict[str, Any]]:
        """Analyze storage usage by backup type (projects vs databases)"""
        results = {
            "projects": self._analyze_directory(self.projects_dir),
            "databases": self._analyze_directory(self.databases_dir),
        }

        # Calculate percentages
        total = results["projects"]["size"] + results["databases"]["size"]
        if total > 0:
            results["projects"]["percentage"] = (results["projects"]["size"] / total) * 100
            results["databases"]["percentage"] = (results["databases"]["size"] / total) * 100
        else:
            results["projects"]["percentage"] = 0
            results["databases"]["percentage"] = 0

        return results

    def analyze_by_item(self) -> list[dict[str, Any]]:
        """Analyze storage usage by individual projects and databases"""
        items = []

        # Analyze projects
        if self.projects_dir.exists():
            for project_dir in self.projects_dir.iterdir():
                if project_dir.is_dir():
                    analysis = self._analyze_item_directory(project_dir, "project")
                    items.append(analysis)

        # Analyze databases
        if self.databases_dir.exists():
            for db_dir in self.databases_dir.iterdir():
                if db_dir.is_dir():
                    analysis = self._analyze_item_directory(db_dir, "database")
                    items.append(analysis)

        # Sort by size (largest first)
        items.sort(key=lambda x: x["total_size"], reverse=True)
        return items

    def _analyze_directory(self, directory: Path) -> dict[str, Any]:
        """Analyze a single directory"""
        if not directory.exists():
            return {"size": 0, "size_mb": 0, "count": 0, "oldest": None, "newest": None}

        total_size = 0
        count = 0
        oldest = None
        newest = None

        for file in directory.rglob("*"):
            if file.is_file() and not file.name.endswith(".json"):
                try:
                    size = file.stat().st_size
                    mtime = datetime.fromtimestamp(file.stat().st_mtime)

                    total_size += size
                    count += 1

                    if oldest is None or mtime < oldest:
                        oldest = mtime

                    if newest is None or mtime > newest:
                        newest = mtime

                except (OSError, PermissionError):
                    pass

        return {
            "size": total_size,
            "size_mb": total_size / (1024 * 1024),
            "size_gb": total_size / (1024 * 1024 * 1024),
            "count": count,
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
            "age_days": (datetime.now() - oldest).days if oldest else 0,
        }

    def _analyze_item_directory(self, directory: Path, item_type: str) -> dict[str, Any]:
        """Analyze a specific project or database directory"""
        backups: list[dict[str, Any]] = []
        total_size = 0
        tagged_count = 0
        tagged_size = 0

        # Find all backup files
        patterns = ["*.tar.gz"] if item_type == "project" else ["*.sql.gz"]

        for pattern in patterns:
            for backup_file in directory.glob(pattern):
                if backup_file.is_symlink():
                    continue

                backup_info: dict[str, Any] = {
                    "name": backup_file.name,
                    "size": backup_file.stat().st_size,
                    "modified": datetime.fromtimestamp(backup_file.stat().st_mtime),
                    "age_days": (datetime.now() - datetime.fromtimestamp(backup_file.stat().st_mtime)).days,
                }

                # Check if backup is tagged
                metadata_file = directory / backup_file.name.replace(".tar.gz", ".json").replace(".sql.gz", ".json")
                if metadata_file.exists():
                    try:
                        with open(metadata_file) as f:
                            metadata = json.load(f)

                        backup_info["tagged"] = bool(
                            metadata.get("tags")
                            or metadata.get("keep_forever")
                            or metadata.get("pinned")
                            or metadata.get("importance") not in [None, "normal"]
                        )

                        backup_info["tags"] = metadata.get("tags", [])
                        backup_info["importance"] = metadata.get("importance", "normal")
                        backup_info["description"] = metadata.get("description")

                        if backup_info["tagged"]:
                            tagged_count += 1
                            tagged_size += backup_info["size"]

                    except (OSError, json.JSONDecodeError):
                        backup_info["tagged"] = False

                backups.append(backup_info)
                total_size += backup_info["size"]

        # Sort backups by date (newest first)
        backups.sort(key=lambda x: x["modified"], reverse=True)

        # Calculate statistics
        oldest_backup = min(backups, key=lambda x: x["modified"]) if backups else None
        newest_backup = max(backups, key=lambda x: x["modified"]) if backups else None

        return {
            "name": directory.name,
            "type": item_type,
            "total_size": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "backup_count": len(backups),
            "tagged_count": tagged_count,
            "tagged_size_mb": tagged_size / (1024 * 1024),
            "oldest_backup": oldest_backup["modified"].isoformat() if oldest_backup else None,
            "newest_backup": newest_backup["modified"].isoformat() if newest_backup else None,
            "age_span_days": (newest_backup["modified"] - oldest_backup["modified"]).days
            if oldest_backup and newest_backup
            else 0,
            "average_size_mb": (total_size / len(backups)) / (1024 * 1024) if backups else 0,
            "backups": backups[:10],  # Include only most recent 10 for preview
        }

    def get_cleanup_candidates(
        self, retention_days: dict[str, int] | None = None, preserve_tagged: bool = True, keep_minimum: int = 3
    ) -> dict[str, Any]:
        """Get list of backups that can be cleaned up based on retention policy"""

        if retention_days is None:
            retention_days = self.default_retention

        candidates: dict[str, Any] = {"projects": [], "databases": [], "total_size": 0, "total_count": 0}

        # Check projects
        if self.projects_dir.exists():
            for project_dir in self.projects_dir.iterdir():
                if project_dir.is_dir():
                    project_candidates = self._get_item_cleanup_candidates(
                        project_dir, "project", retention_days["project"], preserve_tagged, keep_minimum
                    )
                    candidates["projects"].extend(project_candidates)

        # Check databases
        if self.databases_dir.exists():
            for db_dir in self.databases_dir.iterdir():
                if db_dir.is_dir():
                    db_candidates = self._get_item_cleanup_candidates(
                        db_dir, "database", retention_days["database"], preserve_tagged, keep_minimum
                    )
                    candidates["databases"].extend(db_candidates)

        # Calculate totals
        for item in candidates["projects"] + candidates["databases"]:
            candidates["total_size"] += item["size"]
            candidates["total_count"] += 1

        candidates["total_size_mb"] = candidates["total_size"] / (1024 * 1024)
        candidates["total_size_gb"] = candidates["total_size"] / (1024 * 1024 * 1024)

        return candidates

    def _get_item_cleanup_candidates(
        self, directory: Path, item_type: str, retention_days: int, preserve_tagged: bool, keep_minimum: int
    ) -> list[dict[str, Any]]:
        """Get cleanup candidates for a specific item"""
        candidates = []
        cutoff_date = datetime.now() - timedelta(days=retention_days)

        # Get all backups
        pattern = "*.tar.gz" if item_type == "project" else "*.sql.gz"
        backups: list[dict[str, Any]] = []

        for backup_file in directory.glob(pattern):
            if backup_file.is_symlink():
                continue

            backup_info: dict[str, Any] = {
                "path": backup_file,
                "name": backup_file.name,
                "item_name": directory.name,
                "item_type": item_type,
                "size": backup_file.stat().st_size,
                "modified": datetime.fromtimestamp(backup_file.stat().st_mtime),
                "age_days": (datetime.now() - datetime.fromtimestamp(backup_file.stat().st_mtime)).days,
                "tagged": False,
            }

            # Check metadata
            metadata_file = directory / backup_file.name.replace(".tar.gz", ".json").replace(".sql.gz", ".json")
            if metadata_file.exists():
                try:
                    with open(metadata_file) as f:
                        metadata = json.load(f)

                    backup_info["tagged"] = bool(
                        metadata.get("tags")
                        or metadata.get("keep_forever")
                        or metadata.get("pinned")
                        or metadata.get("importance") not in [None, "normal"]
                    )

                    backup_info["tags"] = metadata.get("tags", [])
                    backup_info["importance"] = metadata.get("importance", "normal")

                except (OSError, json.JSONDecodeError):
                    pass

            backups.append(backup_info)

        # Sort by date (newest first)
        backups.sort(key=lambda x: x["modified"], reverse=True)

        # Keep minimum number of backups
        backups_to_check = backups[keep_minimum:]

        # Check remaining backups against retention policy
        for backup in backups_to_check:
            should_delete = False

            # Check if older than retention period
            if backup["modified"] < cutoff_date:
                should_delete = True

            # Preserve tagged backups if requested
            if preserve_tagged and backup["tagged"]:
                should_delete = False

            if should_delete:
                candidates.append(
                    {
                        "path": str(backup["path"]),
                        "name": backup["name"],
                        "item_name": backup["item_name"],
                        "item_type": backup["item_type"],
                        "size": backup["size"],
                        "size_mb": backup["size"] / (1024 * 1024),
                        "age_days": backup["age_days"],
                        "reason": f"Older than {retention_days} days",
                    }
                )

        return candidates

    def get_storage_timeline(self, days: int = 30) -> list[dict[str, Any]]:
        """Get storage usage timeline for the last N days"""
        now = datetime.now()

        # Walk filesystem once, collect all backup files with their metadata
        backup_files: list[tuple[datetime, int, str]] = []  # (mtime_date, size, category)
        for root, _dirs, files in os.walk(self.storage_path):
            for file in files:
                if file.endswith((".tar.gz", ".sql.gz")) and not os.path.islink(os.path.join(root, file)):
                    file_path = Path(root) / file
                    try:
                        stat = file_path.stat()
                        mtime = datetime.fromtimestamp(stat.st_mtime)
                        category = (
                            "projects"
                            if "projects" in str(file_path)
                            else "databases"
                            if "databases" in str(file_path)
                            else "other"
                        )
                        backup_files.append((mtime, stat.st_size, category))
                    except (OSError, PermissionError):
                        pass

        # Sort by mtime for efficient cumulative computation
        backup_files.sort(key=lambda x: x[0])

        # Build timeline by iterating days and accumulating
        timeline = []
        file_idx = 0
        cumulative = {"projects_size": 0, "databases_size": 0, "projects_count": 0, "databases_count": 0}

        for i in range(days, -1, -1):
            date = now - timedelta(days=i)

            # Add all files with mtime <= this date
            while file_idx < len(backup_files) and backup_files[file_idx][0].date() <= date.date():
                mtime, size, category = backup_files[file_idx]
                if category == "projects":
                    cumulative["projects_size"] += size
                    cumulative["projects_count"] += 1
                elif category == "databases":
                    cumulative["databases_size"] += size
                    cumulative["databases_count"] += 1
                file_idx += 1

            total = cumulative["projects_size"] + cumulative["databases_size"]
            timeline.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    **cumulative.copy(),
                    "total_size": total,
                    "total_size_mb": total / (1024 * 1024),
                }
            )

        return timeline

    def get_duplication_analysis(self) -> dict[str, Any]:
        """Analyze potential duplication in backups"""
        items: dict[str, dict[str, Any]] = {}

        # Analyze all backup files
        for root, _dirs, files in os.walk(self.storage_path):
            for file in files:
                if file.endswith((".tar.gz", ".sql.gz")) and not os.path.islink(os.path.join(root, file)):
                    file_path = Path(root) / file

                    # Extract item name from path
                    parts = file_path.parts
                    if "projects" in parts or "databases" in parts:
                        item_name = file_path.parent.name

                        if item_name not in items:
                            items[item_name] = {"backups": [], "total_size": 0, "unique_sizes": set()}

                        try:
                            size = file_path.stat().st_size
                            items[item_name]["backups"].append({"name": file, "size": size, "path": str(file_path)})
                            items[item_name]["total_size"] += size
                            items[item_name]["unique_sizes"].add(size)

                        except (OSError, PermissionError):
                            pass

        # Calculate duplication metrics
        duplication_report: dict[str, Any] = {"items": [], "total_potential_savings": 0, "highly_duplicated": []}

        for item_name, data in items.items():
            if len(data["backups"]) > 1:
                # Sort backups by size
                data["backups"].sort(key=lambda x: x["size"])

                # Check for same-size backups (potential duplicates)
                size_counts: dict[int, int] = {}
                for backup in data["backups"]:
                    size = backup["size"]
                    size_counts[size] = size_counts.get(size, 0) + 1

                duplicates = sum(count - 1 for count in size_counts.values() if count > 1)
                duplication_ratio = duplicates / len(data["backups"]) if data["backups"] else 0

                item_report = {
                    "name": item_name,
                    "backup_count": len(data["backups"]),
                    "total_size_mb": data["total_size"] / (1024 * 1024),
                    "unique_sizes": len(data["unique_sizes"]),
                    "potential_duplicates": duplicates,
                    "duplication_ratio": duplication_ratio,
                    "average_size_mb": (data["total_size"] / len(data["backups"])) / (1024 * 1024),
                }

                # If high duplication, add to special list
                if duplication_ratio > self.high_duplication_ratio:
                    duplication_report["highly_duplicated"].append(item_report)

                    # Estimate savings from deduplication
                    if duplicates > 0:
                        avg_size = data["total_size"] / len(data["backups"])
                        potential_savings = avg_size * duplicates * 0.8  # Assume 80% savings
                        duplication_report["total_potential_savings"] += potential_savings

                duplication_report["items"].append(item_report)

        duplication_report["total_potential_savings_mb"] = duplication_report["total_potential_savings"] / (1024 * 1024)

        return duplication_report

    def generate_cleanup_report(self, dry_run: bool = True) -> dict[str, Any]:
        """Generate a comprehensive cleanup report"""
        report: dict[str, Any] = {
            "generated": datetime.now().isoformat(),
            "dry_run": dry_run,
            "current_usage": self.get_total_usage(),
            "by_type": self.analyze_by_type(),
            "by_item": self.analyze_by_item(),
            "cleanup_candidates": self.get_cleanup_candidates(),
            "duplication": self.get_duplication_analysis(),
            "recommendations": [],
        }

        # Add recommendations based on analysis
        if report["current_usage"]["disk_percent"] > self.disk_usage_critical:
            report["recommendations"].append(
                {
                    "level": "critical",
                    "message": f"Disk usage is at {report['current_usage']['disk_percent']:.1f}%. Immediate cleanup recommended.",
                }
            )

        if report["cleanup_candidates"]["total_size_gb"] > 1:
            report["recommendations"].append(
                {
                    "level": "high",
                    "message": f"Can free {report['cleanup_candidates']['total_size_gb']:.2f} GB by cleaning old backups.",
                }
            )

        if report["duplication"]["total_potential_savings_mb"] > self.dedup_savings_threshold:
            report["recommendations"].append(
                {
                    "level": "medium",
                    "message": f"Potential {report['duplication']['total_potential_savings_mb']:.0f} MB savings from deduplication.",
                }
            )

        # Check for items with too many backups
        for item in report["by_item"]:
            if item["backup_count"] > self.max_backup_count_warning:
                report["recommendations"].append(
                    {
                        "level": "low",
                        "message": f"{item['name']} has {item['backup_count']} backups. Consider more aggressive retention.",
                    }
                )

        return report
