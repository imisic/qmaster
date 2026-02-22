"""Dashboard page - overview, quick actions, analytics."""

from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from web.cache import (
    get_backup_status,
    get_backup_success_rate,
    get_backup_timeline,
    get_health_metrics,
    get_retention_distribution,
    get_storage_by_type,
    get_storage_trends,
)
from utils.background_backup import BackupTask
from web.components.backup_card import task_status_row
from web.components.data_table import relative_time
from web.components.empty_state import empty_state
from web.components.metrics import format_last_backup
from web.components.status_badge import (
    health_label,
    health_level,
    status_badge,
)
from web.state import AppComponents


def render_dashboard(app: AppComponents) -> None:
    """Render the Dashboard page."""
    st.markdown('<div class="page-title">Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Backup overview and quick actions</div>', unsafe_allow_html=True)

    # ── Running tasks (auto-refresh fragment) ────────────────────────
    _render_running_tasks(app)

    # ── Startup task summary ─────────────────────────────────────────
    _render_startup_summary(app)

    # ── Metrics Row ──────────────────────────────────────────────────
    health_metrics = get_health_metrics(app.visualizer)
    projects = app.config.get_all_projects()
    databases = app.config.get_all_databases()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Projects", len(projects))
    with col2:
        st.metric("Databases", len(databases))
    with col3:
        st.metric("Total Size", f"{health_metrics['total_size_gb']:.2f} GB")
    with col4:
        st.metric("Last Backup", format_last_backup(health_metrics["newest_backup"]))
    with col5:
        level = health_level(health_metrics["newest_backup"])
        label = health_label(level)
        st.metric("Health", label)
        st.markdown(status_badge(label, level), unsafe_allow_html=True)

    st.markdown("---")

    # ── Quick Actions + Active Tasks ─────────────────────────────────
    action_col, task_col = st.columns([3, 2])

    with action_col:
        st.markdown('<div class="section-header">Quick Actions</div>', unsafe_allow_html=True)
        btn1, btn2, btn3 = st.columns(3)

        with btn1:
            if st.button("Backup All Projects", type="primary", use_container_width=True, key="dash_backup_projects"):
                task_id = app.bg_backup.schedule_backup("all-projects", "all")
                st.success(f"Started in background (Task: {task_id[:8]}...)")

        with btn2:
            if st.button("Backup All Databases", type="primary", use_container_width=True, key="dash_backup_dbs"):
                task_id = app.bg_backup.schedule_backup("all-databases", "all")
                st.success(f"Started in background (Task: {task_id[:8]}...)")

        with btn3:
            if st.button("Run Overdue", use_container_width=True, key="dash_run_overdue"):
                task_ids = app.bg_backup.run_overdue_backups(force=True)
                if task_ids:
                    st.success(f"Started {len(task_ids)} tasks in background")
                else:
                    st.info("No backups needed")

    with task_col:
        st.markdown('<div class="section-header">Active Tasks</div>', unsafe_allow_html=True)
        running = app.bg_backup.get_running_tasks()
        if running:
            for task in running:
                _render_task_progress(task)
        else:
            st.markdown(
                '<div class="qm-card-muted" style="text-align:center;padding:1.5rem;">All systems quiet</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── Recent Backups + Chart ───────────────────────────────────────
    table_col, chart_col = st.columns(2)

    with table_col:
        st.markdown('<div class="section-header">Recent Backups</div>', unsafe_allow_html=True)
        _render_recent_backups_table(app, projects, databases)

    with chart_col:
        st.markdown('<div class="section-header">Backup Timeline</div>', unsafe_allow_html=True)
        timeline_fig = get_backup_timeline(app.visualizer, 30)
        if timeline_fig:
            st.plotly_chart(timeline_fig, use_container_width=True)
        else:
            empty_state("No timeline data", "Backups will appear here after creation")

        with st.expander("More analytics"):
            _render_analytics_tabs(app)

    # ── Health Alerts ────────────────────────────────────────────────
    if health_metrics["items_without_recent_backup"]:
        items = ", ".join(health_metrics["items_without_recent_backup"])
        st.markdown(
            f'<div class="health-alert">Items without recent backup (&gt;7 days): <strong>{items}</strong></div>',
            unsafe_allow_html=True,
        )

    # ── Background Task History ──────────────────────────────────────
    all_tasks = app.bg_backup.get_all_tasks()
    if all_tasks:
        with st.expander(f"Background Task History ({len(all_tasks)} tasks)"):
            sorted_tasks = sorted(all_tasks, key=lambda t: t.started_at or datetime.min, reverse=True)
            for task in sorted_tasks[:3]:
                task_status_row(task)
                st.divider()

    # ── Schedule Summary (absorbed from Scheduler page) ──────────────
    with st.expander("Schedule Summary"):
        _render_schedule_table(app, projects, databases)


# ── Private Helpers ──────────────────────────────────────────────────


@st.fragment(run_every=5)
def _render_running_tasks(app: AppComponents) -> None:
    """Auto-refreshing fragment for running-task indicators."""
    running = app.bg_backup.get_running_tasks()
    if running:
        st.info("Background backups running. This section auto-refreshes.")
        for task in running:
            _render_task_progress(task)


def _render_task_progress(task: BackupTask) -> None:
    """Show a single task's progress bar."""
    label = f"{task.task_type}: {task.target}" if task.target != "all" else task.task_type.replace("-", " ").title()
    st.markdown(
        f'<div class="task-row">{label} &mdash; {task.progress}%</div>',
        unsafe_allow_html=True,
    )
    st.progress(task.progress / 100)


def _render_startup_summary(app: AppComponents) -> None:
    """Show startup overdue-backup summary if applicable."""
    startup_tasks = st.session_state.get("startup_tasks", [])
    if not startup_tasks:
        return

    all_completed = True
    success_count = 0
    failed_count = 0

    for task_id in startup_tasks:
        task = app.bg_backup.get_task_status(task_id)
        if task:
            if task.status.value in ("pending", "running"):
                all_completed = False
            elif task.status.value == "completed":
                success_count += 1
            elif task.status.value == "failed":
                failed_count += 1

    if not all_completed:
        st.markdown(
            '<div class="health-alert">Overdue backups detected — running in background...</div>',
            unsafe_allow_html=True,
        )
    elif success_count > 0 and failed_count == 0:
        st.success(f"Background backups completed ({success_count} tasks)")
    elif success_count > 0:
        st.warning(f"Background backups: {success_count} succeeded, {failed_count} failed")
    elif failed_count > 0:
        st.error(f"Background backups failed ({failed_count} tasks)")


def _render_recent_backups_table(app: AppComponents, projects: dict[str, Any], databases: dict[str, Any]) -> None:
    """Unified recent-backups table (projects + databases)."""
    if not PANDAS_AVAILABLE:
        st.info("Install pandas for table display")
        return

    rows = []
    for name in projects:
        status = get_backup_status(app.backup_engine, "project", name)
        if status["latest_backup"]:
            lb = status["latest_backup"]
            rows.append(
                {
                    "Name": name,
                    "Type": "Project",
                    "Size (MB)": f"{lb['size_mb']:.2f}",
                    "Last Backup": lb.get("modified", ""),
                    "Age": relative_time(lb.get("modified")),
                }
            )

    for name in databases:
        status = get_backup_status(app.backup_engine, "database", name)
        if status["latest_backup"]:
            lb = status["latest_backup"]
            rows.append(
                {
                    "Name": name,
                    "Type": "Database",
                    "Size (MB)": f"{lb['size_mb']:.2f}",
                    "Last Backup": lb.get("modified", ""),
                    "Age": relative_time(lb.get("modified")),
                }
            )

    if rows:
        df = pd.DataFrame(rows)
        if "Last Backup" in df.columns:
            df["_sort"] = pd.to_datetime(df["Last Backup"], format="ISO8601", errors="coerce")
            df = df.sort_values("_sort", ascending=False).drop(columns=["_sort"]).head(10)
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        empty_state("No backups yet", "Create your first backup to get started")


def _render_analytics_tabs(app: AppComponents) -> None:
    """Additional analytics charts in an expander."""
    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "Storage Trends",
            "Retention Tiers",
            "Success Rate",
            "Storage by Type",
        ]
    )

    with tab1:
        fig = get_storage_trends(app.visualizer, 30)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No storage data available")

    with tab2:
        fig = get_retention_distribution(app.visualizer)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No retention data available")

    with tab3:
        fig = get_backup_success_rate(app.visualizer, 7)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No success data available")

    with tab4:
        fig = get_storage_by_type(app.visualizer)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No storage type data available")


def _render_schedule_table(app: AppComponents, projects: dict[str, Any], databases: dict[str, Any]) -> None:
    """Absorbed from old Scheduler page: show schedule table + cron instructions."""
    if not PANDAS_AVAILABLE:
        st.info("Install pandas for schedule display")
        return

    items = []
    for name, project in projects.items():
        if project.get("backup", {}).get("enabled", True):
            items.append(
                {
                    "Type": "Project",
                    "Name": name,
                    "Schedule": project.get("backup", {}).get("schedule", "manual"),
                    "Time": project.get("backup", {}).get("time", "N/A"),
                    "Retention": f"{project.get('backup', {}).get('retention_days', 30)}d",
                }
            )

    for name, db in databases.items():
        if db.get("backup", {}).get("enabled", True):
            items.append(
                {
                    "Type": "Database",
                    "Name": name,
                    "Schedule": db.get("backup", {}).get("schedule", "manual"),
                    "Time": db.get("backup", {}).get("time", "N/A"),
                    "Retention": f"{db.get('backup', {}).get('retention_days', 14)}d",
                }
            )

    if items:
        st.dataframe(pd.DataFrame(items), hide_index=True, use_container_width=True)
    else:
        st.info("No scheduled backups configured")

    with st.expander("Cron Setup Instructions"):
        install_dir = Path(__file__).parent.parent.parent.parent
        st.code(
            "# Add to crontab (crontab -e):\n"
            "\n"
            "# Daily project backups at 2 AM\n"
            f"0 2 * * * cd {install_dir} && ./venv/bin/python src/cli.py backup --all\n"
            "\n"
            "# Daily database backups at 3 AM\n"
            f"0 3 * * * cd {install_dir} && ./venv/bin/python src/cli.py backup-db --all",
            language="bash",
        )
