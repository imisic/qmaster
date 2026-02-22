"""Storage & Cleanup page - merges Backup Cleanup, Claude Config, and Retention."""

import logging
from datetime import datetime, timedelta

import streamlit as st

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from typing import Any

from web.cache import (
    get_backup_details,
    get_backup_stats,
    get_binaries_stats,
    get_claude_stats,
    get_retention_status,
    invalidate,
)
from web.components.action_bar import danger_button
from web.state import AppComponents


def render_storage_cleanup(app: AppComponents) -> None:
    """Render the Storage & Cleanup page."""
    st.markdown('<div class="page-title">Storage & Cleanup</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-subtitle">Manage disk space across all backup locations</div>', unsafe_allow_html=True
    )

    # ── Global Metrics ───────────────────────────────────────────────
    local_stats = get_backup_stats(app.backup_cleanup, "local")
    sync_stats = get_backup_stats(app.backup_cleanup, "sync")
    claude_stats = get_claude_stats(app.claude_config)
    binaries_stats = get_binaries_stats(app.claude_config)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        local_size = local_stats["total_size_mb"] if local_stats["exists"] else 0
        st.metric("Local Storage", f"{local_size:.0f} MB")
    with col2:
        sync_size = sync_stats["total_size_mb"] if sync_stats["exists"] else 0
        st.metric("Sync Storage", f"{sync_size:.0f} MB")
    with col3:
        claude_size = claude_stats.get("total_size_mb", 0) if claude_stats.get("exists") else 0
        st.metric("Claude Config", f"{claude_size} MB")
    with col4:
        import shutil

        storage_path = str(app.config.get_storage_paths()["local"])
        usage = shutil.disk_usage(storage_path)
        free_gb = usage.free / (1024**3)
        st.metric("Disk Free", f"{free_gb:.1f} GB")

    st.markdown("---")

    # ── Tabs ─────────────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs(["Backup Storage", "Claude Config", "Retention Policy"])

    with tab1:
        _render_backup_storage(app, local_stats, sync_stats)

    with tab2:
        _render_claude_config(app, claude_stats, binaries_stats)

    with tab3:
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

    st.markdown(f'<span class="mono-text">{stats["path"]}</span>', unsafe_allow_html=True)

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
            if success and details.get("size_freed_mb", 0) > 0:
                st.success(message)
                invalidate()
                st.rerun()
            elif success:
                st.info("Nothing to clean (minimum kept)")
            else:
                st.error(message)


# ═══════════════════════════════════════════════════════════════════════
# Tab 2: Claude Config
# ═══════════════════════════════════════════════════════════════════════


def _render_claude_config(app: AppComponents, stats: dict[str, Any], binaries_stats: dict[str, Any]) -> None:
    """Claude config cleanup — unified table replacing 12 cards."""
    if not stats.get("exists"):
        st.warning("Claude configuration directory not found at ~/.claude/")
        return

    # Overview
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Size", f"{stats['total_size_mb']} MB")
    with col2:
        st.metric("Projects", stats["projects_count"])
    with col3:
        health = stats.get("health", "unknown")
        label = {"good": "Healthy", "warning": "Needs cleanup", "critical": "Cleanup needed"}.get(health, health)
        st.metric("Health", label)

    st.markdown("---")

    # Quick actions
    qcol1, qcol2, qcol3 = st.columns(3)
    with qcol1:
        if st.button("Clean All (except projects)", type="primary", use_container_width=True, key="cc_clean_all"):
            with st.spinner("Cleaning..."):
                success, message, details = app.claude_config.clean_all(keep_projects=True)
            if success and details.get("total_freed_mb", 0) > 0:
                st.success(message)
                invalidate()
                st.rerun()
            elif success:
                st.info("Nothing to clean")
            else:
                st.error(message)
    with qcol2:
        if binaries_stats.get("exists") and binaries_stats.get("version_count", 0) > 1:
            if st.button("Clean Old Binaries", use_container_width=True, key="cc_clean_binaries"):
                with st.spinner("Removing old versions..."):
                    success, message, details = app.claude_config.clean_old_binaries()
                if success and details.get("size_freed_mb", 0) > 0:
                    st.success(message)
                    invalidate()
                    st.rerun()
                elif success:
                    st.info("Nothing to clean")
                else:
                    st.error(message)
        else:
            st.button("Clean Old Binaries", use_container_width=True, disabled=True, key="cc_clean_binaries_dis")
    with qcol3:
        if binaries_stats.get("exists"):
            st.text(f"Binaries: {binaries_stats['total_size_mb']} MB ({binaries_stats['version_count']} version(s))")

    st.markdown("---")

    # Unified cleanup table
    st.markdown('<div class="section-header">Category Cleanup</div>', unsafe_allow_html=True)

    # Gather all category stats
    categories = _get_claude_categories(app)
    if not categories:
        st.info("All categories are empty")
    else:
        for cat in categories:
            if cat["size_mb"] > 0:
                _render_category_row(cat)

    st.markdown("---")

    # Misc cleanup
    _render_misc_cleanup(app)

    # Safe files warning
    st.markdown("---")
    st.markdown(
        '<div class="health-alert">'
        "Do NOT delete: plugins/ (MCP servers), settings.json (permissions), CLAUDE.md (instructions), mcp.json (MCP config)"
        "</div>",
        unsafe_allow_html=True,
    )

    # Project History Cleanup
    with st.expander("Project History Cleanup"):
        _render_project_history_cleanup(app)

    # MCP Servers
    with st.expander("MCP Server Management"):
        _render_mcp_servers(app)


def _get_claude_categories(app: AppComponents) -> list[dict[str, Any]]:
    """Collect stats for all Claude config categories."""
    cc = app.claude_config
    categories = []

    def _add(name, stats_fn, clean_fn, has_age=True):
        try:
            s = stats_fn()
        except Exception as e:
            logging.warning(f"Could not get stats for '{name}': {e}")
            return
        if s.get("exists"):
            categories.append(
                {
                    "name": name,
                    "size_mb": s.get("total_size_mb", s.get("size_mb", 0)),
                    "file_count": s.get("file_count", s.get("line_count", 0)),
                    "clean_fn": clean_fn,
                    "has_age": has_age,
                }
            )

    _add("File History", lambda: cc.get_dir_stats("file_history"), lambda age=None: cc.clean_dir("file_history", age))
    _add("Debug Logs", lambda: cc.get_dir_stats("debug"), lambda age=None: cc.clean_dir("debug", age))
    _add("Command History", cc.get_history_stats, cc.clean_history, has_age=False)
    _add("Shell Snapshots", lambda: cc.get_dir_stats("shell_snapshots"), lambda age=None: cc.clean_dir("shell_snapshots", age))
    _add("Session Env", lambda: cc.get_dir_stats("session_env"), lambda age=None: cc.clean_dir("session_env", age))
    _add("Plans", lambda: cc.get_dir_stats("plans"), lambda age=None: cc.clean_dir("plans", age))
    _add("Image Cache", lambda: cc.get_dir_stats("image_cache"), lambda age=None: cc.clean_dir("image_cache", age))
    _add("Todos", lambda: cc.get_dir_stats("todos"), lambda age=None: cc.clean_dir("todos", age))
    _add("Plugins Cache", cc.get_plugins_cache_stats, cc.clean_plugins_cache, has_age=False)
    _add("Paste Cache", lambda: cc.get_dir_stats("paste_cache"), lambda age=None: cc.clean_dir("paste_cache", age), has_age=False)
    _add("Tasks Cache", lambda: cc.get_dir_stats("tasks"), lambda age=None: cc.clean_dir("tasks", age), has_age=False)

    return categories


def _render_category_row(cat: dict[str, Any]) -> None:
    """Render a single cleanup category row."""
    col_name, col_size, col_files, col_btn = st.columns([3, 1, 1, 1])
    with col_name:
        st.markdown(f"**{cat['name']}**")
    with col_size:
        st.text(f"{cat['size_mb']:.1f} MB")
    with col_files:
        st.text(f"{cat['file_count']}" if cat["file_count"] else "")
    with col_btn:
        key = f"cc_cat_{cat['name'].lower().replace(' ', '_')}"
        if st.button("Clean", key=key, use_container_width=True):
            with st.spinner(f"Cleaning {cat['name']}..."):
                if cat["has_age"]:
                    success, message, details = cat["clean_fn"](None)
                else:
                    success, message, details = cat["clean_fn"]()
            if success and details.get("size_freed_mb", 0) > 0:
                st.success(f"Freed {details['size_freed_mb']:.1f} MB")
                invalidate()
                st.rerun()
            elif success:
                st.info("Nothing to clean")
            else:
                st.error(message)


def _render_misc_cleanup(app: AppComponents) -> None:
    """Misc cleanup: cache, statsig, ide, telemetry, stale files."""
    cc = app.claude_config
    cache_stats = cc.get_dir_stats("cache")
    statsig_stats = cc.get_dir_stats("statsig")
    ide_stats = cc.get_dir_stats("ide")
    telemetry_stats = cc.get_dir_stats("telemetry")
    stale_stats = cc.get_stale_files_stats()

    misc_total = sum(
        s["total_size_mb"]
        for s in [cache_stats, statsig_stats, ide_stats, telemetry_stats, stale_stats]
        if s.get("exists")
    )

    if misc_total > 0:
        col1, col2 = st.columns([3, 1])
        with col1:
            parts = []
            if cache_stats.get("exists") and cache_stats["total_size_mb"] > 0:
                parts.append(f"cache: {cache_stats['total_size_mb']} MB")
            if statsig_stats.get("exists") and statsig_stats["total_size_mb"] > 0:
                parts.append(f"statsig: {statsig_stats['total_size_mb']} MB")
            if ide_stats.get("exists") and ide_stats["total_size_mb"] > 0:
                parts.append(f"ide: {ide_stats['total_size_mb']} MB")
            if telemetry_stats.get("exists") and telemetry_stats["total_size_mb"] > 0:
                parts.append(f"telemetry: {telemetry_stats['total_size_mb']} MB")
            if stale_stats.get("exists") and stale_stats["total_size_mb"] > 0:
                parts.append(f"stale: {stale_stats.get('file_count', 0)} files")
            st.text(f"Misc: {misc_total:.1f} MB ({', '.join(parts)})")
        with col2:
            if st.button("Clean All Misc", use_container_width=True, key="cc_clean_misc"):
                with st.spinner("Cleaning misc..."):
                    freed = 0
                    for cleaner in [
                        lambda: cc.clean_dir("cache"),
                        lambda: cc.clean_dir("statsig"),
                        lambda: cc.clean_dir("ide"),
                        lambda: cc.clean_dir("telemetry"),
                        cc.clean_stale_files,
                    ]:
                        success, _, details = cleaner()
                        if success:
                            freed += details.get("size_freed_mb", 0)
                if freed > 0:
                    st.success(f"Freed {freed:.1f} MB")
                    invalidate()
                    st.rerun()
                else:
                    st.info("Nothing to clean")


def _ph_check_key(name: str) -> str:
    """Stable widget key for a project history checkbox."""
    return f"ph_check_{name}"


def _get_selected_projects(projects: list[dict[str, Any]]) -> list[str]:
    """Read selected project names from checkbox widget keys."""
    return [p["name"] for p in projects if st.session_state.get(_ph_check_key(p["name"]), False)]


def _render_project_history_cleanup(app: AppComponents) -> None:
    """Project history cleanup section (absorbed from Claude Config page)."""
    projects = app.claude_config.list_projects()
    if not projects:
        st.info("No project histories found")
        return

    selected = _get_selected_projects(projects)

    # Action buttons
    acol1, acol2, acol3, acol4 = st.columns(4)
    with acol1:
        if st.button("Select All", use_container_width=True, type="primary", key="ph_sel_all"):
            for p in projects:
                st.session_state[_ph_check_key(p["name"])] = True
            st.rerun()
    with acol2:
        if st.button("Deselect All", use_container_width=True, key="ph_desel_all"):
            for p in projects:
                st.session_state[_ph_check_key(p["name"])] = False
            st.rerun()
    with acol3:
        top_n = st.selectbox("Top N", [5, 10, 20], index=0, key="ph_top_n")
        if st.button(f"Top {top_n} Largest", use_container_width=True, key="ph_top_n_btn"):
            for p in projects:
                st.session_state[_ph_check_key(p["name"])] = False
            for p in projects[:top_n]:
                st.session_state[_ph_check_key(p["name"])] = True
            st.rerun()
    with acol4:
        keep_n = st.number_input("Keep N", min_value=1, max_value=10, value=3, key="ph_keep_n")
        if st.button(
            f"Keep Last {keep_n}", use_container_width=True, type="primary", disabled=not selected, key="ph_keep_btn"
        ):
            with st.spinner(f"Keeping last {keep_n}..."):
                total_deleted = 0
                total_freed = 0
                for pname in selected:
                    proj = next(p for p in projects if p["name"] == pname)
                    success, _message, details = app.claude_config.keep_last_n_conversations(proj["path"], keep_n)
                    if success and details.get("deleted", 0) > 0:
                        total_deleted += details["deleted"]
                        total_freed += details["size_freed_mb"]
            if total_deleted > 0:
                st.success(f"Freed {total_freed:.1f} MB ({total_deleted} conversations)")
                invalidate()
                st.rerun()
            else:
                st.info("Nothing to delete")

    # Age-based cleanup row
    age_col1, age_col2 = st.columns([1, 3])
    with age_col1:
        max_age = st.selectbox(
            "Older than", [7, 14, 30, 60, 90], index=0, key="ph_max_age", format_func=lambda x: f"{x} days"
        )
    with age_col2:
        st.markdown('<div class="btn-align"></div>', unsafe_allow_html=True)
        scope = "selected" if selected else "all"
        if st.button(f"Clean >{max_age}d ({scope} projects)", use_container_width=True, key="ph_age_clean_btn"):
            with st.spinner(f"Cleaning conversations older than {max_age} days..."):
                if selected:
                    total_deleted = 0
                    total_freed = 0
                    projects_cleaned = 0
                    for pname in selected:
                        proj = next(p for p in projects if p["name"] == pname)
                        success, _message, details = app.claude_config.clean_old_conversations(proj["path"], max_age)
                        if success and details.get("deleted", 0) > 0:
                            projects_cleaned += 1
                            total_deleted += details["deleted"]
                            total_freed += details["size_freed_mb"]
                    if total_deleted > 0:
                        st.success(
                            f"Freed {total_freed:.1f} MB ({total_deleted} items from {projects_cleaned} projects)"
                        )
                        invalidate()
                        st.rerun()
                    else:
                        st.info(f"No conversations older than {max_age} days in selected projects")
                else:
                    success, _message, details = app.claude_config.clean_old_conversations_all_projects(max_age)
                    if success and details.get("conversations_deleted", 0) > 0:
                        st.success(
                            f"Freed {details['size_freed_mb']:.1f} MB ({details['conversations_deleted']} items from {details['projects_cleaned']} projects)"
                        )
                        invalidate()
                        st.rerun()
                    else:
                        st.info(f"No conversations older than {max_age} days")

    if selected:
        sel_size = sum(p["size_mb"] for p in projects if p["name"] in selected)
        st.info(f"Selected {len(selected)} project(s) — {sel_size:.1f} MB")

    # Project table
    for project in projects[:20]:
        col_check, col_path, col_size, col_conv, col_date = st.columns([0.3, 4, 0.8, 0.8, 1.2])
        with col_check:
            st.checkbox(
                "sel",
                key=_ph_check_key(project["name"]),
                label_visibility="collapsed",
            )
        with col_path:
            full_path = project.get("original_path", project["path"])
            parts = full_path.rsplit("/", 1)
            if len(parts) == 2:
                st.markdown(
                    f'<span class="mono-text">{parts[0]}/</span>'
                    f'<span style="color:#22c55e;font-family:monospace;font-weight:bold">{parts[1]}</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f'<span class="mono-text">{full_path}</span>', unsafe_allow_html=True)
        with col_size:
            st.text(f"{project['size_mb']} MB")
        with col_conv:
            st.text(f"{project['conversation_count']} conv")
        with col_date:
            st.text(project["last_modified"].strftime("%Y-%m-%d"))

    if len(projects) > 20:
        st.info(f"Showing top 20 of {len(projects)} projects")

    # Delete selected
    selected = _get_selected_projects(projects)
    if selected:
        st.markdown("---")
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            if st.button("Export Selected", use_container_width=True, key="ph_export"):
                with st.spinner("Exporting..."):
                    exported = []
                    for pname in selected:
                        proj = next(p for p in projects if p["name"] == pname)
                        success, _message = app.claude_config.export_project(proj["path"])
                        if success:
                            exported.append(pname)
                if exported:
                    st.success(f"Exported {len(exported)} project(s)")
        with dcol2:
            if danger_button("Delete Selected", key="ph_delete", disabled=not selected):
                _show_project_delete_dialog(app, projects, selected)

    # Recommendations
    old_threshold = datetime.now() - timedelta(days=90)
    old_projects = [p for p in projects if p["last_modified"] < old_threshold]
    if old_projects:
        old_size = sum(p["size_mb"] for p in old_projects)
        st.info(f"{len(old_projects)} projects older than 90 days ({old_size:.1f} MB)")


@st.dialog("Confirm Delete Projects")
def _show_project_delete_dialog(app: AppComponents, projects: list[dict[str, Any]], selected: list[str]) -> None:
    """Confirm deletion of selected project histories."""
    selected_size = sum(p["size_mb"] for p in projects if p["name"] in selected)
    st.markdown(
        f'<div class="health-alert health-alert-critical">'
        f"Deleting {len(selected)} project(s) ({selected_size:.1f} MB). This is permanent."
        f"</div>",
        unsafe_allow_html=True,
    )

    confirm = st.checkbox("I understand this is permanent", key="ph_del_confirm_check")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<div class="danger-btn">', unsafe_allow_html=True)
        if st.button("Delete", disabled=not confirm, use_container_width=True, key="ph_del_confirm_btn"):
            with st.spinner("Deleting..."):
                paths = [p["path"] for p in projects if p["name"] in selected]
                success, message, _details = app.claude_config.delete_projects(paths, create_backup=True)
            if success:
                st.success(message)
                for p in projects:
                    st.session_state[_ph_check_key(p["name"])] = False
                invalidate()
                st.rerun()
            else:
                st.error(message)
        st.markdown("</div>", unsafe_allow_html=True)
    with col2:
        if st.button("Cancel", use_container_width=True, key="ph_del_cancel_btn"):
            st.rerun()


def _render_mcp_servers(app: AppComponents) -> None:
    """MCP server management section."""
    success, servers, error = app.claude_config.get_mcp_servers()
    if not success:
        st.error(f"Error reading MCP config: {error}")
        return

    if not servers:
        st.info("No MCP servers configured")
    else:
        st.text(f"Configured servers: {len(servers)}")
        for server in servers:
            with st.expander(f"{server['name']}" + (" (disabled)" if server.get("disabled") else "")):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f'<span class="mono-text">Command: {server["command"]}</span>', unsafe_allow_html=True)
                    if server.get("args"):
                        st.markdown(
                            f'<span class="mono-text">Args: {" ".join(server["args"])}</span>', unsafe_allow_html=True
                        )
                    if server.get("env"):
                        st.text("Environment:")
                        secret_hints = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
                        for key, value in server["env"].items():
                            if any(hint in key.upper() for hint in secret_hints):
                                masked = value[:4] + "***" if len(value) > 4 else "***"
                                st.text(f"  {key}={masked}")
                            else:
                                st.text(f"  {key}={value}")
                with col2:
                    if danger_button("Delete", key=f"mcp_del_{server['name']}"):
                        with st.spinner(f"Deleting {server['name']}..."):
                            ok, err = app.claude_config.delete_mcp_server(server["name"])
                        if ok:
                            st.success(f"Deleted {server['name']}")
                            invalidate()
                            st.rerun()
                        else:
                            st.error(err)

    st.markdown("---")
    st.markdown('<div class="section-header">Add MCP Server</div>', unsafe_allow_html=True)

    with st.form("add_mcp_server_form"):
        fcol1, fcol2 = st.columns(2)
        with fcol1:
            server_name = st.text_input("Server Name", placeholder="my-mcp-server")
            server_command = st.text_input("Command", placeholder="node")
        with fcol2:
            server_args = st.text_input("Arguments (space-separated)", placeholder="/path/to/server.js")
            server_env = st.text_area("Environment (KEY=VALUE per line)", placeholder="API_KEY=key\nENV=prod")

        if st.form_submit_button("Add Server"):
            if server_name and server_command:
                args = server_args.split() if server_args else []
                env = {}
                if server_env:
                    for line in server_env.strip().split("\n"):
                        if "=" in line:
                            k, v = line.split("=", 1)
                            env[k.strip()] = v.strip()
                ok, err = app.claude_config.add_mcp_server(server_name, server_command, args, env)
                if ok:
                    st.success(f"Added '{server_name}'")
                    invalidate()
                    st.rerun()
                else:
                    st.error(err)
            else:
                st.error("Server name and command are required")


# ═══════════════════════════════════════════════════════════════════════
# Tab 3: Retention Policy
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
