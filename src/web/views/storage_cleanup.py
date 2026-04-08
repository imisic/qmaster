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
from web.components import (
    Metric,
    item_heading,
    metrics_grid,
    page_header,
    section,
    show_confirm,
)
from web.state import AppComponents


# Maps for cleanup bar selectbox → backend values
_AGE_MAP = {">30 days": 30, ">60 days": 60, ">90 days": 90}
_TYPE_MAP = {"All": "all", "Projects only": "projects", "Databases only": "databases"}
_TARGET_MAP = {"Local": "local", "Sync": "sync", "Both": "both"}


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
    page_header(
        "Storage & Retention",
        "Disk usage, retention policy, and cleanup across all backup locations",
    )
    _render_storage_paths(app)

    local_stats = get_backup_stats(app.backup_cleanup, "local")
    sync_stats = get_backup_stats(app.backup_cleanup, "sync")

    # ── Global Overview — non-derivable numbers only ─────────────────
    # Per-location totals (Local/Sync size) live inside the tabs so we
    # don't show the same number twice on one screen. Top row is the
    # page-wide context: disk headroom and total backup count.
    storage_path = str(app.config.get_storage_paths()["local"])
    usage = shutil.disk_usage(storage_path)
    free_gb = usage.free / (1024**3)
    total_gb = usage.total / (1024**3)
    free_pct = (usage.free / usage.total) * 100 if usage.total else 0

    total_backups = _count_total_backups(local_stats, sync_stats)
    total_size_mb = (local_stats.get("total_size_mb", 0) or 0) + (sync_stats.get("total_size_mb", 0) or 0)

    metrics_grid(
        [
            Metric("Total Backups", total_backups),
            Metric("Total Size", f"{total_size_mb:.0f} MB"),
            Metric(
                "Disk Free",
                f"{free_gb:.1f} GB",
                help=f"{free_pct:.0f}% of {total_gb:.0f} GB",
            ),
        ],
        max_columns=3,
    )

    st.divider()

    tab1, tab2 = st.tabs(["Backup Storage", "Retention Policy"])

    with tab1:
        _render_backup_storage(app, local_stats, sync_stats)

    with tab2:
        _render_retention_policy(app)


# ═══════════════════════════════════════════════════════════════════════
# Tab 1: Backup Storage (unified single cleanup bar + location toggle)
# ═══════════════════════════════════════════════════════════════════════


def _count_total_backups(local_stats: dict[str, Any], sync_stats: dict[str, Any]) -> int:
    """Sum backup file counts across both locations."""
    total = 0
    for stats in (local_stats, sync_stats):
        if not stats.get("exists"):
            continue
        total += stats.get("projects", {}).get("files", 0)
        total += stats.get("databases", {}).get("files", 0)
    return total


def _render_backup_storage(
    app: AppComponents,
    local_stats: dict[str, Any],
    sync_stats: dict[str, Any],
) -> None:
    """Single unified backup storage view: pick a location to inspect, then clean."""
    available = []
    if local_stats["exists"]:
        available.append("Local")
    if sync_stats["exists"]:
        available.append("Sync")

    if not available:
        st.warning("No backup storage directories found")
        return

    # Pill toggle constrained to a narrow toolbar so it doesn't leave
    # an 85% empty row to the right of two small pills.
    toggle_col, _rest = st.columns([1, 5])
    with toggle_col:
        view_loc = st.segmented_control(
            "View",
            available,
            default=available[0],
            key="storage_view_loc",
            label_visibility="collapsed",
        )
    if not view_loc:
        view_loc = available[0]

    viewed_stats = local_stats if view_loc == "Local" else sync_stats
    _render_location_metrics(view_loc, viewed_stats)

    with st.expander("Backup Details"):
        details = get_backup_details(
            app.backup_cleanup,
            "local" if view_loc == "Local" else "sync",
        )
        if details:
            for item in details:
                kind = "Project" if item["type"] == "project" else "Database"
                st.text(
                    f"{kind}: {item['name']} — {item['count']} backups, "
                    f"{item['size_mb']} MB (oldest: {item['oldest_days']}d)"
                )
        else:
            st.info("No backups found")

    st.divider()
    section("Clean Up")
    st.caption(
        "Permanently removes backups older than the selected age. "
        "The `Keep minimum` setting guarantees at least that many recent backups "
        "are retained per item, even if they're older than the cutoff."
    )
    _render_cleanup_bar(app)


def _render_location_metrics(view_loc: str, stats: dict[str, Any]) -> None:
    """Render prominent location heading + path + 4-metric grid."""
    item_heading(view_loc)
    st.markdown(
        f'<div class="mono-path">{html_mod.escape(str(stats["path"]))}</div>',
        unsafe_allow_html=True,
    )

    projects_files = stats.get("projects", {}).get("files", 0)
    dbs_files = stats.get("databases", {}).get("files", 0)

    metrics_grid(
        [
            Metric(
                "Total",
                f"{stats['total_size_mb']:.0f} MB",
                help=f"Projects: {projects_files}, Databases: {dbs_files}",
            ),
            Metric(
                ">30 days",
                f"{stats['old_30d']['size_mb']:.0f} MB",
                help=f"{stats['old_30d']['files']} files",
            ),
            Metric(
                ">60 days",
                f"{stats['old_60d']['size_mb']:.0f} MB",
                help=f"{stats['old_60d']['files']} files",
            ),
            Metric(
                ">90 days",
                f"{stats['old_90d']['size_mb']:.0f} MB",
                help=f"{stats['old_90d']['files']} files",
            ),
        ],
    )


def _render_cleanup_bar(app: AppComponents) -> None:
    """Cleanup bar with dry-run preview + confirm dialog.

    Layout: Target | Age | Type | Keep | Clean (primary, 2x weight).
    Clicking Clean runs a dry_run to compute the impact, then opens a
    confirm dialog echoing the operation and preview numbers. The real
    destructive call only runs on confirm.
    """
    col_target, col_age, col_type, col_keep, col_btn = st.columns([1, 1, 1, 1, 2])

    with col_target:
        target_label = st.selectbox("Target", list(_TARGET_MAP), key="storage_clean_target")
    with col_age:
        age_opt = st.selectbox("Delete older than", list(_AGE_MAP), key="storage_clean_age")
    with col_type:
        type_opt = st.selectbox("Type", list(_TYPE_MAP), key="storage_clean_type")
    with col_keep:
        keep_min = st.number_input(
            "Keep minimum",
            min_value=1,
            max_value=30,
            value=15,
            key="storage_clean_keep",
            help="Always keep at least this many recent backups per item",
        )
    with col_btn:
        st.write("")  # baseline-align button with the labeled inputs
        if st.button(
            "Clean",
            type="primary",
            use_container_width=True,
            key="storage_clean_btn",
        ):
            _open_cleanup_confirm(
                app,
                target_label=target_label,
                age_opt=age_opt,
                type_opt=type_opt,
                keep_min=int(keep_min),
            )


def _open_cleanup_confirm(
    app: AppComponents,
    *,
    target_label: str,
    age_opt: str,
    type_opt: str,
    keep_min: int,
) -> None:
    """Run a dry_run pass to compute the impact, then open a confirm dialog."""
    max_age = _AGE_MAP[age_opt]
    backup_type = _TYPE_MAP[type_opt]
    location = _TARGET_MAP[target_label]

    with st.spinner("Computing impact..."):
        success, _msg, preview = app.backup_cleanup.clean_old_backups(
            max_age_days=max_age,
            backup_type=backup_type,
            keep_minimum=keep_min,
            location=location,
            dry_run=True,
        )

    if not success:
        st.error("Could not compute cleanup preview.")
        return

    will_delete = preview.get("deleted", 0)
    will_free_mb = preview.get("size_freed_mb", 0)

    if will_delete == 0:
        st.info("Nothing to clean — no backups match these filters (or all protected by Keep minimum).")
        return

    def _on_confirm() -> None:
        with st.spinner(f"Cleaning {target_label}..."):
            ok, message, _details = app.backup_cleanup.clean_old_backups(
                max_age_days=max_age,
                backup_type=backup_type,
                keep_minimum=keep_min,
                location=location,
                dry_run=False,
            )
        if ok:
            st.success(message)
            invalidate()
            st.rerun()
        else:
            st.error(message)

    warning_body = (
        f"This will permanently delete **{will_delete}** backup file(s) "
        f"(~**{will_free_mb:.1f} MB**) from **{target_label}** storage.\n\n"
        f"• Age: older than {max_age} days\n"
        f"• Type: {type_opt}\n"
        f"• Keep minimum: {keep_min} most recent per item\n\n"
        "This cannot be undone."
    )

    show_confirm(
        title="Confirm Cleanup",
        warning=warning_body,
        confirm_label=f"Delete {will_delete} file(s)",
        on_confirm=_on_confirm,
        key_prefix="storage_clean_dlg",
    )


# ═══════════════════════════════════════════════════════════════════════
# Tab 2: Retention Policy
# ═══════════════════════════════════════════════════════════════════════


def _render_retention_policy(app: AppComponents) -> None:
    """Retention policy management — surfaces the existing RetentionManager backend."""
    st.caption("Tiered policy: hourly, daily, weekly, monthly, yearly — recent dense, old sparse.")

    tiers = app.retention.default_tiers

    section("Tier Configuration")
    if PANDAS_AVAILABLE:
        tier_rows = [
            {
                "Tier": name.capitalize(),
                "Keep": cfg["keep"],
                "Max Age": _format_tier_age(cfg),
            }
            for name, cfg in tiers.items()
        ]
        st.dataframe(pd.DataFrame(tier_rows), hide_index=True, use_container_width=True)

    section("Current Status")
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

    st.divider()
    section("Optimize Storage")
    st.caption("Preview what would be deleted by applying the retention policy.")

    col1, _col2 = st.columns([1, 3])
    with col1:
        if st.button("Preview (Dry Run)", type="primary", use_container_width=True, key="ret_dry_run"):
            with st.spinner("Analyzing..."):
                results = app.retention.optimize_all_retention(dry_run=True)
            st.session_state.retention_preview = results

    if "retention_preview" in st.session_state:
        results = st.session_state.retention_preview
        total_to_delete = results["total_deleted"]
        space_mb = results["total_space_freed"] / (1024 * 1024) if results["total_space_freed"] else 0

        if total_to_delete > 0:
            st.markdown(
                f'<div class="health-alert">Would delete {total_to_delete} backup(s), freeing {space_mb:.1f} MB</div>',
                unsafe_allow_html=True,
            )

            with st.expander("Details"):
                for category in ["projects", "databases"]:
                    for name, report in results.get(category, {}).items():
                        if report.get("backups_to_delete", 0) > 0:
                            st.text(
                                f"{name}: {report['backups_to_delete']} to delete, "
                                f"{report['backups_to_keep']} to keep"
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
    if "max_age_days" in cfg:
        return f"{cfg['max_age_days']} days"
    if "max_age_weeks" in cfg:
        return f"{cfg['max_age_weeks']} weeks"
    if "max_age_months" in cfg:
        return f"{cfg['max_age_months']} months"
    if "max_age_years" in cfg:
        return f"{cfg['max_age_years']} years"
    return "N/A"
