"""Storage & Retention page — backup storage, retention policy, and cleanup actions."""

import html as html_mod
import shutil
from typing import Any

import streamlit as st

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from web.cache import (
    get_backup_details,
    get_backup_stats,
    get_retention_status,
    invalidate,
)
from web.state import AppComponents


def _render_storage_paths(app: AppComponents) -> None:
    """Render the storage paths expander, absorbed from the old sidebar Settings."""
    with st.expander("Storage Paths", expanded=False):
        paths = app.config.get_storage_paths()
        st.markdown(f"**Local:** `{paths['local']}`")
        sync = paths.get("sync")
        if sync:
            st.markdown(f"**Sync:** `{sync}`")
        else:
            st.markdown("**Sync:** *not configured*")


def render_storage_cleanup(app: AppComponents) -> None:
    """Render the Storage & Retention page."""
    st.markdown('<div class="page-title">Storage & Retention</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-subtitle">Disk usage, retention policy, and cleanup across all backup locations</div>',
        unsafe_allow_html=True,
    )
    _render_storage_paths(app)

    # ── Global Metrics ───────────────────────────────────────────────
    local_stats = get_backup_stats(app.backup_cleanup, "local")
    sync_stats = get_backup_stats(app.backup_cleanup, "sync")

    col1, col2, col3 = st.columns(3)
    with col1:
        local_size = local_stats["total_size_mb"] if local_stats["exists"] else 0
        st.metric("Local Storage", f"{local_size:.0f} MB")
    with col2:
        sync_size = sync_stats["total_size_mb"] if sync_stats["exists"] else 0
        st.metric("Sync Storage", f"{sync_size:.0f} MB")
    with col3:
        storage_path = str(app.config.get_storage_paths()["local"])
        usage = shutil.disk_usage(storage_path)
        free_gb = usage.free / (1024**3)
        st.metric("Disk Free", f"{free_gb:.1f} GB")

    st.markdown("---")

    # ── Tabs ─────────────────────────────────────────────────────────
    tab1, tab2 = st.tabs(["Backup Storage", "Retention Policy"])

    with tab1:
        _render_backup_storage(app, local_stats, sync_stats)

    with tab2:
        _render_retention_policy(app)


# ═══════════════════════════════════════════════════════════════════════
# Tab 1: Backup Storage
# ═══════════════════════════════════════════════════════════════════════


def _render_backup_storage(app: AppComponents, local_stats: dict[str, Any], sync_stats: dict[str, Any]) -> None:
    """Backup storage cleanup controls for local and sync locations."""
    left, right = st.columns(2)

    with left:
        st.markdown('<div class="section-header">Local Storage</div>', unsafe_allow_html=True)
        _render_location_cleanup(app, local_stats, "local", "local")

    with right:
        st.markdown('<div class="section-header">Sync Storage</div>', unsafe_allow_html=True)
        _render_location_cleanup(app, sync_stats, "sync", "sync")

    st.markdown("---")

    # Clean Both
    st.markdown('<div class="section-header">Clean Both Locations</div>', unsafe_allow_html=True)
    _render_cleanup_controls(app, "both", "both")


def _render_location_cleanup(app: AppComponents, stats: dict[str, Any], location: str, prefix: str) -> None:
    """Render metrics + controls for a single backup location."""
    if not stats["exists"]:
        st.warning(f"{location.upper()} backup directory not found")
        return

    st.markdown(f'<span class="mono-text">{html_mod.escape(str(stats["path"]))}</span>', unsafe_allow_html=True)

    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
    with mcol1:
        st.metric(
            "Total",
            f"{stats['total_size_mb']:.0f} MB",
            help=f"Projects: {stats.get('projects', {}).get('files', 0)}, DBs: {stats.get('databases', {}).get('files', 0)}",
        )
    with mcol2:
        st.metric(
            ">30d",
            f"{stats['old_30d']['size_mb']:.0f} MB",
            delta=f"{stats['old_30d']['files']} files",
            delta_color="off",
        )
    with mcol3:
        st.metric(
            ">60d",
            f"{stats['old_60d']['size_mb']:.0f} MB",
            delta=f"{stats['old_60d']['files']} files",
            delta_color="off",
        )
    with mcol4:
        st.metric(
            ">90d",
            f"{stats['old_90d']['size_mb']:.0f} MB",
            delta=f"{stats['old_90d']['files']} files",
            delta_color="off",
        )

    _render_cleanup_controls(app, location, prefix)

    with st.expander("Backup Details"):
        details = get_backup_details(app.backup_cleanup, location)
        if details:
            for item in details:
                kind = "Project" if item["type"] == "project" else "Database"
                st.text(
                    f"{kind}: {item['name']} — {item['count']} backups, {item['size_mb']} MB (oldest: {item['oldest_days']}d)"
                )
        else:
            st.info("No backups found")


def _render_cleanup_controls(app: AppComponents, location: str, prefix: str) -> None:
    """Age/type/keep selectors + clean button."""
    col_age, col_type, col_keep, col_btn = st.columns([1, 1, 1, 1])

    with col_age:
        age_opt = st.selectbox("Delete older than", [">30 days", ">60 days", ">90 days"], key=f"{prefix}_age")
    with col_type:
        type_opt = st.selectbox("Type", ["All", "Projects only", "Databases only"], key=f"{prefix}_type")
    with col_keep:
        keep_min = st.number_input(
            "Keep minimum",
            min_value=1,
            max_value=30,
            value=15,
            key=f"{prefix}_keep",
            help="Always keep at least this many recent backups per item",
        )
    with col_btn:
        st.markdown('<div class="btn-align"></div>', unsafe_allow_html=True)
        if st.button("Clean", type="primary", use_container_width=True, key=f"{prefix}_clean_btn"):
            age_map = {">30 days": 30, ">60 days": 60, ">90 days": 90}
            type_map = {"All": "all", "Projects only": "projects", "Databases only": "databases"}

            with st.spinner("Cleaning..."):
                success, message, details = app.backup_cleanup.clean_old_backups(
                    max_age_days=age_map[age_opt],
                    backup_type=type_map[type_opt],
                    keep_minimum=keep_min,
                    location=location,
                )
            if success:
                if details.get("deleted", 0) > 0:
                    st.success(message)
                else:
                    st.info("Nothing to clean (minimum kept)")
                invalidate()
                st.rerun()
            else:
                st.error(message)


# ═══════════════════════════════════════════════════════════════════════
# Tab 2: Retention Policy
# ═══════════════════════════════════════════════════════════════════════


def _render_retention_policy(app: AppComponents) -> None:
    """Retention policy management — surfaces the existing RetentionManager backend."""
    st.markdown('<div class="section-header">Tiered Retention Policy</div>', unsafe_allow_html=True)
    st.markdown(
        '<span style="color:#a1a7b5">Automatically manage backup lifecycle: hourly, daily, weekly, monthly, yearly tiers</span>',
        unsafe_allow_html=True,
    )

    # Current tier configuration
    tiers = app.retention.default_tiers
    st.markdown("**Current Tier Configuration**")

    if PANDAS_AVAILABLE:
        tier_rows = []
        for name, cfg in tiers.items():
            tier_rows.append(
                {
                    "Tier": name.capitalize(),
                    "Keep": cfg["keep"],
                    "Max Age": _format_tier_age(cfg),
                }
            )
        st.dataframe(pd.DataFrame(tier_rows), hide_index=True, use_container_width=True)

    st.markdown("---")

    # Retention status
    st.markdown("**Current Status**")
    ret_status = get_retention_status(app.retention)

    if ret_status["items"]:
        if PANDAS_AVAILABLE:
            status_rows = []
            for item in ret_status["items"]:
                row = {
                    "Name": item["name"],
                    "Type": item["type"].capitalize(),
                    "Total Backups": item["total_backups"],
                    "Size (MB)": f"{item['total_size'] / (1024 * 1024):.1f}",
                }
                for tier_name in tiers:
                    row[tier_name.capitalize()] = item["tiers"].get(tier_name, 0)
                status_rows.append(row)
            st.dataframe(pd.DataFrame(status_rows), hide_index=True, use_container_width=True)
    else:
        st.info("No backups found for retention analysis")

    st.markdown("---")

    # Dry-run preview
    st.markdown("**Optimize Storage**")
    st.markdown(
        '<span style="color:#a1a7b5">Preview what would be deleted by applying the retention policy</span>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Preview (Dry Run)", type="primary", use_container_width=True, key="ret_dry_run"):
            with st.spinner("Analyzing..."):
                results = app.retention.optimize_all_retention(dry_run=True)
            st.session_state.retention_preview = results
    with col2:
        pass

    if "retention_preview" in st.session_state:
        results = st.session_state.retention_preview
        total_to_delete = results["total_deleted"]
        space_mb = results["total_space_freed"] / (1024 * 1024) if results["total_space_freed"] else 0

        if total_to_delete > 0:
            st.markdown(
                f'<div class="health-alert">Would delete {total_to_delete} backup(s), freeing {space_mb:.1f} MB</div>',
                unsafe_allow_html=True,
            )

            # Show per-item details
            with st.expander("Details"):
                for category in ["projects", "databases"]:
                    for name, report in results.get(category, {}).items():
                        if report.get("backups_to_delete", 0) > 0:
                            st.text(
                                f"{name}: {report['backups_to_delete']} to delete, {report['backups_to_keep']} to keep"
                            )

            if st.button("Apply Retention Policy", type="primary", key="ret_apply"):
                with st.spinner("Applying retention policy..."):
                    results = app.retention.optimize_all_retention(dry_run=False)
                    deleted = results["total_deleted"]
                    freed = results["total_space_freed"] / (1024 * 1024) if results["total_space_freed"] else 0
                if deleted > 0:
                    st.success(f"Deleted {deleted} backups, freed {freed:.1f} MB")
                    del st.session_state.retention_preview
                    invalidate()
                    st.rerun()
                else:
                    st.info("No backups were deleted")
        else:
            st.success("All backups are within retention policy — nothing to delete")


def _format_tier_age(cfg: dict[str, Any]) -> str:
    """Format a tier's max age into a readable string."""
    if "max_age_hours" in cfg:
        return f"{cfg['max_age_hours']} hours"
    elif "max_age_days" in cfg:
        return f"{cfg['max_age_days']} days"
    elif "max_age_weeks" in cfg:
        return f"{cfg['max_age_weeks']} weeks"
    elif "max_age_months" in cfg:
        return f"{cfg['max_age_months']} months"
    elif "max_age_years" in cfg:
        return f"{cfg['max_age_years']} years"
    return "N/A"
