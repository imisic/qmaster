"""Color-coded status badges replacing emoji indicators."""

from datetime import datetime, timedelta


def status_badge(text: str, level: str) -> str:
    """Return HTML for a pill-shaped status badge.

    Args:
        text: Label text shown inside the badge.
        level: One of 'healthy', 'success', 'warning', 'critical',
               'error', 'running', 'info', 'inactive'.
    """
    return f'<span class="status-badge status-{level}">{text}</span>'


def type_badge(label: str, kind: str = "") -> str:
    """Return HTML for a type badge (project, database, full, incremental, etc.)."""
    css_class = f"type-{kind}" if kind else ""
    return f'<span class="type-badge {css_class}">{label}</span>'


def health_level(last_backup_time: datetime | str | None) -> str:
    """Determine health level from a backup timestamp.

    Returns:
        'healthy', 'warning', 'critical', or 'inactive'
    """
    if last_backup_time is None:
        return "inactive"

    if isinstance(last_backup_time, str):
        try:
            last_backup_time = datetime.fromisoformat(last_backup_time)
        except ValueError:
            return "inactive"

    age = datetime.now() - last_backup_time
    if age <= timedelta(days=3):
        return "healthy"
    elif age <= timedelta(days=7):
        return "warning"
    else:
        return "critical"


def health_label(level: str) -> str:
    """Friendly label for a health level."""
    return {
        "healthy": "Healthy",
        "warning": "Stale",
        "critical": "Overdue",
        "inactive": "No backups",
    }.get(level, level.capitalize())


def task_status_badge(status_value: str) -> str:
    """Badge for a background task status."""
    mapping = {
        "completed": ("Completed", "success"),
        "failed": ("Failed", "error"),
        "running": ("Running", "running"),
        "pending": ("Pending", "info"),
        "skipped": ("Skipped", "inactive"),
    }
    text, level = mapping.get(status_value, (status_value, "inactive"))
    return status_badge(text, level)
