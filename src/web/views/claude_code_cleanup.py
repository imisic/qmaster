"""Claude Code page — config cleanup and project history sections."""

import html as html_mod
import logging
from datetime import datetime, timedelta
from typing import Any

import streamlit as st

from web.cache import (
    get_misc_claude_stats,
    get_orphan_projects_stats,
    get_subagent_stats,
    invalidate,
    list_claude_projects,
)
from web.components.action_bar import danger_button
from web.state import AppComponents


# ───────────────────────────────────────────────────────────────────────
# Config Cleanup tab
# ───────────────────────────────────────────────────────────────────────


def render_config_tab(app: AppComponents, stats: dict[str, Any], binaries_stats: dict[str, Any]) -> None:
    """Claude config cleanup tab."""
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
    st.markdown('<div class="section-header">Category Cleanup</div>', unsafe_allow_html=True)

    categories = _get_claude_categories(app)
    if not categories:
        st.info("All categories are empty")
    else:
        for cat in categories:
            if cat["size_mb"] > 0:
                _render_category_row(cat)

    st.markdown("---")
    _render_misc_cleanup(app)

    st.markdown("---")
    _render_advanced_cleanup(app)

    st.markdown("---")
    st.markdown(
        '<div class="health-alert">'
        "Do NOT delete: plugins/ (MCP servers), settings.json (permissions), CLAUDE.md (instructions), mcp.json (MCP config)"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_advanced_cleanup(app: AppComponents) -> None:
    """Subagent logs, orphaned projects, misc small caches."""
    st.markdown('<div class="section-header">Advanced Cleanup</div>', unsafe_allow_html=True)

    cc = app.claude_config

    # ── Subagent logs ────────────────────────────────────────────────
    age_col, sa_info, sa_btn = st.columns([1, 2, 1])
    with age_col:
        max_age = st.selectbox(
            "Sessions older than",
            [7, 14, 30, 60, 90],
            index=2,
            key="sa_max_age",
            format_func=lambda x: f"{x} days",
        )
    sa_stats = get_subagent_stats(cc, max_age)
    with sa_info:
        total_mb = sa_stats.get("total_size_mb", 0)
        old_mb = sa_stats.get("old", {}).get("size_mb", 0)
        old_sessions = sa_stats.get("old", {}).get("session_count", 0)
        total_sessions = sa_stats.get("session_count", 0)
        st.markdown("**Subagent Logs**")
        st.text(
            f"Total: {total_mb:.1f} MB across {total_sessions} sessions  ·  "
            f"Eligible (>{max_age}d): {old_mb:.1f} MB in {old_sessions} sessions"
        )
        st.caption(
            "Per-session audit logs CC never reads back. Safe to delete for old sessions; "
            "active/recent sessions stay untouched."
        )
    with sa_btn:
        disabled = old_sessions == 0
        if st.button("Clean Subagents", use_container_width=True, key="sa_clean_btn", disabled=disabled):
            with st.spinner("Deleting old subagent logs..."):
                success, message, details = cc.clean_subagent_logs(max_age_days=max_age)
            if success and details.get("size_freed_mb", 0) > 0:
                st.success(f"Freed {details['size_freed_mb']:.1f} MB ({details['deleted']} sessions)")
                invalidate()
                st.rerun()
            elif success:
                st.info("Nothing to clean")
            else:
                st.error(message)

    st.markdown("")

    # ── Orphaned project caches ──────────────────────────────────────
    orphan_info, orphan_btn = st.columns([3, 1])
    orphan_stats = get_orphan_projects_stats(cc)
    with orphan_info:
        st.markdown("**Orphaned Project Caches**")
        if orphan_stats["exists"]:
            st.text(f"{orphan_stats['count']} orphans  ·  {orphan_stats['total_size_mb']:.2f} MB")
            for o in orphan_stats["projects"][:5]:
                st.markdown(
                    f'<span class="mono-text" style="color:#a1a7b5">↳ {html_mod.escape(o["guessed_path"])}'
                    f"  ({o['size_mb']:.2f} MB)</span>",
                    unsafe_allow_html=True,
                )
            if orphan_stats["count"] > 5:
                st.caption(f"...and {orphan_stats['count'] - 5} more")
        else:
            st.text("None found")
        st.caption(
            "Project caches whose real working directory no longer exists on disk "
            "(moved, renamed, or deleted projects — plus cleaned-up git worktrees)."
        )
    with orphan_btn:
        if st.button(
            "Clean Orphans",
            use_container_width=True,
            key="orphan_clean_btn",
            disabled=not orphan_stats["exists"],
        ):
            with st.spinner("Deleting orphaned caches..."):
                success, message, details = cc.clean_orphan_projects()
            if success and details.get("deleted", 0) > 0:
                st.success(f"Deleted {details['deleted']} orphan(s), freed {details['size_freed_mb']:.2f} MB")
                invalidate()
                st.rerun()
            elif success:
                st.info("Nothing to clean")
            else:
                st.error(message)

    st.markdown("")

    # ── Misc small caches ────────────────────────────────────────────
    misc_info, misc_btn = st.columns([3, 1])
    misc_stats = get_misc_claude_stats(cc)
    with misc_info:
        st.markdown("**Misc Small Caches**")
        if misc_stats["exists"]:
            names = ", ".join(i["name"] for i in misc_stats["items"][:6])
            if len(misc_stats["items"]) > 6:
                names += f", +{len(misc_stats['items']) - 6} more"
            st.text(f"{misc_stats['item_count']} items  ·  {misc_stats['total_size_mb']:.2f} MB")
            st.caption(f"Bundled: {names}")
        else:
            st.text("None found")
        st.caption(
            "Bundled cleanup: usage-data, backups, sessions, teams, reports, "
            "security_warnings_state_*.json, mcp.json.backup."
        )
    with misc_btn:
        if st.button(
            "Clean Misc",
            use_container_width=True,
            key="misc_claude_clean_btn",
            disabled=not misc_stats["exists"],
        ):
            with st.spinner("Deleting misc caches..."):
                success, message, details = cc.clean_misc_claude()
            if success and details.get("deleted", 0) > 0:
                st.success(f"Deleted {details['deleted']} item(s), freed {details['size_freed_mb']:.2f} MB")
                invalidate()
                st.rerun()
            elif success:
                st.info("Nothing to clean")
            else:
                st.error(message)


def _get_claude_categories(app: AppComponents) -> list[dict[str, Any]]:
    """Collect stats for all Claude config categories."""
    cc = app.claude_config
    categories = []

    def _add(name: str, stats_fn, clean_fn, has_age: bool = True) -> None:
        try:
            s = stats_fn()
        except (OSError, PermissionError) as e:
            logging.warning("Could not get stats for '%s': %s", name, e)
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

    if misc_total <= 0:
        return

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
                    success, message, details = cleaner()
                    if success:
                        freed += details.get("size_freed_mb", 0)
                    else:
                        logging.warning("Misc cleaner failed: %s", message)
            if freed > 0:
                st.success(f"Freed {freed:.1f} MB")
                invalidate()
                st.rerun()
            else:
                st.info("Nothing to clean")


# ───────────────────────────────────────────────────────────────────────
# Project History tab
# ───────────────────────────────────────────────────────────────────────


def _ph_check_key(name: str) -> str:
    """Stable widget key for a project history checkbox."""
    return f"ph_check_{name}"


def _get_selected_projects(projects: list[dict[str, Any]]) -> list[str]:
    """Read selected project names from checkbox widget keys."""
    return [p["name"] for p in projects if st.session_state.get(_ph_check_key(p["name"]), False)]


def render_project_history_tab(app: AppComponents) -> None:
    """Project history cleanup — selectable per-project conversation pruning."""
    projects = list_claude_projects(app.claude_config)
    if not projects:
        st.info("No project histories found")
        return

    by_name = {p["name"]: p for p in projects}
    selected = _get_selected_projects(projects)

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
                total_freed = 0.0
                for pname in selected:
                    proj = by_name.get(pname)
                    if not proj:
                        continue
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

    age_col1, age_col2 = st.columns([1, 3])
    with age_col1:
        max_age = st.selectbox(
            "Older than", [7, 14, 30, 60, 90], index=0, key="ph_max_age", format_func=lambda x: f"{x} days"
        )
    with age_col2:
        st.markdown('<div class="btn-align"></div>', unsafe_allow_html=True)
        scope = "selected" if selected else "all"
        if st.button(f"Clean >{max_age}d ({scope} projects)", use_container_width=True, key="ph_age_clean_btn"):
            _clean_old_conversations(app, projects, by_name, selected, max_age)

    if selected:
        sel_size = sum(p["size_mb"] for p in projects if p["name"] in selected)
        st.info(f"Selected {len(selected)} project(s) — {sel_size:.1f} MB")

    for project in projects[:20]:
        col_check, col_path, col_size, col_conv, col_date = st.columns([0.3, 4, 0.8, 0.8, 1.2])
        with col_check:
            st.checkbox("sel", key=_ph_check_key(project["name"]), label_visibility="collapsed")
        with col_path:
            full_path = project.get("original_path", project["path"])
            parts = full_path.rsplit("/", 1)
            if len(parts) == 2:
                st.markdown(
                    f'<span class="mono-text">{html_mod.escape(parts[0])}/</span>'
                    f'<span style="color:#22c55e;font-family:monospace;font-weight:bold">{html_mod.escape(parts[1])}</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f'<span class="mono-text">{html_mod.escape(full_path)}</span>', unsafe_allow_html=True)
        with col_size:
            st.text(f"{project['size_mb']} MB")
        with col_conv:
            st.text(f"{project['conversation_count']} conv")
        with col_date:
            st.text(project["last_modified"].strftime("%Y-%m-%d"))

    if len(projects) > 20:
        st.info(f"Showing top 20 of {len(projects)} projects")

    selected = _get_selected_projects(projects)
    if selected:
        st.markdown("---")
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            if st.button("Export Selected", use_container_width=True, key="ph_export"):
                with st.spinner("Exporting..."):
                    exported = []
                    for pname in selected:
                        proj = by_name.get(pname)
                        if not proj:
                            continue
                        success, _message = app.claude_config.export_project(proj["path"])
                        if success:
                            exported.append(pname)
                if exported:
                    st.success(f"Exported {len(exported)} project(s)")
        with dcol2:
            if danger_button("Delete Selected", key="ph_delete", disabled=not selected):
                _show_project_delete_dialog(app, projects, selected)

    old_threshold = datetime.now() - timedelta(days=90)
    old_projects = [p for p in projects if p["last_modified"] < old_threshold]
    if old_projects:
        old_size = sum(p["size_mb"] for p in old_projects)
        st.info(f"{len(old_projects)} projects older than 90 days ({old_size:.1f} MB)")


def _clean_old_conversations(
    app: AppComponents,
    projects: list[dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    selected: list[str],
    max_age: int,
) -> None:
    """Run conversation cleanup against either selected or all projects."""
    with st.spinner(f"Cleaning conversations older than {max_age} days..."):
        if selected:
            total_deleted = 0
            total_freed = 0.0
            projects_cleaned = 0
            for pname in selected:
                proj = by_name.get(pname)
                if not proj:
                    continue
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
                    f"Freed {details['size_freed_mb']:.1f} MB "
                    f"({details['conversations_deleted']} items from {details['projects_cleaned']} projects)"
                )
                invalidate()
                st.rerun()
            else:
                st.info(f"No conversations older than {max_age} days")


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
