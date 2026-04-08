"""Databases page - per-database management, backup/restore."""

import html
from typing import Any

import streamlit as st

from web.cache import get_backup_status, invalidate
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
    restore_section,
    show_confirm,
    status_badge,
    type_badge,
)
from web.state import AppComponents
from web.theme import COLORS


# ── Entry Point ──────────────────────────────────────────────────────


def render_databases(app: AppComponents) -> None:
    """Render the Databases page."""
    page_header(
        "Databases",
        "MySQL/MariaDB connections backed up via mysqldump. Passwords encrypt on first save.",
    )

    defaults_expander(
        "Database Defaults",
        "These defaults apply to new database backups unless overridden.",
        [
            ("Schedule", app.config.get_setting("defaults.database.schedule", "daily")),
            ("Retention", f"{app.config.get_setting('defaults.database.retention_days', 14)}d"),
            ("Time", app.config.get_setting("defaults.database.time", "03:00")),
        ],
    )

    databases = app.config.get_all_databases()

    if not databases:
        empty_state("No databases configured", "Add a database to get started")
        if st.button("Add Database", type="primary", key="db_add_empty"):
            _show_add_database_dialog(app)
        return

    toolbar_col, _rest = st.columns([2, 4])
    with toolbar_col:
        tb1, tb2 = st.columns(2)
        with tb1:
            if st.button("+ Add Database", use_container_width=True, key="db_add_btn"):
                _show_add_database_dialog(app)
        with tb2:
            if st.button("Backup All", use_container_width=True, key="db_backup_all"):
                task_id = app.bg_backup.schedule_backup("all-databases", "all")
                st.success(f"Started in background ({task_id[:8]}...)")

    db_names = list(databases.keys())
    selected_db = item_picker("Database", db_names, key="db_selector") or db_names[0]

    st.divider()

    db_config = databases[selected_db]
    status = get_backup_status(app.backup_engine, "database", selected_db)

    _render_database_header(selected_db, db_config, status)
    st.divider()
    _render_database_actions(app, selected_db)

    tab1, tab2 = st.tabs(["Backups", "Configuration"])

    with tab1:
        _render_backup_history(app, selected_db, status)

    with tab2:
        _render_db_config(db_config)


# ── Header Block ─────────────────────────────────────────────────────


def _render_database_header(
    name: str,
    db_config: dict[str, Any],
    status: dict[str, Any],
) -> None:
    """Two-column header: info on the left, metrics grid on the right."""
    info_col, stats_col = st.columns([3, 2])

    with info_col:
        item_heading(name)
        badge = type_badge(db_config.get("type", "mysql"), "database")
        host = html.escape(str(db_config.get("host", "localhost")))
        port = html.escape(str(db_config.get("port", 3306)))
        st.markdown(f"{badge}&nbsp;&nbsp;{host}:{port}", unsafe_allow_html=True)

        desc = db_config.get("description", "")
        if desc:
            st.markdown(
                f'<span style="color:{COLORS["text_muted"]}">{html.escape(desc)}</span>',
                unsafe_allow_html=True,
            )

    with stats_col:
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


def _render_database_actions(app: AppComponents, name: str) -> None:
    """Single primary action: Backup Now."""

    def _backup() -> None:
        with st.spinner(f"Backing up {name}..."):
            success, message = app.backup_engine.backup_database(name)
        if success:
            st.success(message)
            invalidate()
            st.rerun()
        else:
            st.error(message)

    action_bar(primary=Action("Backup Now", f"db_bk_{name}", _backup))


# ── Backups Tab ──────────────────────────────────────────────────────


def _render_backup_history(app: AppComponents, db_name: str, status: dict[str, Any]) -> None:
    """Backup history tab with restore."""
    if not status["exists"] or not status.get("all_backups"):
        empty_state("No backups yet", "Create your first backup with the button above")
        return

    all_backups = status.get("all_backups", [])
    backup_table(all_backups, show_type=False, max_rows=10, key_prefix=f"db_bt_{db_name}")

    restore_section(
        [b["name"] for b in all_backups],
        key_prefix=f"db_{db_name}",
        on_restore=lambda file: _show_restore_dialog(app, db_name, file),
    )


def _show_restore_dialog(app: AppComponents, db_name: str, backup_name: str) -> None:
    """Database restore confirmation dialog."""

    def _on_confirm() -> None:
        with st.spinner("Restoring..."):
            success, message = app.backup_engine.restore_database(db_name, backup_name)
        if success:
            st.success(message)
            invalidate()
            st.rerun()
        else:
            st.error(message)

    show_confirm(
        title="Confirm Database Restore",
        warning=(
            f"This will replace the current **{db_name}** database with the contents of "
            f"`{backup_name}`. This cannot be undone."
        ),
        confirm_label="Restore",
        on_confirm=_on_confirm,
        key_prefix="db_restore_dlg",
    )


# ── Configuration Tab ────────────────────────────────────────────────


def _render_db_config(db_config: dict[str, Any]) -> None:
    """Read-only database configuration display."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Schedule**")
        backup_cfg = db_config.get("backup", {})
        st.text(f"Enabled:    {'Yes' if backup_cfg.get('enabled', True) else 'No'}")
        st.text(f"Schedule:   {backup_cfg.get('schedule', 'daily')}")
        st.text(f"Time:       {backup_cfg.get('time', '03:00')}")
        st.text(f"Retention:  {backup_cfg.get('retention_days', 14)} days")
        st.text(f"Compress:   {'Yes' if backup_cfg.get('compress', True) else 'No'}")

    with col2:
        st.markdown("**Dump Options**")
        dump_options = backup_cfg.get("options", []) if "backup" in db_config else []
        if dump_options:
            for option in dump_options:
                st.code(option, language=None)
        else:
            st.text("Using default mysqldump options")


# ── Add Dialog ───────────────────────────────────────────────────────


@st.dialog("Add Database")
def _show_add_database_dialog(app: AppComponents) -> None:
    """Add new database dialog."""
    with st.form("add_database_form"):
        name = st.text_input("Database Name")
        db_type = st.selectbox("Type", ["mysql", "postgresql", "mongodb"])
        default_host = app.config.get_setting("mysql_defaults.host", "localhost")
        default_port = app.config.get_setting("mysql_defaults.port", 3306)
        default_user = app.config.get_setting("mysql_defaults.user", "root")
        host = st.text_input("Host", value=default_host)
        port = st.number_input("Port", value=default_port)
        user = st.text_input("Username", value=default_user)
        password = st.text_input("Password", type="password")
        description = st.text_area("Description")

        col1, col2 = st.columns(2)
        with col1:
            schedule = st.selectbox("Backup Schedule", ["daily", "weekly", "manual"])
            retention = st.number_input("Retention Days", min_value=1, value=14)
        with col2:
            compress = st.checkbox("Compress Backups", value=True)

        if st.form_submit_button("Add Database"):
            if name and user:
                new_database = {
                    "type": db_type,
                    "host": host,
                    "port": port,
                    "user": user,
                    "password": password,
                    "description": description,
                    "backup": {
                        "enabled": True,
                        "schedule": schedule,
                        "retention_days": retention,
                        "compress": compress,
                    },
                }
                app.config.add_database(name, new_database)
                st.success(f"Database '{name}' added")
                invalidate()
                st.rerun()
            else:
                st.error("Name and Username are required")
