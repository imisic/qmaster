"""Metric helpers."""

from datetime import datetime


def format_last_backup(last_backup_time: datetime | str | None) -> str:
    """Format a last-backup timestamp into a relative string."""
    if last_backup_time is None:
        return "No backups yet"

    if isinstance(last_backup_time, str):
        try:
            last_backup_time = datetime.fromisoformat(last_backup_time)
        except ValueError:
            return "Unknown"

    now = datetime.now()
    diff = now - last_backup_time
    total_seconds = diff.total_seconds()

    if total_seconds < 60:
        return "Just now"
    elif total_seconds < 3600:
        mins = int(total_seconds / 60)
        return f"{mins}m ago"
    elif total_seconds < 86400:
        hours = total_seconds / 3600
        return f"{hours:.1f}h ago"
    else:
        days = int(total_seconds / 86400)
        return f"{days}d ago"
