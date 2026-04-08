"""Dashboard page - overview, quick actions, analytics."""

import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
from web.components import (
    Metric,
    block_heading,
    empty_state,
    health_label,
    health_level,
    metrics_grid,
    page_header,
    relative_time,
    task_status_row,
)
from web.state import AppComponents
from web.theme import COLORS


def render_dashboard(app: AppComponents) -> None:
    """Render the Dashboard page."""
    page_header("Dashboard", "Backup overview and quick actions")

    # ── Running tasks (auto-refresh fragment) ────────────────────────
    _render_running_tasks(app)

    # ── Startup task summary ─────────────────────────────────────────
    _render_startup_summary(app)

    # ── Metrics Row ──────────────────────────────────────────────────
    health_metrics = get_health_metrics(app.visualizer)
    projects = app.config.get_all_projects()
    databases = app.config.get_all_databases()

    level = health_level(health_metrics["newest_backup"])
    metrics_grid(
        [
            Metric("Projects", len(projects)),
            Metric("Databases", len(databases)),
            Metric("Total Size", f"{health_metrics['total_size_gb']:.2f} GB"),
            Metric("Last Backup", relative_time(health_metrics["newest_backup"])),
            Metric("Health", health_label(level), status_level=level),
        ],
        max_columns=5,
    )

    st.divider()

    # ── Quick Actions + Active Tasks ─────────────────────────────────
    # The Dashboard is an overview, not a wizard with one obvious next step.
    # All bulk-backup buttons are equal-weight toolbar actions, NOT primaries.
    action_col, task_col = st.columns([3, 2])

    with action_col:
        block_heading("Quick Actions")
        btn1, btn2, btn3 = st.columns(3)

        with btn1:
            if st.button("Backup All Projects", use_container_width=True, key="dash_backup_projects"):
                task_id = app.bg_backup.schedule_backup("all-projects", "all")
                st.success(f"Started in background (Task: {task_id[:8]}...)")

        with btn2:
            if st.button("Backup All Databases", use_container_width=True, key="dash_backup_dbs"):
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
        block_heading("Active Tasks")
        running = app.bg_backup.get_running_tasks()
        if running:
            for task in running:
                _render_task_progress(task)
        else:
            # Full opacity here — qm-card-muted's 0.7 drops contrast on the
            # only text in the cell ("All systems quiet") below the dark-mode
            # secondary-text minimum.
            st.markdown(
                f'<div class="qm-card" style="text-align:center;padding:1rem;color:{COLORS["text_muted"]};">'
                "All systems quiet</div>",
                unsafe_allow_html=True,
            )

    st.divider()

    # ── Recent Backups + Chart ───────────────────────────────────────
    # Chart needs more horizontal room than the sparse table — 2:3 split
    # stops the table from looking stretched and the chart from feeling cramped.
    table_col, chart_col = st.columns([2, 3])

    with table_col:
        block_heading("Recent Backups")
        _render_recent_backups_table(app, projects, databases)

    with chart_col:
        block_heading("Backup Timeline (last 30 days)")
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
            f'<div class="health-alert">Items without recent backup (&gt;7 days): <strong>{html.escape(items)}</strong></div>',
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
        f'<div class="task-row">{html.escape(label)} &mdash; {task.progress}%</div>',
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


def _format_backup_timestamp(raw: str | None) -> str:
    """Render an ISO timestamp as 'YYYY-MM-DD HH:MM' — no microseconds, no T."""
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        logger.debug("bad timestamp in recent-backups table: %r", raw)
        return ""


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
                    "Last Backup": _format_backup_timestamp(lb.get("modified")),
                    "Age": relative_time(lb.get("modified")),
                    "_sort": lb.get("modified", ""),
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
                    "Last Backup": _format_backup_timestamp(lb.get("modified")),
                    "Age": relative_time(lb.get("modified")),
                    "_sort": lb.get("modified", ""),
                }
            )

    if rows:
        df = pd.DataFrame(rows)
        df["_sort"] = pd.to_datetime(df["_sort"], format="ISO8601", errors="coerce")
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
