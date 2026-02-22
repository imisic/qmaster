"""Projects page - per-project management, git, backup/restore."""

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
from web.components.data_table import backup_table, relative_time
from web.components.empty_state import empty_state
from web.components.status_badge import (
    health_label,
    health_level,
    status_badge,
    type_badge,
)
from web.state import AppComponents


def render_projects(app: AppComponents) -> None:
    """Render the Projects page."""
    st.markdown('<div class="page-title">Projects</div>', unsafe_allow_html=True)

    projects = app.config.get_all_projects()

    if not projects:
        empty_state("No projects configured", "Add a project to get started")
        if st.button("Add Project", type="primary", key="proj_add_empty"):
            _show_add_project_dialog(app)
        return

    # ── Header Bar ───────────────────────────────────────────────────
    project_names = list(projects.keys())

    btn_col1, btn_col2 = st.columns([1, 1])
    with btn_col1:
        if st.button("+ Add Project", use_container_width=True, key="proj_add_btn"):
            _show_add_project_dialog(app)
    with btn_col2:
        if st.button("Backup All", use_container_width=True, key="proj_backup_all"):
            task_id = app.bg_backup.schedule_backup("all-projects", "all")
            st.success(f"Started in background ({task_id[:8]}...)")

    # Single-click project switcher
    selected_name = st.segmented_control(
        "Project",
        project_names,
        default=project_names[0],
        key="proj_selector",
        label_visibility="collapsed",
    )

    if not selected_name:
        selected_name = project_names[0]

    st.markdown("---")

    # ── Selected Project View ────────────────────────────────────────
    project = projects[selected_name]
    status = get_backup_status(app.backup_engine, "project", selected_name)

    # Row 1: Project Header
    info_col, stats_col = st.columns([3, 1])

    with info_col:
        st.markdown(f'<div class="section-header">{selected_name}</div>', unsafe_allow_html=True)
        st.markdown(f'<span class="project-path">{project["path"]}</span>', unsafe_allow_html=True)

        badge_html = type_badge(project.get("type", "unknown"), "project")
        desc = project.get("description", "")
        st.markdown(f"{badge_html}&nbsp;&nbsp;{desc}", unsafe_allow_html=True)

        # Git status
        if project.get("git", {}).get("track", False):
            git_status = get_git_status(app.git_manager, project["path"])
            if git_status.get("is_repo"):
                branch = git_status.get("branch", "unknown")
                if git_status.get("is_dirty"):
                    changes = git_status.get("total_changes", 0)
                    git_badge = status_badge(f"{branch} ({changes} changes)", "warning")
                else:
                    git_badge = status_badge(f"{branch} (clean)", "healthy")
                st.markdown(f"Git: {git_badge}", unsafe_allow_html=True)

    with stats_col:
        if status["exists"] and status.get("backup_count", 0) > 0:
            level = health_level(status["latest_backup"].get("modified") if status["latest_backup"] else None)
            st.markdown(status_badge(health_label(level), level), unsafe_allow_html=True)
            st.metric("Backups", status["backup_count"])
            st.metric("Size", f"{status.get('total_size_mb', 0):.1f} MB")
        else:
            st.markdown(status_badge("No backups", "inactive"), unsafe_allow_html=True)

    st.markdown("---")

    # Row 2: Action Bar
    actions = st.columns(4)
    with actions[0]:
        if st.button("Backup Now", type="primary", use_container_width=True, key=f"proj_bk_{selected_name}"):
            with st.spinner(f"Backing up {selected_name}..."):
                success, message = app.backup_engine.backup_project(selected_name)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)

    with actions[1]:
        if st.button("Incremental", use_container_width=True, key=f"proj_incr_{selected_name}"):
            with st.spinner("Creating incremental backup..."):
                success, message = app.backup_engine.backup_project(selected_name, incremental=True)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)

    with actions[2]:
        if st.button("Complete Backup", use_container_width=True, key=f"proj_complete_{selected_name}"):
            with st.spinner("Creating complete backup..."):
                success, message = app.backup_engine.backup_project_complete(selected_name)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)

    with actions[3]:
        if project.get("git", {}).get("track", False) and st.button(
            "Create Savepoint", use_container_width=True, key=f"proj_save_{selected_name}"
        ):
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

    # Row 3: Content Tabs
    tab_labels = ["Backup History"]
    # Check if project is a git repo (not just if tracking is enabled)
    is_git_repo = app.git_manager.is_git_repo(project["path"])
    if is_git_repo:
        tab_labels.append("Git History")
        tab_labels.append("Git Backups")
    tab_labels.append("Configuration")

    tabs = st.tabs(tab_labels)

    # ── Tab: Backup History ──────────────────────────────────────────
    with tabs[0]:
        _render_backup_history(app, selected_name, status)

    # ── Tab: Git History (if git repo) ───────────────────────────────
    if is_git_repo:
        with tabs[1]:
            _render_git_history(app, selected_name, project)

        # ── Tab: Git Backups ─────────────────────────────────────────
        with tabs[2]:
            _render_git_backups(app, selected_name, project)

    # ── Tab: Configuration ───────────────────────────────────────────
    with tabs[-1]:
        _render_project_config(project)


# ── Private Helpers ──────────────────────────────────────────────────


def _render_backup_history(app: AppComponents, project_name: str, status: dict[str, Any]) -> None:
    """Backup history tab with restore/delete per row."""
    if not status["exists"] or not status.get("all_backups"):
        empty_state("No backups yet", "Create your first backup with the button above")
        return

    all_backups = status.get("all_backups", [])
    backup_table(all_backups, show_type=True, max_rows=10, key_prefix=f"proj_bt_{project_name}")

    st.markdown("---")

    # Restore controls
    st.markdown('<div class="section-header">Restore</div>', unsafe_allow_html=True)
    restore_col, btn_col = st.columns([3, 1])
    with restore_col:
        restore_file = st.selectbox(
            "Select backup to restore",
            [b["name"] for b in all_backups],
            key=f"proj_restore_sel_{project_name}",
            label_visibility="collapsed",
        )
    with btn_col:
        if st.button("Restore", type="primary", use_container_width=True, key=f"proj_restore_btn_{project_name}"):
            _show_restore_dialog(app, project_name, restore_file)


@st.dialog("Confirm Restore")
def _show_restore_dialog(app: AppComponents, project_name: str, backup_name: str) -> None:
    """Restore confirmation dialog."""
    st.warning(
        f"This will replace all current files in **{project_name}** with the contents of "
        f"`{backup_name}`. This cannot be undone."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Restore", type="primary", use_container_width=True, key="proj_dialog_restore"):
            with st.spinner("Restoring..."):
                success, message = app.backup_engine.restore_project(project_name, backup_name)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)
    with col2:
        if st.button("Cancel", use_container_width=True, key="proj_dialog_cancel"):
            st.rerun()


def _render_git_history(app: AppComponents, project_name: str, project: dict[str, Any]) -> None:
    """Git history tab."""
    commits = app.git_manager.get_commit_history(project["path"], limit=15)
    if not commits:
        empty_state("No commits found", "Create a savepoint to start tracking changes")
        return

    if PANDAS_AVAILABLE:
        rows = []
        for c in commits:
            rows.append(
                {
                    "Hash": c["short_hash"],
                    "Message": c["message"][:60],
                    "Author": c["author"],
                    "Date": c["date"],
                    "Savepoint": "Yes" if c.get("is_savepoint") else "",
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Per-commit actions
    st.markdown("---")
    st.markdown('<div class="section-header">Git Actions</div>', unsafe_allow_html=True)

    selected_hash = st.selectbox(
        "Select commit",
        [c["short_hash"] for c in commits],
        format_func=lambda h: f"{h} - {next(c['message'][:50] for c in commits if c['short_hash'] == h)}",
        key=f"proj_git_sel_{project_name}",
    )

    full_hash = next(c["hash"] for c in commits if c["short_hash"] == selected_hash)

    gcol1, gcol2, gcol3 = st.columns(3)

    with gcol1:
        if st.button("Restore to this commit", use_container_width=True, key=f"proj_git_restore_{project_name}"):
            _show_git_restore_dialog(app, project["path"], full_hash, selected_hash)

    with gcol2:
        if st.button("Revert this commit", use_container_width=True, key=f"proj_git_revert_{project_name}"):
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
            "Branch name", key=f"proj_git_branch_name_{project_name}", placeholder="feature-branch"
        )
        if st.button("Create Branch", use_container_width=True, key=f"proj_git_branch_{project_name}") and branch_name:
            success, message = app.git_manager.create_branch_from_commit(project["path"], full_hash, branch_name)
            if success:
                st.success(message)
            else:
                st.error(message)


@st.dialog("Confirm Git Restore")
def _show_git_restore_dialog(app: AppComponents, path: str, full_hash: str, short_hash: str) -> None:
    """Git restore confirmation dialog."""
    st.warning(f"This will hard-reset the repository to commit `{short_hash}`. All uncommitted changes will be lost.")

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


def _render_git_backups(app: AppComponents, project_name: str, project: dict[str, Any]) -> None:
    """Git bundle backups tab."""
    git_status = get_backup_status(app.backup_engine, "git", project_name)

    # Action bar
    col1, _col2 = st.columns([1, 3])
    with col1:
        if st.button(
            "Create Git Backup", type="primary", use_container_width=True, key=f"proj_git_backup_{project_name}"
        ):
            with st.spinner("Creating git bundle backup..."):
                success, message = app.backup_engine.backup_git(project_name)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)

    # Show git backup status
    if not git_status["exists"] or not git_status.get("all_backups"):
        empty_state(
            "No git backups yet",
            "Git backups are portable bundle files containing your full git history. "
            "Create one to have a restorable backup of all commits and branches.",
        )
        return

    # Stats row
    stat_col1, stat_col2, stat_col3 = st.columns(3)
    with stat_col1:
        st.metric("Total Backups", git_status["backup_count"])
    with stat_col2:
        st.metric("Total Size", f"{git_status.get('total_size_mb', 0):.1f} MB")
    with stat_col3:
        if git_status["latest_backup"]:
            latest_date = git_status["latest_backup"].get("modified", "")[:10]
            st.metric("Latest Backup", latest_date)

    st.markdown("---")

    # Backup history table
    all_backups = git_status.get("all_backups", [])

    if PANDAS_AVAILABLE:
        rows = []
        for b in all_backups:
            rows.append(
                {
                    "Name": b["name"],
                    "Size (MB)": f"{b['size_mb']:.2f}",
                    "Date": b["modified"][:19].replace("T", " "),
                    "Age": relative_time(datetime.fromisoformat(b["modified"])),
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        for b in all_backups[:10]:
            st.text(f"{b['name']} - {b['size_mb']:.2f} MB - {b['modified'][:10]}")

    # Restore controls
    st.markdown("---")
    st.markdown('<div class="section-header">Restore from Git Backup</div>', unsafe_allow_html=True)

    restore_col1, restore_col2, restore_col3 = st.columns([2, 1, 1])
    with restore_col1:
        restore_file = st.selectbox(
            "Select backup",
            [b["name"] for b in all_backups],
            key=f"proj_git_restore_sel_{project_name}",
            label_visibility="collapsed",
        )
    with restore_col2:
        restore_mode = st.selectbox(
            "Mode",
            ["clone", "fetch"],
            help="clone: create new repo from backup, fetch: update existing repo",
            key=f"proj_git_restore_mode_{project_name}",
        )
    with restore_col3:
        if st.button("Restore", type="primary", use_container_width=True, key=f"proj_git_restore_btn_{project_name}"):
            _show_git_backup_restore_dialog(app, project_name, restore_file, restore_mode)


@st.dialog("Confirm Git Backup Restore")
def _show_git_backup_restore_dialog(app: AppComponents, project_name: str, backup_file: str, mode: str) -> None:
    """Git backup restore confirmation dialog."""
    if mode == "clone":
        st.warning(
            "This will **replace** the current project directory with a fresh clone from the backup. "
            "The existing directory will be renamed as a backup."
        )
    else:
        st.info(
            "This will **fetch** the git history from the backup into the existing repository. "
            "Your working directory and current branch will not be modified."
        )

    target_path = st.text_input(
        "Target path (optional)",
        placeholder="Leave empty to use original project path",
        key="git_backup_restore_target",
    )

    # Validate target path if provided
    validated_target = None
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
                success, message = app.backup_engine.restore_git(project_name, backup_file, validated_target, mode)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)
    with col2:
        if st.button("Cancel", use_container_width=True, key="git_backup_dialog_cancel"):
            st.rerun()


def _render_project_config(project: dict[str, Any]) -> None:
    """Read-only project configuration display."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Schedule Settings**")
        backup_config = project.get("backup", {})
        st.text(f"Enabled:    {'Yes' if backup_config.get('enabled', True) else 'No'}")
        st.text(f"Schedule:   {backup_config.get('schedule', 'daily')}")
        st.text(f"Time:       {backup_config.get('time', '02:00')}")
        st.text(f"Retention:  {backup_config.get('retention_days', 30)} days")

        git_config = project.get("git", {})
        st.markdown("**Git Settings**")
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
