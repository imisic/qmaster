"""Enhanced dataframe display with sorting and highlighting."""

from datetime import datetime
from typing import Any

import streamlit as st

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


def relative_time(dt: datetime | str | None) -> str:
    """Convert a datetime to a human-friendly relative string."""
    if dt is None:
        return "Never"

    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return str(dt)

    now = datetime.now()
    diff = now - dt
    total_seconds = diff.total_seconds()

    if total_seconds < 0 or total_seconds < 60:
        return "Just now"
    elif total_seconds < 3600:
        return f"{int(total_seconds / 60)}m ago"
    elif total_seconds < 86400:
        return f"{total_seconds / 3600:.1f}h ago"
    elif diff.days < 30:
        return f"{diff.days}d ago"
    elif diff.days < 365:
        return f"{diff.days // 30}mo ago"
    else:
        return f"{diff.days // 365}y ago"


def backup_table(
    backups: list[dict[str, Any]],
    show_type: bool = True,
    max_rows: int = 10,
    key_prefix: str = "bt",
) -> None:
    """Render a backup list as a sortable dataframe.

    Args:
        backups: List of backup dicts (from backup_engine.get_backup_status).
        show_type: Whether to show the Type column.
        max_rows: Number of rows to show by default.
        key_prefix: Unique prefix for widget keys.
    """
    if not PANDAS_AVAILABLE:
        for b in backups[:max_rows]:
            st.text(f"{b.get('name', 'unknown')}  {b.get('size_mb', 0):.1f} MB")
        return

    if not backups:
        return

    rows = []
    for b in backups:
        row = {
            "Name": b.get("name", "unknown"),
            "Size (MB)": f"{b.get('size_mb', 0):.2f}",
        }
        if show_type:
            row["Type"] = b.get("backup_type", b.get("type", "full"))

        modified = b.get("modified")
        if modified:
            row["Date"] = modified
            row["Age"] = relative_time(modified)
        else:
            row["Date"] = ""
            row["Age"] = ""

        rows.append(row)

    df = pd.DataFrame(rows)

    # Sort by Date descending
    if "Date" in df.columns and df["Date"].notna().any():
        df["_sort"] = pd.to_datetime(df["Date"], format="ISO8601", errors="coerce")
        df = df.sort_values("_sort", ascending=False).drop(columns=["_sort"])

    visible = df.head(max_rows)
    st.dataframe(visible, hide_index=True, use_container_width=True)

    remaining = len(df) - max_rows
    if remaining > 0:
        with st.expander(f"Show all ({len(df)} total)"):
            st.dataframe(df, hide_index=True, use_container_width=True)
