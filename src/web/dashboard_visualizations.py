"""Enhanced dashboard visualizations for Quartermaster"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Optional imports for charts
try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import plotly.express as px
    import plotly.graph_objects as go

    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    # Create dummy class for type hints when plotly not available
    if TYPE_CHECKING:
        from plotly.graph_objects import Figure
    else:

        class Figure:
            pass

        class go:  # noqa: N801
            Figure = Figure


STALE_BACKUP_DAYS = 7


class DashboardVisualizer:
    """Generate advanced visualizations for the backup dashboard"""

    def __init__(self, storage_path: Path):
        self.storage_path = Path(storage_path)
        self.logger = logging.getLogger("DashboardVisualizer")

    def get_backup_timeline(self, days: int = 30) -> Any | None:
        """Create a timeline chart of all backups"""
        if not PLOTLY_AVAILABLE or not PANDAS_AVAILABLE:
            return None

        cutoff = datetime.now() - timedelta(days=days)
        data = []

        # Collect backup data
        for category in ["projects", "databases"]:
            category_dir = self.storage_path / category
            if not category_dir.exists():
                continue

            for item_dir in category_dir.iterdir():
                if not item_dir.is_dir():
                    continue

                # Get all backups with metadata
                for metadata_file in item_dir.glob("*.json"):
                    try:
                        with open(metadata_file) as f:
                            metadata = json.load(f)

                        timestamp = datetime.fromisoformat(metadata.get("timestamp", ""))
                        if timestamp < cutoff:
                            continue

                        data.append(
                            {
                                "timestamp": timestamp,
                                "item": item_dir.name,
                                "type": category[:-1],  # Remove 's' from plural
                                "size_mb": metadata.get("size_mb", 0),
                                "backup_type": metadata.get("backup_type", "full"),
                                "importance": metadata.get("importance", "normal"),
                            }
                        )
                    except (json.JSONDecodeError, ValueError, FileNotFoundError):
                        continue

        if not data:
            return None

        # Create DataFrame and timeline chart
        df = pd.DataFrame(data)
        df = df.sort_values("timestamp")

        # Create interactive timeline with color coding
        fig = px.scatter(
            df,
            x="timestamp",
            y="item",
            color="type",
            size="size_mb",
            hover_data=["backup_type", "importance", "size_mb"],
            title=f"Backup Timeline (Last {days} Days)",
            labels={"timestamp": "Date", "item": "Item Name", "size_mb": "Size (MB)"},
            color_discrete_map={"project": "#1f77b4", "database": "#ff7f0e"},
        )

        fig.update_layout(
            height=400, xaxis_title="Date", yaxis_title="Backup Item", hovermode="closest", showlegend=True
        )

        return fig

    def get_storage_trends(self, days: int = 30) -> Any | None:
        """Create storage usage trend chart"""
        if not PLOTLY_AVAILABLE or not PANDAS_AVAILABLE:
            return None

        # Collect daily storage data
        daily_usage: defaultdict[Any, dict[str, float]] = defaultdict(lambda: {"projects": 0.0, "databases": 0.0})
        cutoff = datetime.now() - timedelta(days=days)

        for category in ["projects", "databases"]:
            category_dir = self.storage_path / category
            if not category_dir.exists():
                continue

            for item_dir in category_dir.iterdir():
                if not item_dir.is_dir():
                    continue

                for backup_file in list(item_dir.glob("*.tar.gz")) + list(item_dir.glob("*.sql.gz")):
                    if backup_file.is_symlink():
                        continue

                    stat = backup_file.stat()
                    backup_date = datetime.fromtimestamp(stat.st_mtime)

                    if backup_date >= cutoff:
                        date_key = backup_date.date()
                        daily_usage[date_key][category] += stat.st_size / (1024 * 1024)  # Convert to MB

        if not daily_usage:
            return None

        # Prepare data for plotting
        dates = sorted(daily_usage.keys())
        project_sizes = [daily_usage[d]["projects"] for d in dates]
        database_sizes = [daily_usage[d]["databases"] for d in dates]

        # Create stacked area chart
        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=dates,
                y=project_sizes,
                name="Projects",
                mode="lines",
                fill="tonexty",
                line={"color": "#1f77b4", "width": 2},
                fillcolor="rgba(31, 119, 180, 0.3)",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=dates,
                y=database_sizes,
                name="Databases",
                mode="lines",
                fill="tonexty",
                line={"color": "#ff7f0e", "width": 2},
                fillcolor="rgba(255, 127, 14, 0.3)",
            )
        )

        fig.update_layout(
            title=f"Storage Usage Trend (Last {days} Days)",
            xaxis_title="Date",
            yaxis_title="Storage (MB)",
            hovermode="x unified",
            height=350,
        )

        return fig

    def get_retention_distribution(self) -> Any | None:
        """Create retention tier distribution chart"""
        if not PLOTLY_AVAILABLE:
            return None

        tier_counts: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Analyze backups by retention tier
        for category in ["projects", "databases"]:
            category_dir = self.storage_path / category
            if not category_dir.exists():
                continue

            for item_dir in category_dir.iterdir():
                if not item_dir.is_dir():
                    continue

                backups = self._categorize_by_age(item_dir)
                for tier, count in backups.items():
                    tier_counts[tier][item_dir.name] = count

        if not tier_counts:
            return None

        # Create grouped bar chart
        fig = go.Figure()

        tiers = ["hourly", "daily", "weekly", "monthly", "yearly", "archive"]
        colors = ["#e74c3c", "#e67e22", "#f39c12", "#2ecc71", "#3498db", "#9b59b6"]

        for i, tier in enumerate(tiers):
            if tier in tier_counts:
                items = list(tier_counts[tier].keys())
                counts = list(tier_counts[tier].values())

                fig.add_trace(go.Bar(name=tier.capitalize(), x=items, y=counts, marker_color=colors[i % len(colors)]))

        fig.update_layout(
            title="Backup Distribution by Retention Tier",
            xaxis_title="Backup Item",
            yaxis_title="Number of Backups",
            barmode="stack",
            height=400,
            showlegend=True,
        )

        return fig

    def get_backup_success_rate(self, days: int = 7) -> Any | None:
        """Create backup success/failure rate chart"""
        if not PLOTLY_AVAILABLE:
            return None

        # Parse backup logs for success/failure data
        log_file = self.storage_path / "logs" / "backup.log"

        if not log_file.exists():
            return None

        success_count = 0
        failure_count = 0
        cutoff = datetime.now() - timedelta(days=days)

        try:
            with open(log_file) as f:
                for line in f:
                    # Parse log timestamp
                    if " - BackupEngine - " in line:
                        try:
                            timestamp_str = line.split(" - ")[0]
                            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")

                            if timestamp >= cutoff:
                                if "Successfully backed up" in line:
                                    success_count += 1
                                elif "Failed to backup" in line:
                                    failure_count += 1
                        except (ValueError, IndexError):
                            continue
        except Exception as e:
            logging.warning(f"Error reading backup success rate: {e}")
            return None

        if success_count == 0 and failure_count == 0:
            return None

        # Create pie chart
        fig = go.Figure(
            data=[
                go.Pie(
                    labels=["Success", "Failure"],
                    values=[success_count, failure_count],
                    hole=0.4,
                    marker_colors=["#2ecc71", "#e74c3c"],
                )
            ]
        )

        success_rate = (
            (success_count / (success_count + failure_count)) * 100 if (success_count + failure_count) > 0 else 0
        )

        fig.update_layout(
            title=f"Backup Success Rate (Last {days} Days)",
            height=350,
            annotations=[{"text": f"{success_rate:.1f}%", "x": 0.5, "y": 0.5, "font_size": 20, "showarrow": False}],
        )

        return fig

    def get_storage_by_type(self) -> Any | None:
        """Create storage usage by backup type chart"""
        if not PLOTLY_AVAILABLE:
            return None

        type_sizes: defaultdict[str, float] = defaultdict(float)
        type_counts: defaultdict[str, int] = defaultdict(int)

        # Collect storage by backup type
        for category in ["projects", "databases"]:
            category_dir = self.storage_path / category
            if not category_dir.exists():
                continue

            for item_dir in category_dir.iterdir():
                if not item_dir.is_dir():
                    continue

                for metadata_file in item_dir.glob("*.json"):
                    try:
                        with open(metadata_file) as f:
                            metadata = json.load(f)

                        backup_type = metadata.get("backup_type", "full")
                        size_mb = metadata.get("size_mb", 0)

                        type_sizes[backup_type] += size_mb
                        type_counts[backup_type] += 1

                    except (json.JSONDecodeError, FileNotFoundError):
                        continue

        if not type_sizes:
            return None

        # Create sunburst chart
        labels = []
        parents = []
        values = []
        colors = []

        total_size = sum(type_sizes.values())
        labels.append("Total")
        parents.append("")
        values.append(total_size)
        colors.append("#ecf0f1")

        color_map = {"full": "#3498db", "incremental": "#2ecc71", "snapshot": "#e74c3c"}

        for backup_type, size in type_sizes.items():
            labels.append(f"{backup_type.capitalize()}<br>{type_counts[backup_type]} backups")
            parents.append("Total")
            values.append(size)
            colors.append(color_map.get(backup_type, "#95a5a6"))

        fig = go.Figure(
            go.Sunburst(
                labels=labels,
                parents=parents,
                values=values,
                branchvalues="total",
                marker={"colors": colors},
                textinfo="label+value",
                hovertemplate="<b>%{label}</b><br>Size: %{value:.1f} MB<extra></extra>",
            )
        )

        fig.update_layout(title="Storage Usage by Backup Type", height=400)

        return fig

    def get_recent_activity_feed(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent backup activity for activity feed"""
        activities = []

        # Collect recent backups
        for category in ["projects", "databases"]:
            category_dir = self.storage_path / category
            if not category_dir.exists():
                continue

            for item_dir in category_dir.iterdir():
                if not item_dir.is_dir():
                    continue

                for metadata_file in item_dir.glob("*.json"):
                    try:
                        with open(metadata_file) as f:
                            metadata = json.load(f)

                        activities.append(
                            {
                                "timestamp": datetime.fromisoformat(metadata.get("timestamp", "")),
                                "item_name": metadata.get("item_name", "Unknown"),
                                "item_type": metadata.get("item_type", "unknown"),
                                "backup_name": metadata.get("backup_name", ""),
                                "size_mb": metadata.get("size_mb", 0),
                                "backup_type": metadata.get("backup_type", "full"),
                                "importance": metadata.get("importance", "normal"),
                                "tags": metadata.get("tags", []),
                            }
                        )
                    except (json.JSONDecodeError, ValueError, FileNotFoundError):
                        continue

        # Sort by timestamp and return most recent
        activities.sort(key=lambda x: x["timestamp"], reverse=True)
        return activities[:limit]

    def _categorize_by_age(self, backup_dir: Path) -> dict[str, int]:
        """Categorize backups by age into retention tiers"""
        now = datetime.now()
        tiers = {"hourly": 0, "daily": 0, "weekly": 0, "monthly": 0, "yearly": 0, "archive": 0}

        for backup_file in list(backup_dir.glob("*.tar.gz")) + list(backup_dir.glob("*.sql.gz")):
            if backup_file.is_symlink():
                continue

            age = now - datetime.fromtimestamp(backup_file.stat().st_mtime)

            if age.days == 0:
                tiers["hourly"] += 1
            elif age.days <= 7:
                tiers["daily"] += 1
            elif age.days <= 30:
                tiers["weekly"] += 1
            elif age.days <= 365:
                tiers["monthly"] += 1
            elif age.days <= 365 * 5:
                tiers["yearly"] += 1
            else:
                tiers["archive"] += 1

        return tiers

    def get_health_metrics(self) -> dict[str, Any]:
        """Calculate overall system health metrics"""
        total_backups = 0
        total_size_gb = 0.0
        oldest_backup: datetime | None = None
        newest_backup: datetime | None = None
        avg_backup_size_mb = 0.0
        items_without_recent_backup: list[str] = []

        all_backups: list[dict[str, Any]] = []
        item_latest: dict[str, datetime] = {}

        # Analyze all backups
        for category in ["projects", "databases"]:
            category_dir = self.storage_path / category
            if not category_dir.exists():
                continue

            for item_dir in category_dir.iterdir():
                if not item_dir.is_dir():
                    continue

                item_backups: list[datetime] = []
                for backup_file in list(item_dir.glob("*.tar.gz")) + list(item_dir.glob("*.sql.gz")):
                    if backup_file.is_symlink():
                        continue

                    stat = backup_file.stat()
                    backup_time = datetime.fromtimestamp(stat.st_mtime)

                    all_backups.append({"time": backup_time, "size": stat.st_size})

                    item_backups.append(backup_time)

                if item_backups:
                    latest = max(item_backups)
                    item_latest[item_dir.name] = latest

                    # Check if backup is stale (>7 days old)
                    if (datetime.now() - latest).days > STALE_BACKUP_DAYS:
                        items_without_recent_backup.append(item_dir.name)

        if all_backups:
            total_backups = len(all_backups)
            total_size_gb = sum(b["size"] for b in all_backups) / (1024**3)
            oldest_backup = min(b["time"] for b in all_backups)
            newest_backup = max(b["time"] for b in all_backups)
            avg_backup_size_mb = (sum(b["size"] for b in all_backups) / len(all_backups)) / (1024**2)

        return {
            "total_backups": total_backups,
            "total_size_gb": total_size_gb,
            "oldest_backup": oldest_backup,
            "newest_backup": newest_backup,
            "avg_backup_size_mb": avg_backup_size_mb,
            "items_without_recent_backup": items_without_recent_backup,
            "critical_items": [],
        }
