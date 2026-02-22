"""Caching layer with action-aware invalidation for the Streamlit dashboard.

Uses @st.cache_data with TTL for expensive data-fetching operations (filesystem
scans, subprocess calls, log reads).  After any mutation (backup, restore,
cleanup, config change) call ``invalidate()`` to clear all data caches so the
next render fetches fresh results.

Parameters prefixed with ``_`` are excluded from Streamlit's cache-key hashing,
which lets us pass unhashable objects like app components safely.
"""

from typing import Any, cast

import streamlit as st

# ── Invalidation ─────────────────────────────────────────────────────


def invalidate() -> None:
    """Clear every @st.cache_data entry.

    Call this before ``st.rerun()`` after any action that mutates data
    (backups, restores, cleanups, config changes, log clears, etc.).
    """
    st.cache_data.clear()


# ── Dashboard / Visualizer ───────────────────────────────────────────


@st.cache_data(ttl=60, show_spinner=False)
def get_health_metrics(_visualizer: Any) -> dict[str, Any]:
    """Health metrics — scans all backup metadata files."""
    return cast("dict[str, Any]", _visualizer.get_health_metrics())


@st.cache_data(ttl=120, show_spinner=False)
def get_backup_timeline(_visualizer: Any, days: int) -> Any:
    """Backup timeline chart (Plotly figure)."""
    return _visualizer.get_backup_timeline(days)


@st.cache_data(ttl=120, show_spinner=False)
def get_storage_trends(_visualizer: Any, days: int) -> Any:
    """Storage usage trend chart."""
    return _visualizer.get_storage_trends(days)


@st.cache_data(ttl=120, show_spinner=False)
def get_retention_distribution(_visualizer: Any) -> Any:
    """Retention tier distribution chart."""
    return _visualizer.get_retention_distribution()


@st.cache_data(ttl=120, show_spinner=False)
def get_backup_success_rate(_visualizer: Any, days: int) -> Any:
    """Backup success/failure rate chart."""
    return _visualizer.get_backup_success_rate(days)


@st.cache_data(ttl=120, show_spinner=False)
def get_storage_by_type(_visualizer: Any) -> Any:
    """Storage usage by backup type chart."""
    return _visualizer.get_storage_by_type()


@st.cache_data(ttl=60, show_spinner=False)
def get_recent_activity(_visualizer: Any, limit: int) -> list[dict[str, Any]]:
    """Recent backup activity feed."""
    return cast("list[dict[str, Any]]", _visualizer.get_recent_activity_feed(limit))


# ── Backup Engine ────────────────────────────────────────────────────


@st.cache_data(ttl=60, show_spinner=False)
def get_backup_status(_engine: Any, category: str, name: str) -> dict[str, Any]:
    """Backup status for a single project or database."""
    return cast("dict[str, Any]", _engine.get_backup_status(category, name))


# ── Storage / Cleanup ────────────────────────────────────────────────


@st.cache_data(ttl=60, show_spinner=False)
def get_backup_stats(_cleanup: Any, location: str) -> dict[str, Any]:
    """Backup storage stats for a location (local/sync)."""
    return cast("dict[str, Any]", _cleanup.get_backup_stats(location))


@st.cache_data(ttl=60, show_spinner=False)
def get_backup_details(_cleanup: Any, location: str) -> list[dict[str, Any]]:
    """Per-item backup details for a location."""
    return cast("list[dict[str, Any]]", _cleanup.get_backup_details(location))


# ── Git ──────────────────────────────────────────────────────────────


@st.cache_data(ttl=30, show_spinner=False)
def get_git_status(_git_manager: Any, path: str) -> dict[str, Any]:
    """Git repo status — runs subprocess."""
    return cast("dict[str, Any]", _git_manager.get_repo_status(path))


# ── Log Parsing ──────────────────────────────────────────────────────


@st.cache_data(ttl=30, show_spinner=False)
def get_log_stats(_parser: Any, log_path: str) -> dict[str, Any]:
    """Apache/error log file statistics."""
    return cast("dict[str, Any]", _parser.get_log_stats(log_path))


# ── Retention ────────────────────────────────────────────────────────


@st.cache_data(ttl=120, show_spinner=False)
def get_retention_status(_retention: Any) -> dict[str, Any]:
    """Retention tier status for all items."""
    return cast("dict[str, Any]", _retention.get_retention_status())


# ── Claude Config ────────────────────────────────────────────────────


@st.cache_data(ttl=120, show_spinner=False)
def get_claude_stats(_claude_config: Any) -> dict[str, Any]:
    """Claude config directory stats."""
    return cast("dict[str, Any]", _claude_config.get_stats())


@st.cache_data(ttl=120, show_spinner=False)
def get_binaries_stats(_claude_config: Any) -> dict[str, Any]:
    """Claude binary version stats."""
    return cast("dict[str, Any]", _claude_config.get_binaries_stats())
