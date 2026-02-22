"""Databases page - per-database management, backup/restore."""

import importlib.util
from typing import Any

import streamlit as st

PANDAS_AVAILABLE = importlib.util.find_spec("pandas") is not None
if PANDAS_AVAILABLE:
    import pandas as pd

from web.cache import get_backup_status, invalidate
from web.components.data_table import backup_table
from web.components.empty_state import empty_state
from web.components.status_badge import (
    health_label,
    health_level,
    status_badge,
    type_badge,
)
from web.state import AppComponents


def render_databases(app: AppComponents) -> None:
    """Render the Databases page."""
    st.markdown('<div class="page-title">Databases</div>', unsafe_allow_html=True)

    databases = app.config.get_all_databases()

    if not databases:
        empty_state("No databases configured", "Add a database to get started")
        if st.button("Add Database", type="primary", key="db_add_empty"):
            _show_add_database_dialog(app)
        return

    # ── Header Bar ───────────────────────────────────────────────────
    db_names = list(databases.keys())

    btn_col1, btn_col2 = st.columns([1, 1])
    with btn_col1:
        if st.button("+ Add Database", use_container_width=True, key="db_add_btn"):
            _show_add_database_dialog(app)
    with btn_col2:
        if st.button("Backup All", use_container_width=True, key="db_backup_all"):
            task_id = app.bg_backup.schedule_backup("all-databases", "all")
            st.success(f"Started in background ({task_id[:8]}...)")

    # Single-click database switcher
    selected_db = st.segmented_control(
        "Database",
        db_names,
        default=db_names[0],
        key="db_selector",
        label_visibility="collapsed",
    )

    if not selected_db:
        selected_db = db_names[0]

    st.markdown("---")

    # ── Selected Database View ───────────────────────────────────────
    db_config = databases[selected_db]
    status = get_backup_status(app.backup_engine, "database", selected_db)

    # Row 1: Header
    info_col, stats_col = st.columns([3, 1])

    with info_col:
        st.markdown(f'<div class="section-header">{selected_db}</div>', unsafe_allow_html=True)
        badge = type_badge(db_config.get("type", "mysql"), "database")
        st.markdown(
            f"{badge}&nbsp;&nbsp;{db_config.get('host', 'localhost')}:{db_config.get('port', 3306)}",
            unsafe_allow_html=True,
        )
        desc = db_config.get("description", "")
        if desc:
            st.markdown(f'<span style="color:#a1a7b5">{desc}</span>', unsafe_allow_html=True)

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
    acol1, acol2 = st.columns(2)
    with acol1:
        if st.button("Backup Now", type="primary", use_container_width=True, key=f"db_bk_{selected_db}"):
            with st.spinner(f"Backing up {selected_db}..."):
                success, message = app.backup_engine.backup_database(selected_db)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)
    with acol2:
        pass  # Space for future actions

    # Row 3: Content Tabs
    tab1, tab2 = st.tabs(["Backup History", "Configuration"])

    with tab1:
        _render_backup_history(app, selected_db, status)

    with tab2:
        _render_db_config(db_config)


# ── Private Helpers ──────────────────────────────────────────────────


def _render_backup_history(app: AppComponents, db_name: str, status: dict[str, Any]) -> None:
    """Backup history tab with restore."""
    if not status["exists"] or not status.get("all_backups"):
        empty_state("No backups yet", "Create your first backup with the button above")
        return

    all_backups = status.get("all_backups", [])
    backup_table(all_backups, show_type=False, max_rows=10, key_prefix=f"db_bt_{db_name}")

    st.markdown("---")

    st.markdown('<div class="section-header">Restore</div>', unsafe_allow_html=True)
    restore_col, btn_col = st.columns([3, 1])
    with restore_col:
        restore_file = st.selectbox(
            "Select backup to restore",
            [b["name"] for b in all_backups],
            key=f"db_restore_sel_{db_name}",
            label_visibility="collapsed",
        )
    with btn_col:
        if st.button("Restore", type="primary", use_container_width=True, key=f"db_restore_btn_{db_name}"):
            _show_restore_dialog(app, db_name, restore_file)


@st.dialog("Confirm Database Restore")
def _show_restore_dialog(app: AppComponents, db_name: str, backup_name: str) -> None:
    """Database restore confirmation dialog."""
    st.warning(
        f"This will replace the current **{db_name}** database with the contents of "
        f"`{backup_name}`. This cannot be undone."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Restore", type="primary", use_container_width=True, key="db_dialog_restore"):
            with st.spinner("Restoring..."):
                success, message = app.backup_engine.restore_database(db_name, backup_name)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)
    with col2:
        if st.button("Cancel", use_container_width=True, key="db_dialog_cancel"):
            st.rerun()


def _render_db_config(db_config: dict[str, Any]) -> None:
    """Read-only database configuration display."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Schedule Settings**")
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
