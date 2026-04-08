"""Projects page - per-project management, git, backup/restore."""

import html
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from web.cache import get_backup_status, get_git_status, invalidate
from web.cache import is_git_repo as _is_git_repo
from web.components import (
    Action,
    Metric,
    action_bar,
    backup_table,
    defaults_expander,
    empty_state,
    health_label,
    health_level,
    item_heading,
    item_picker,
    metrics_grid,
    page_header,
    relative_time,
    restore_section,
    section,
    show_confirm,
    status_badge,
    type_badge,
)
from web.state import AppComponents


# ── Entry Point ──────────────────────────────────────────────────────


def render_projects(app: AppComponents) -> None:
    """Render the Projects page."""
    page_header(
        "Projects",
        "Folders Quartermaster backs up. Each has its own history, optional git tracking, and schedule.",
    )

    defaults_expander(
        "Project Defaults",
        "These defaults apply to new projects unless overridden.",
        [
            ("Schedule", app.config.get_setting("defaults.project.schedule", "daily")),
            ("Retention", f"{app.config.get_setting('defaults.project.retention_days', 30)}d"),
            ("Time", app.config.get_setting("defaults.project.time", "02:00")),
        ],
    )

    projects = app.config.get_all_projects()

    if not projects:
        empty_state("No projects configured", "Add a project to get started")
        if st.button("Add Project", type="primary", key="proj_add_empty"):
            _show_add_project_dialog(app)
        return

    # Top bar: Add + Backup All — constrained to 1/3 width so the buttons
    # sit together as a toolbar instead of being spread with an empty gap.
    toolbar_col, _rest = st.columns([2, 4])
    with toolbar_col:
        tb1, tb2 = st.columns(2)
        with tb1:
            if st.button("+ Add Project", use_container_width=True, key="proj_add_btn"):
                _show_add_project_dialog(app)
        with tb2:
            if st.button("Backup All", use_container_width=True, key="proj_backup_all"):
                task_id = app.bg_backup.schedule_backup("all-projects", "all")
                st.success(f"Started in background ({task_id[:8]}...)")

    # Project picker: pills when few, dropdown when many (auto at >6)
    project_names = list(projects.keys())
    selected_name = item_picker("Project", project_names, key="proj_selector") or project_names[0]

    st.divider()

    project = projects[selected_name]
    status = get_backup_status(app.backup_engine, "project", selected_name)
    is_git_repo = _is_git_repo(app.git_manager, project["path"])

    _render_project_header(app, selected_name, project, status, is_git_repo)
    st.divider()
    _render_project_actions(app, selected_name, project, is_git_repo)

    # Tabs: Backups (unified), Git, Configuration
    tab_labels = ["Backups"]
    if is_git_repo:
        tab_labels.append("Git")
    tab_labels.append("Configuration")
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        _render_backups_tab(app, selected_name, project, status, is_git_repo)

    if is_git_repo:
        with tabs[1]:
            _render_git_tab(app, selected_name, project)

    with tabs[-1]:
        _render_project_config(project)


# ── Header Block ─────────────────────────────────────────────────────


def _render_project_header(
    app: AppComponents,
    name: str,
    project: dict[str, Any],
    status: dict[str, Any],
    is_git_repo: bool,
) -> None:
    """Two-column header: info on the left, metrics grid on the right."""
    info_col, stats_col = st.columns([3, 2])

    with info_col:
        item_heading(name)
        st.markdown(
            f'<span class="project-path">{html.escape(str(project["path"]))}</span>',
            unsafe_allow_html=True,
        )
        badge_html = type_badge(project.get("type", "unknown"), "project")
        desc = html.escape(project.get("description", ""))
        st.markdown(f"{badge_html}&nbsp;&nbsp;{desc}", unsafe_allow_html=True)

        if is_git_repo and project.get("git", {}).get("track", False):
            git_status = get_git_status(app.git_manager, project["path"])
            if git_status.get("is_repo"):
                branch = git_status.get("branch", "unknown")
                if git_status.get("is_dirty"):
                    changes = git_status.get("total_changes", 0)
                    git_html = status_badge(f"{branch} ({changes} changes)", "warning")
                else:
                    git_html = status_badge(f"{branch} (clean)", "healthy")
                st.markdown(f"Git: {git_html}", unsafe_allow_html=True)

    with stats_col:
        _render_project_stats(status)


def _render_project_stats(status: dict[str, Any]) -> None:
    """Health + Backups + Size metric grid (or empty state)."""
    if not (status["exists"] and status.get("backup_count", 0) > 0):
        st.markdown(status_badge("No backups", "inactive"), unsafe_allow_html=True)
        return

    latest = status.get("latest_backup") or {}
    level = health_level(latest.get("modified"))
    metrics_grid(
        [
            Metric("Health", health_label(level), status_level=level),
            Metric("Backups", status["backup_count"]),
            Metric("Size", f"{status.get('total_size_mb', 0):.1f} MB"),
        ],
        max_columns=3,
    )


# ── Action Bar ───────────────────────────────────────────────────────


def _render_project_actions(
    app: AppComponents,
    name: str,
    project: dict[str, Any],
    is_git_repo: bool,
) -> None:
    """One primary action (Backup Now) + related secondary buttons."""

    def _backup(incremental: bool = False, complete: bool = False) -> None:
        if complete:
            label = f"Creating complete backup of {name}"
        elif incremental:
            label = f"Creating incremental backup of {name}"
        else:
            label = f"Backing up {name}"
        with st.spinner(f"{label}..."):
            if complete:
                success, message = app.backup_engine.backup_project_complete(name)
            else:
                success, message = app.backup_engine.backup_project(name, incremental=incremental)
        if success:
            st.success(message)
            invalidate()
            st.rerun()
        else:
            st.error(message)

    def _savepoint() -> None:
        with st.spinner("Creating savepoint..."):
            success, message = app.git_manager.create_savepoint(
                project["path"],
                f"Savepoint - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
        if success:
            st.success(message)
            invalidate()
            st.rerun()
        else:
            st.error(message)

    secondary: list[Action] = [
        Action("Incremental", f"proj_incr_{name}", lambda: _backup(incremental=True)),
        Action("Complete", f"proj_complete_{name}", lambda: _backup(complete=True)),
    ]
    if is_git_repo and project.get("git", {}).get("track", False):
        secondary.append(Action("Savepoint", f"proj_save_{name}", _savepoint))

    action_bar(
        primary=Action("Backup Now", f"proj_bk_{name}", lambda: _backup()),
        secondary=secondary,
    )


# ── Backups Tab (unified: regular + git bundles) ─────────────────────


def _render_backups_tab(
    app: AppComponents,
    name: str,
    project: dict[str, Any],
    status: dict[str, Any],
    is_git_repo: bool,
) -> None:
    """Unified backups tab with a source filter (tar.gz / git bundles)."""
    sources = ["Archives"]
    if is_git_repo:
        sources.append("Git Bundles")

    if len(sources) > 1:
        source = st.segmented_control(
            "Source",
            sources,
            default="Archives",
            key=f"proj_src_{name}",
            label_visibility="collapsed",
        )
        if not source:
            source = "Archives"
    else:
        source = "Archives"

    if source == "Archives":
        _render_archive_backups(app, name, status)
    else:
        _render_git_bundle_backups(app, name, project)


def _render_archive_backups(app: AppComponents, name: str, status: dict[str, Any]) -> None:
    """Regular tar.gz backup history + restore."""
    if not status["exists"] or not status.get("all_backups"):
        empty_state("No backups yet", "Create your first backup with the button above")
        return

    all_backups = status.get("all_backups", [])
    backup_table(all_backups, show_type=True, max_rows=10, key_prefix=f"proj_bt_{name}")

    restore_section(
        [b["name"] for b in all_backups],
        key_prefix=f"proj_{name}",
        on_restore=lambda file: _show_restore_dialog(app, name, file),
    )


def _render_git_bundle_backups(
    app: AppComponents,
    name: str,
    project: dict[str, Any],
) -> None:
    """Git bundle backups tab — portable .bundle snapshots of full repo history."""
    git_status = get_backup_status(app.backup_engine, "git", name)

    top_col, _spacer = st.columns([1, 3])
    with top_col:
        if st.button(
            "Create Git Backup",
            use_container_width=True,
            key=f"proj_git_backup_{name}",
        ):
            with st.spinner("Creating git bundle backup..."):
                success, message = app.backup_engine.backup_git(name)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)

    if not git_status["exists"] or not git_status.get("all_backups"):
        empty_state(
            "No git backups yet",
            "Portable bundle files containing your full git history. "
            "Create one to snapshot all commits and branches.",
        )
        return

    latest_modified = None
    if git_status["latest_backup"]:
        latest_modified = git_status["latest_backup"].get("modified", "")[:10]

    metrics_grid(
        [
            Metric("Bundles", git_status["backup_count"]),
            Metric("Total Size", f"{git_status.get('total_size_mb', 0):.1f} MB"),
            Metric("Latest", latest_modified or "—"),
        ],
        max_columns=3,
    )

    st.divider()

    all_backups = git_status.get("all_backups", [])
    if PANDAS_AVAILABLE:
        rows = [
            {
                "Name": b["name"],
                "Size (MB)": f"{b['size_mb']:.2f}",
                "Date": b["modified"][:19].replace("T", " "),
                "Age": relative_time(datetime.fromisoformat(b["modified"])),
            }
            for b in all_backups
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        for b in all_backups[:10]:
            st.text(f"{b['name']} - {b['size_mb']:.2f} MB - {b['modified'][:10]}")

    st.divider()
    section("Restore from Git Bundle")
    rcol1, rcol2, rcol3 = st.columns([2, 1, 1])
    with rcol1:
        restore_file = st.selectbox(
            "Select bundle",
            [b["name"] for b in all_backups],
            key=f"proj_git_restore_sel_{name}",
            label_visibility="collapsed",
        )
    with rcol2:
        restore_mode = st.selectbox(
            "Mode",
            ["clone", "fetch"],
            help="clone: create new repo from bundle, fetch: update existing repo",
            key=f"proj_git_restore_mode_{name}",
        )
    with rcol3:
        if st.button(
            "Restore",
            use_container_width=True,
            key=f"proj_git_restore_btn_{name}",
        ):
            _show_git_backup_restore_dialog(app, name, restore_file, restore_mode)


# ── Git Tab (commit history + actions) ───────────────────────────────


def _render_git_tab(app: AppComponents, name: str, project: dict[str, Any]) -> None:
    """Git commit history with per-commit actions."""
    commits = app.git_manager.get_commit_history(project["path"], limit=15)
    if not commits:
        empty_state("No commits found", "Create a savepoint to start tracking changes")
        return

    if PANDAS_AVAILABLE:
        rows = [
            {
                "Hash": c["short_hash"],
                "Message": c["message"][:60],
                "Author": c["author"],
                "Date": c["date"],
                "Savepoint": "Yes" if c.get("is_savepoint") else "",
            }
            for c in commits
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.divider()
    section("Git Actions")

    selected_hash = st.selectbox(
        "Select commit",
        [c["short_hash"] for c in commits],
        format_func=lambda h: f"{h} - {next((c['message'][:50] for c in commits if c['short_hash'] == h), '')}",
        key=f"proj_git_sel_{name}",
    )
    full_hash = next((c["hash"] for c in commits if c["short_hash"] == selected_hash), selected_hash)

    gcol1, gcol2, gcol3 = st.columns(3)

    with gcol1:
        if st.button("Restore to commit", use_container_width=True, key=f"proj_git_restore_{name}"):
            _show_git_restore_dialog(app, project["path"], full_hash, selected_hash)

    with gcol2:
        if st.button("Revert commit", use_container_width=True, key=f"proj_git_revert_{name}"):
            with st.spinner("Reverting..."):
                success, message = app.git_manager.revert_commit(project["path"], full_hash)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)

    with gcol3:
        branch_name = st.text_input(
            "Branch name",
            key=f"proj_git_branch_name_{name}",
            placeholder="feature-branch",
            label_visibility="collapsed",
        )
        if (
            st.button("Create Branch", use_container_width=True, key=f"proj_git_branch_{name}")
            and branch_name
        ):
            success, message = app.git_manager.create_branch_from_commit(project["path"], full_hash, branch_name)
            if success:
                st.success(message)
            else:
                st.error(message)


# ── Configuration Tab ────────────────────────────────────────────────


def _render_project_config(project: dict[str, Any]) -> None:
    """Read-only project configuration display."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Schedule**")
        backup_config = project.get("backup", {})
        st.text(f"Enabled:    {'Yes' if backup_config.get('enabled', True) else 'No'}")
        st.text(f"Schedule:   {backup_config.get('schedule', 'daily')}")
        st.text(f"Time:       {backup_config.get('time', '02:00')}")
        st.text(f"Retention:  {backup_config.get('retention_days', 30)} days")

        st.markdown("**Git**")
        git_config = project.get("git", {})
        st.text(f"Track:       {'Yes' if git_config.get('track', False) else 'No'}")
        st.text(f"Auto-commit: {'Yes' if git_config.get('auto_commit', False) else 'No'}")
        st.text(f"Branch:      {git_config.get('branch', 'main')}")

    with col2:
        st.markdown("**Excluded from Backup**")
        st.caption("Always excluded: folders starting with _ or .")
        exclude_patterns = project.get("exclude", [])
        if exclude_patterns:
            for pattern in exclude_patterns:
                st.code(pattern, language=None)
        else:
            st.text("No additional exclusions configured")


# ── Dialogs ──────────────────────────────────────────────────────────


def _show_restore_dialog(app: AppComponents, project_name: str, backup_name: str) -> None:
    """Restore confirmation dialog."""

    def _on_confirm() -> None:
        with st.spinner("Restoring..."):
            success, message = app.backup_engine.restore_project(project_name, backup_name)
        if success:
            st.success(message)
            invalidate()
            st.rerun()
        else:
            st.error(message)

    show_confirm(
        title="Confirm Restore",
        warning=(
            f"This will replace all current files in **{project_name}** with the contents of "
            f"`{backup_name}`. This cannot be undone."
        ),
        confirm_label="Restore",
        on_confirm=_on_confirm,
        key_prefix="proj_restore_dlg",
    )


@st.dialog("Confirm Git Restore")
def _show_git_restore_dialog(app: AppComponents, path: str, full_hash: str, short_hash: str) -> None:
    """Git restore confirmation dialog — custom because it picks restore mode."""
    st.warning(
        f"This will hard-reset the repository to commit `{short_hash}`. "
        "All uncommitted changes will be lost."
    )
    mode = st.selectbox(
        "Restore mode",
        ["hard", "mixed", "soft"],
        help="hard: discard changes, mixed: keep unstaged, soft: keep staged",
        key="proj_git_dialog_mode",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Restore", type="primary", use_container_width=True, key="proj_git_dialog_confirm"):
            success, message = app.git_manager.restore_to_commit(path, full_hash, mode)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)
    with col2:
        if st.button("Cancel", use_container_width=True, key="proj_git_dialog_cancel"):
            st.rerun()


@st.dialog("Confirm Git Backup Restore")
def _show_git_backup_restore_dialog(
    app: AppComponents, project_name: str, backup_file: str, mode: str
) -> None:
    """Git bundle restore dialog — custom because it accepts a target path."""
    if mode == "clone":
        st.warning(
            "This will **replace** the current project directory with a fresh clone from the bundle. "
            "The existing directory will be renamed as a backup."
        )
    else:
        st.info(
            "This will **fetch** the git history from the bundle into the existing repository. "
            "Your working directory and current branch will not be modified."
        )

    target_path = st.text_input(
        "Target path (optional)",
        placeholder="Leave empty to use original project path",
        key="git_backup_restore_target",
    )

    validated_target: str | None = None
    if target_path:
        resolved = Path(target_path).resolve()
        home = Path.home().resolve()
        if not str(resolved).startswith(str(home)):
            st.error("Target path must be within your home directory.")
        else:
            validated_target = str(resolved)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Restore", type="primary", use_container_width=True, key="git_backup_dialog_confirm"):
            with st.spinner("Restoring git backup..."):
                success, message = app.backup_engine.restore_git(
                    project_name, backup_file, validated_target, mode
                )
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)
    with col2:
        if st.button("Cancel", use_container_width=True, key="git_backup_dialog_cancel"):
            st.rerun()


@st.dialog("Add Project")
def _show_add_project_dialog(app: AppComponents) -> None:
    """Add new project dialog."""
    with st.form("add_project_form"):
        name = st.text_input("Project Name")
        path = st.text_input("Project Path")
        project_type = st.selectbox("Type", ["php", "python", "node", "other"])
        description = st.text_area("Description")

        col1, col2 = st.columns(2)
        with col1:
            schedule = st.selectbox("Backup Schedule", ["daily", "weekly", "manual"])
            retention = st.number_input("Retention Days", min_value=1, value=30)
        with col2:
            track_git = st.checkbox("Track with Git", value=True)
            auto_commit = st.checkbox("Auto Commit")

        exclude = st.text_area(
            "Exclude Patterns (one per line)",
            value="vendor/\nnode_modules/\n.git/\n*.log",
        )

        if st.form_submit_button("Add Project"):
            if name and path:
                new_project = {
                    "path": path,
                    "type": project_type,
                    "description": description,
                    "backup": {
                        "enabled": True,
                        "schedule": schedule,
                        "retention_days": retention,
                    },
                    "git": {
                        "track": track_git,
                        "auto_commit": auto_commit,
                    },
                    "exclude": [p for p in exclude.split("\n") if p.strip()] if exclude else [],
                }
                app.config.add_project(name, new_project)
                st.success(f"Project '{name}' added")
                invalidate()
                st.rerun()
            else:
                st.error("Name and Path are required")
