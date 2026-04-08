"""Claude Code page — config cleanup and project history sections."""

import html as html_mod
import logging
from collections.abc import Callable
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
from web.components import section, show_confirm
from web.components.action_bar import danger_button
from web.state import AppComponents
from web.theme import COLORS


def _run_cleanup_action(
    label: str,
    fn: Callable[[], tuple[bool, str, dict]],
    *,
    success_msg_template: str = "Freed {freed:.1f} MB",
) -> None:
    """Run a cleanup function with a spinner and standard success/error UX."""
    with st.spinner(f"Cleaning {label}..."):
        success, message, details = fn()
    if not success:
        st.error(message)
        return
    details = details if isinstance(details, dict) else {}
    freed = details.get("size_freed_mb", 0)
    deleted = details.get("deleted", 0)
    if freed > 0 or deleted > 0 or details.get("total_freed_mb", 0) > 0:
        st.success(success_msg_template.format(freed=freed, deleted=deleted))
        invalidate()
        st.rerun()
    else:
        st.info("Nothing to clean")


# ───────────────────────────────────────────────────────────────────────
# Config Cleanup tab
# ───────────────────────────────────────────────────────────────────────


def render_config_tab(app: AppComponents, stats: dict[str, Any], binaries_stats: dict[str, Any]) -> None:
    """Claude config cleanup tab."""
    # ── Bulk actions row — single primary, demote the rest ───────────
    qcol1, qcol2, qcol3 = st.columns([2, 2, 2])
    with qcol1:
        if st.button("Clean All (except projects)", type="primary", use_container_width=True, key="cc_clean_all"):
            _open_clean_all_confirm(app, stats)
    with qcol2:
        binaries_active = binaries_stats.get("exists") and binaries_stats.get("version_count", 0) > 1
        if st.button(
            "Clean Old Binaries",
            use_container_width=True,
            key="cc_clean_binaries",
            disabled=not binaries_active,
        ):
            _open_clean_binaries_confirm(app, binaries_stats)
    with qcol3:
        if binaries_stats.get("exists"):
            st.caption(
                f"Binaries: **{binaries_stats['total_size_mb']} MB** "
                f"({binaries_stats['version_count']} version(s))"
            )

    st.divider()
    section("Category Cleanup")

    categories = _get_claude_categories(app)
    if not categories:
        st.info("All categories are empty")
    else:
        for cat in categories:
            if cat["size_mb"] > 0:
                _render_category_row(cat)

    st.divider()
    _render_misc_cleanup(app)

    st.divider()
    _render_advanced_cleanup(app)

    st.divider()
    st.markdown(
        '<div class="health-alert">'
        "Do NOT delete: plugins/ (MCP servers), settings.json (permissions), CLAUDE.md (instructions), mcp.json (MCP config)"
        "</div>",
        unsafe_allow_html=True,
    )


def _open_clean_all_confirm(app: AppComponents, stats: dict[str, Any]) -> None:
    """Confirm dialog for the big 'Clean All except projects' action."""
    total_mb = stats.get("total_size_mb", 0)

    def _on_confirm() -> None:
        _run_cleanup_action(
            "all categories",
            lambda: app.claude_config.clean_all(keep_projects=True),
            success_msg_template="Freed {freed:.1f} MB",
        )

    show_confirm(
        title="Confirm Clean All",
        warning=(
            "This will permanently delete data from **all Claude Code categories** "
            "(shell snapshots, todos, debug logs, file history, command history, image cache, "
            "plans, paste cache, tasks, plugins cache, statsig, ide, telemetry, stale files).\n\n"
            f"Project session history will be **preserved**. Current Claude config size on disk: "
            f"~{total_mb} MB.\n\n"
            "This cannot be undone."
        ),
        confirm_label="Clean All",
        on_confirm=_on_confirm,
        key_prefix="cc_clean_all_dlg",
    )


def _open_clean_binaries_confirm(app: AppComponents, binaries_stats: dict[str, Any]) -> None:
    """Confirm dialog for removing old Claude Code binary versions."""
    total = binaries_stats.get("total_size_mb", 0)
    versions = binaries_stats.get("version_count", 0)

    def _on_confirm() -> None:
        _run_cleanup_action(
            "old binaries",
            lambda: app.claude_config.clean_old_binaries(),
        )

    show_confirm(
        title="Confirm Clean Old Binaries",
        warning=(
            f"This will remove all but the most recent Claude Code binary version. "
            f"Currently: **{versions} version(s)** installed (~{total} MB total). "
            "The active version stays."
        ),
        confirm_label="Remove Old Versions",
        on_confirm=_on_confirm,
        key_prefix="cc_clean_bin_dlg",
    )


def _render_advanced_cleanup(app: AppComponents) -> None:
    """Subagent logs, orphaned projects, misc small caches."""
    section("Advanced Cleanup")

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
    total_mb = sa_stats.get("total_size_mb", 0)
    old_mb = sa_stats.get("old", {}).get("size_mb", 0)
    old_sessions = sa_stats.get("old", {}).get("session_count", 0)
    total_sessions = sa_stats.get("session_count", 0)
    with sa_info:
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
        if st.button(
            "Clean Subagents",
            use_container_width=True,
            key="sa_clean_btn",
            disabled=old_sessions == 0,
        ):
            show_confirm(
                title="Confirm Clean Subagent Logs",
                warning=(
                    f"This will delete subagent audit logs from **{old_sessions}** session(s) "
                    f"older than **{max_age} days** (~**{old_mb:.1f} MB**). Recent sessions are kept.\n\n"
                    "This cannot be undone."
                ),
                confirm_label="Clean",
                on_confirm=lambda: _run_cleanup_action(
                    "subagent logs",
                    lambda: cc.clean_subagent_logs(max_age_days=max_age),
                ),
                key_prefix="sa_clean_dlg",
            )

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
                    f'<span class="mono-text" style="color:{COLORS["text_muted"]}">↳ {html_mod.escape(o["guessed_path"])}'
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
            show_confirm(
                title="Confirm Clean Orphaned Caches",
                warning=(
                    f"This will delete **{orphan_stats['count']}** orphaned project cache(s) "
                    f"(~**{orphan_stats['total_size_mb']:.2f} MB**) whose source directories "
                    "no longer exist on disk.\n\nThis cannot be undone."
                ),
                confirm_label="Clean",
                on_confirm=lambda: _run_cleanup_action("orphans", cc.clean_orphan_projects),
                key_prefix="orphan_clean_dlg",
            )

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
            show_confirm(
                title="Confirm Clean Misc Caches",
                warning=(
                    f"This will delete **{misc_stats['item_count']}** misc cache item(s) "
                    f"(~**{misc_stats['total_size_mb']:.2f} MB**): usage-data, backups, sessions, "
                    "teams, reports, security warnings, mcp.json backups.\n\nThis cannot be undone."
                ),
                confirm_label="Clean",
                on_confirm=lambda: _run_cleanup_action("misc caches", cc.clean_misc_claude),
                key_prefix="misc_clean_dlg",
            )


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
    """Render a single cleanup category row with a confirm-gated Clean button."""
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
            _open_category_confirm(cat)


def _open_category_confirm(cat: dict[str, Any]) -> None:
    """Confirm dialog for a single Category Cleanup row."""

    def _on_confirm() -> None:
        if cat["has_age"]:
            result = cat["clean_fn"](None)
        else:
            result = cat["clean_fn"]()
        success, message, details = result
        if success and details.get("size_freed_mb", 0) > 0:
            st.success(f"Freed {details['size_freed_mb']:.1f} MB")
            invalidate()
            st.rerun()
        elif success:
            st.info("Nothing to clean")
        else:
            st.error(message)

    file_str = f" ({cat['file_count']} files)" if cat.get("file_count") else ""
    show_confirm(
        title=f"Confirm Clean — {cat['name']}",
        warning=(
            f"This will permanently delete the **{cat['name']}** data: "
            f"~**{cat['size_mb']:.1f} MB**{file_str}.\n\n"
            "This cannot be undone."
        ),
        confirm_label="Clean",
        on_confirm=_on_confirm,
        key_prefix=f"cc_cat_dlg_{cat['name'].lower().replace(' ', '_')}",
    )


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
            _open_clean_all_misc_confirm(cc, misc_total, parts)


def _open_clean_all_misc_confirm(cc: Any, misc_total: float, parts: list[str]) -> None:
    """Confirm dialog for the bundled misc cleanup (cache/statsig/ide/telemetry/stale)."""

    def _on_confirm() -> None:
        with st.spinner("Cleaning misc..."):
            freed = 0.0
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

    detail = ", ".join(parts) if parts else "—"
    show_confirm(
        title="Confirm Clean All Misc",
        warning=(
            f"This will delete bundled misc data: ~**{misc_total:.1f} MB** total ({detail}).\n\n"
            "This cannot be undone."
        ),
        confirm_label="Clean",
        on_confirm=_on_confirm,
        key_prefix="cc_clean_misc_dlg",
    )


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

    # Selection toolbar — none of these are destructive, so none should be primary
    acol1, acol2, acol3, acol4 = st.columns(4)
    with acol1:
        if st.button("Select All", use_container_width=True, key="ph_sel_all"):
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
            f"Keep Last {keep_n}",
            use_container_width=True,
            disabled=not selected,
            key="ph_keep_btn",
        ):
            _open_keep_last_confirm(app, by_name, selected, int(keep_n))

    age_col1, age_col2 = st.columns([1, 3])
    with age_col1:
        max_age = st.selectbox(
            "Older than", [7, 14, 30, 60, 90], index=0, key="ph_max_age", format_func=lambda x: f"{x} days"
        )
    with age_col2:
        st.write("")  # baseline align with the selectbox above
        scope = "selected" if selected else "all"
        if st.button(f"Clean >{max_age}d ({scope} projects)", use_container_width=True, key="ph_age_clean_btn"):
            _open_clean_old_confirm(app, projects, by_name, selected, int(max_age))

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
                    f'<span style="color:{COLORS["accent_green"]};font-family:monospace;font-weight:600">'
                    f"{html_mod.escape(parts[1])}</span>",
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
        st.divider()
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
                _open_project_delete_confirm(app, projects, selected)

    old_threshold = datetime.now() - timedelta(days=90)
    old_projects = [p for p in projects if p["last_modified"] < old_threshold]
    if old_projects:
        old_size = sum(p["size_mb"] for p in old_projects)
        st.info(f"{len(old_projects)} projects older than 90 days ({old_size:.1f} MB)")


def _open_keep_last_confirm(
    app: AppComponents,
    by_name: dict[str, dict[str, Any]],
    selected: list[str],
    keep_n: int,
) -> None:
    """Confirm dialog before trimming each selected project to its last N conversations."""
    sel_size = sum(p["size_mb"] for p in by_name.values() if p["name"] in selected)

    def _on_confirm() -> None:
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

    show_confirm(
        title="Confirm Keep Last N",
        warning=(
            f"This will permanently delete every conversation beyond the **most recent "
            f"{keep_n}** in each of **{len(selected)}** selected project(s) "
            f"(~{sel_size:.1f} MB total).\n\nThis cannot be undone."
        ),
        confirm_label=f"Trim to last {keep_n}",
        on_confirm=_on_confirm,
        key_prefix="ph_keep_dlg",
    )


def _open_clean_old_confirm(
    app: AppComponents,
    projects: list[dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    selected: list[str],
    max_age: int,
) -> None:
    """Confirm dialog before deleting conversations older than N days."""
    scope_label = f"{len(selected)} selected project(s)" if selected else "all projects"

    def _on_confirm() -> None:
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

    show_confirm(
        title=f"Confirm Clean >{max_age}d",
        warning=(
            f"This will permanently delete all conversations older than **{max_age} days** "
            f"across **{scope_label}**.\n\nThis cannot be undone."
        ),
        confirm_label="Clean",
        on_confirm=_on_confirm,
        key_prefix="ph_age_dlg",
    )


def _open_project_delete_confirm(
    app: AppComponents,
    projects: list[dict[str, Any]],
    selected: list[str],
) -> None:
    """Confirm dialog before deleting selected project history dirs entirely."""
    selected_size = sum(p["size_mb"] for p in projects if p["name"] in selected)

    def _on_confirm() -> None:
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

    show_confirm(
        title="Confirm Delete Projects",
        warning=(
            f"This will permanently delete **{len(selected)} project(s)** "
            f"(~**{selected_size:.1f} MB**) from your Claude Code history. "
            "A backup is automatically created before deletion.\n\n"
            "This cannot be undone."
        ),
        confirm_label=f"Delete {len(selected)} project(s)",
        on_confirm=_on_confirm,
        key_prefix="ph_del_dlg",
    )
