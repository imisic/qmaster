"""Tiered retention system for intelligent backup lifecycle management"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.config_manager import ConfigManager

UNCATEGORIZED_ARCHIVE_KEEP = 3


class RetentionManager:
    """Manage tiered backup retention policies (3-2-1 strategy)"""

    DEFAULT_TIERS: dict[str, dict[str, int]] = {
        "hourly": {"keep": 24, "max_age_hours": 24},
        "daily": {"keep": 7, "max_age_days": 7},
        "weekly": {"keep": 4, "max_age_weeks": 4},
        "monthly": {"keep": 12, "max_age_months": 12},
        "yearly": {"keep": 5, "max_age_years": 5},
    }

    def __init__(self, storage_path: Path, custom_default_tiers: dict[str, dict[str, int]] | None = None, config: "ConfigManager | None" = None):
        self.storage_path = Path(storage_path)
        self.logger = logging.getLogger("RetentionManager")
        if custom_default_tiers:
            self.default_tiers = custom_default_tiers
        elif config:
            self.default_tiers = config.get_setting("retention_tiers", None) or self.DEFAULT_TIERS
        else:
            self.default_tiers = self.DEFAULT_TIERS

    def apply_tiered_retention(
        self,
        item_type: str,
        item_name: str,
        custom_tiers: dict[str, dict[str, int]] | None = None,
        preserve_tagged: bool = True,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Apply tiered retention policy to an item's backups

        Args:
            item_type: 'project' or 'database'
            item_name: Name of the project or database
            custom_tiers: Custom retention tiers (optional)
            preserve_tagged: Preserve tagged/important backups
            dry_run: Preview without actually deleting

        Returns:
            Report of retention actions
        """
        tiers = custom_tiers or self.default_tiers

        # Get backup directory
        if item_type == "project":
            backup_dir = self.storage_path / "projects" / item_name
            pattern = "*.tar.gz"
        else:
            backup_dir = self.storage_path / "databases" / item_name
            pattern = "*.sql.gz"

        if not backup_dir.exists():
            return {"error": f"Backup directory not found: {backup_dir}"}

        # Get all backups with metadata
        backups = self._get_backups_with_metadata(backup_dir, pattern)

        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x["timestamp"], reverse=True)

        # Categorize backups into tiers
        tiered_backups = self._categorize_into_tiers(backups, tiers)

        # Determine which backups to keep and delete
        keep_list, delete_list = self._apply_retention_rules(tiered_backups, tiers, preserve_tagged)

        # Execute retention (if not dry run)
        deleted = []
        if not dry_run:
            for backup in delete_list:
                try:
                    backup_path = Path(backup["path"])
                    if backup_path.exists():
                        backup_path.unlink()

                        # Also delete metadata
                        metadata_path = backup_path.parent / backup_path.name.replace(".tar.gz", ".json").replace(
                            ".sql.gz", ".json"
                        )
                        if metadata_path.exists():
                            metadata_path.unlink()

                        deleted.append(backup["name"])
                        self.logger.info(f"Deleted: {backup['name']} (tier: {backup.get('tier', 'none')})")

                except Exception as e:
                    self.logger.error(f"Failed to delete {backup['name']}: {e}")

        # Generate report
        report = {
            "item_type": item_type,
            "item_name": item_name,
            "total_backups": len(backups),
            "backups_to_keep": len(keep_list),
            "backups_to_delete": len(delete_list),
            "space_to_recover": sum(b["size"] for b in delete_list),
            "dry_run": dry_run,
            "deleted": deleted,
            "tiers": {},
        }

        # Add tier statistics
        for tier_name in tiers:
            tier_backups = [b for b in keep_list if b.get("tier") == tier_name]
            report["tiers"][tier_name] = {
                "count": len(tier_backups),
                "oldest": min(tier_backups, key=lambda x: x["timestamp"])["timestamp"].isoformat()
                if tier_backups
                else None,
                "newest": max(tier_backups, key=lambda x: x["timestamp"])["timestamp"].isoformat()
                if tier_backups
                else None,
            }

        return report

    def _get_backups_with_metadata(self, backup_dir: Path, pattern: str) -> list[dict[str, Any]]:
        """Get all backups with their metadata"""
        backups = []

        for backup_file in backup_dir.glob(pattern):
            if backup_file.is_symlink():
                continue

            backup_info = {
                "path": str(backup_file),
                "name": backup_file.name,
                "size": backup_file.stat().st_size,
                "mtime": datetime.fromtimestamp(backup_file.stat().st_mtime),
                "tagged": False,
                "importance": "normal",
                "tags": [],
            }

            # Try to get actual timestamp from metadata
            metadata_file = backup_dir / backup_file.name.replace(".tar.gz", ".json").replace(".sql.gz", ".json")
            if metadata_file.exists():
                try:
                    with open(metadata_file) as f:
                        metadata = json.load(f)

                    # Use metadata timestamp if available
                    if "timestamp" in metadata:
                        backup_info["timestamp"] = datetime.fromisoformat(metadata["timestamp"])
                    else:
                        backup_info["timestamp"] = backup_info["mtime"]

                    # Check if tagged
                    backup_info["tagged"] = bool(
                        metadata.get("tags")
                        or metadata.get("keep_forever")
                        or metadata.get("pinned")
                        or metadata.get("importance") not in [None, "normal"]
                    )

                    backup_info["importance"] = metadata.get("importance", "normal")
                    backup_info["tags"] = metadata.get("tags", [])

                except (json.JSONDecodeError, ValueError):
                    backup_info["timestamp"] = backup_info["mtime"]
            else:
                backup_info["timestamp"] = backup_info["mtime"]

            backups.append(backup_info)

        return backups

    def _categorize_into_tiers(
        self, backups: list[dict[str, Any]], tiers: dict[str, dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Categorize backups into retention tiers"""
        now = datetime.now()
        tiered: dict[str, list[dict[str, Any]]] = {tier: [] for tier in tiers}
        tiered["uncategorized"] = []

        # Track which backups have been assigned to tiers
        assigned = set()

        # Process each tier in order (most granular first)
        tier_order = ["hourly", "daily", "weekly", "monthly", "yearly"]

        for tier_name in tier_order:
            if tier_name not in tiers:
                continue

            tier_config = tiers[tier_name]

            for backup in backups:
                if backup["name"] in assigned:
                    continue

                age = now - backup["timestamp"]

                # Check if backup fits this tier's age criteria
                fits_tier = False

                if tier_name == "hourly" and age.total_seconds() <= tier_config["max_age_hours"] * 3600:
                    fits_tier = True
                elif tier_name == "daily" and age.days <= tier_config["max_age_days"]:
                    # For daily, keep one per day
                    date_key = backup["timestamp"].date()
                    # Check if we already have a backup for this day
                    existing = [b for b in tiered["daily"] if b["timestamp"].date() == date_key]
                    if not existing:
                        fits_tier = True
                elif tier_name == "weekly" and age.days <= tier_config["max_age_weeks"] * 7:
                    # For weekly, keep one per week (preferably Sunday or oldest of week)
                    week_key = backup["timestamp"].isocalendar()[:2]  # (year, week)
                    existing = [b for b in tiered["weekly"] if b["timestamp"].isocalendar()[:2] == week_key]
                    if not existing:
                        fits_tier = True
                elif tier_name == "monthly" and age.days <= tier_config.get("max_age_months", 12) * 30:
                    # For monthly, keep one per month (preferably first of month)
                    month_key = (backup["timestamp"].year, backup["timestamp"].month)
                    existing = [
                        b for b in tiered["monthly"] if (b["timestamp"].year, b["timestamp"].month) == month_key
                    ]
                    if not existing:
                        fits_tier = True
                elif tier_name == "yearly" and age.days <= tier_config.get("max_age_years", 5) * 365:
                    # For yearly, keep one per year
                    year_key = backup["timestamp"].year
                    existing = [b for b in tiered["yearly"] if b["timestamp"].year == year_key]
                    if not existing:
                        fits_tier = True

                if fits_tier:
                    backup["tier"] = tier_name
                    tiered[tier_name].append(backup)
                    assigned.add(backup["name"])

        # Any remaining backups are uncategorized
        for backup in backups:
            if backup["name"] not in assigned:
                backup["tier"] = "uncategorized"
                tiered["uncategorized"].append(backup)

        return tiered

    def _apply_retention_rules(
        self, tiered_backups: dict[str, list[dict[str, Any]]], tiers: dict[str, dict[str, int]], preserve_tagged: bool
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Apply retention rules to determine which backups to keep"""
        keep_list = []
        delete_list = []

        for tier_name, tier_config in tiers.items():
            tier_backups = tiered_backups.get(tier_name, [])

            # Sort by timestamp (newest first)
            tier_backups.sort(key=lambda x: x["timestamp"], reverse=True)

            # Keep the configured number of backups for this tier
            keep_count = tier_config.get("keep", 5)

            for i, backup in enumerate(tier_backups):
                # Always preserve tagged backups
                if (preserve_tagged and backup["tagged"]) or i < keep_count:
                    keep_list.append(backup)
                else:
                    delete_list.append(backup)

        # Handle uncategorized backups (usually very old)
        for backup in tiered_backups.get("uncategorized", []):
            if preserve_tagged and backup["tagged"]:
                keep_list.append(backup)
            else:
                # Keep a few old backups as archive
                if len([b for b in keep_list if b.get("tier") == "uncategorized"]) < UNCATEGORIZED_ARCHIVE_KEEP:
                    keep_list.append(backup)
                else:
                    delete_list.append(backup)

        return keep_list, delete_list

    def get_retention_status(self, item_type: str | None = None, item_name: str | None = None) -> dict[str, Any]:
        """Get current retention status for items"""
        status: dict[str, Any] = {
            "items": [],
            "total_backups": 0,
            "total_size": 0,
            "tier_distribution": defaultdict(int),
        }

        # Determine items to check
        items_to_check = []

        if item_name:
            items_to_check.append((item_type, item_name))
        else:
            # Check all projects
            projects_dir = self.storage_path / "projects"
            if projects_dir.exists():
                for project_dir in projects_dir.iterdir():
                    if project_dir.is_dir():
                        items_to_check.append(("project", project_dir.name))

            # Check all databases
            databases_dir = self.storage_path / "databases"
            if databases_dir.exists():
                for db_dir in databases_dir.iterdir():
                    if db_dir.is_dir():
                        items_to_check.append(("database", db_dir.name))

        # Analyze each item
        for item_type, item_name in items_to_check:
            if item_type == "project":
                backup_dir = self.storage_path / "projects" / item_name
                pattern = "*.tar.gz"
            else:
                backup_dir = self.storage_path / "databases" / item_name
                pattern = "*.sql.gz"

            if not backup_dir.exists():
                continue

            backups = self._get_backups_with_metadata(backup_dir, pattern)
            tiered = self._categorize_into_tiers(backups, self.default_tiers)

            item_status = {
                "name": item_name,
                "type": item_type,
                "total_backups": len(backups),
                "total_size": sum(b["size"] for b in backups),
                "tiers": {},
            }

            for tier_name in self.default_tiers:
                tier_backups = tiered.get(tier_name, [])
                item_status["tiers"][tier_name] = len(tier_backups)
                status["tier_distribution"][tier_name] += len(tier_backups)

            status["items"].append(item_status)
            status["total_backups"] += item_status["total_backups"]
            status["total_size"] += item_status["total_size"]

        return status

    def optimize_all_retention(self, dry_run: bool = True) -> dict[str, Any]:
        """Apply tiered retention to all items"""
        results: dict[str, Any] = {
            "projects": {},
            "databases": {},
            "total_deleted": 0,
            "total_space_freed": 0,
            "dry_run": dry_run,
        }

        # Optimize projects
        projects_dir = self.storage_path / "projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if project_dir.is_dir():
                    report = self.apply_tiered_retention("project", project_dir.name, dry_run=dry_run)
                    results["projects"][project_dir.name] = report
                    results["total_deleted"] += len(report.get("deleted", []))
                    results["total_space_freed"] += report.get("space_to_recover", 0)

        # Optimize databases
        databases_dir = self.storage_path / "databases"
        if databases_dir.exists():
            for db_dir in databases_dir.iterdir():
                if db_dir.is_dir():
                    report = self.apply_tiered_retention("database", db_dir.name, dry_run=dry_run)
                    results["databases"][db_dir.name] = report
                    results["total_deleted"] += len(report.get("deleted", []))
                    results["total_space_freed"] += report.get("space_to_recover", 0)

        return results

    def suggest_tier_configuration(self, item_type: str, item_name: str) -> dict[str, Any]:
        """Suggest optimal tier configuration based on backup patterns"""
        if item_type == "project":
            backup_dir = self.storage_path / "projects" / item_name
            pattern = "*.tar.gz"
        else:
            backup_dir = self.storage_path / "databases" / item_name
            pattern = "*.sql.gz"

        if not backup_dir.exists():
            return {"error": "Backup directory not found"}

        backups = self._get_backups_with_metadata(backup_dir, pattern)

        if not backups:
            return {"error": "No backups found"}

        # Analyze backup frequency
        backups.sort(key=lambda x: x["timestamp"])

        intervals = []
        for i in range(1, len(backups)):
            interval = (backups[i]["timestamp"] - backups[i - 1]["timestamp"]).total_seconds() / 3600
            intervals.append(interval)

        avg_interval = 24 if not intervals else sum(intervals) / len(intervals)

        # Suggest configuration based on backup frequency
        suggested = {}

        if avg_interval <= 1:  # Hourly or more frequent
            suggested = {
                "hourly": {"keep": 48, "max_age_hours": 48},  # Keep 2 days of hourly
                "daily": {"keep": 14, "max_age_days": 14},  # Keep 2 weeks daily
                "weekly": {"keep": 8, "max_age_weeks": 8},  # Keep 2 months weekly
                "monthly": {"keep": 12, "max_age_months": 12},  # Keep 1 year monthly
            }
        elif avg_interval <= 24:  # Daily
            suggested = {
                "daily": {"keep": 30, "max_age_days": 30},  # Keep 1 month daily
                "weekly": {"keep": 12, "max_age_weeks": 12},  # Keep 3 months weekly
                "monthly": {"keep": 24, "max_age_months": 24},  # Keep 2 years monthly
            }
        else:  # Less frequent
            suggested = {
                "weekly": {"keep": 12, "max_age_weeks": 12},  # Keep 3 months weekly
                "monthly": {"keep": 36, "max_age_months": 36},  # Keep 3 years monthly
            }

        # Calculate space impact
        current_size = sum(b["size"] for b in backups)
        tiered = self._categorize_into_tiers(backups, suggested)
        keep_list, _delete_list = self._apply_retention_rules(tiered, suggested, preserve_tagged=True)

        new_size = sum(b["size"] for b in keep_list)
        savings = current_size - new_size

        return {
            "current_backups": len(backups),
            "current_size_mb": current_size / (1024 * 1024),
            "suggested_tiers": suggested,
            "backups_after": len(keep_list),
            "size_after_mb": new_size / (1024 * 1024),
            "space_savings_mb": savings / (1024 * 1024),
            "avg_backup_interval_hours": avg_interval,
        }
