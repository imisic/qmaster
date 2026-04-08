"""Logs & Diagnostics page - Apache errors, backup log, and activity feed."""

import logging
import os
from collections import deque
from typing import Any

import streamlit as st

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import plotly.express as px
    import plotly.graph_objects as go

    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

from web.cache import get_log_stats, get_recent_activity, invalidate
from web.components import (
    Metric,
    empty_state,
    metrics_grid,
    page_header,
    relative_time,
    section,
    show_confirm,
    status_badge,
)
from web.state import AppComponents


def render_logs_diagnostics(app: AppComponents) -> None:
    """Render the Logs & Diagnostics page."""
    page_header("Logs", "View backup logs, Apache errors, and activity history")

    tab_apache, tab_backup, tab_activity = st.tabs(
        ["Apache Errors", "Backup Log", "Activity Feed"]
    )

    with tab_apache:
        _render_apache_logs(app)
    with tab_backup:
        _render_backup_log(app)
    with tab_activity:
        _render_activity_feed(app)


# ═══════════════════════════════════════════════════════════════════════
# Backup Log
# ═══════════════════════════════════════════════════════════════════════


def _render_backup_log(app: AppComponents) -> None:
    """Backup log viewer."""
    log_file = app.config.get_storage_paths()["local"] / "logs" / "backup.log"

    if not log_file.exists():
        empty_state("No log file found", "Logs will appear after the first backup operation")
        return

    ctrl1, ctrl2, ctrl3 = st.columns([2, 1, 1])
    with ctrl1:
        lines_to_show = st.slider("Lines to show", 10, 500, 100, key="log_lines")
    with ctrl2:
        filter_level = st.selectbox(
            "Level",
            ["All", "INFO", "WARNING", "ERROR"],
            key="log_level",
        )
    with ctrl3:
        if st.button("Refresh", use_container_width=True, key="log_refresh"):
            invalidate()
            st.rerun()

    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            recent_lines = list(deque(f, maxlen=lines_to_show))
    except (OSError, PermissionError) as e:
        st.error(f"Cannot read log file: {e}")
        return

    if not recent_lines:
        empty_state("Log file is empty", "Logs will appear after the first backup operation")
        return

    filtered = [line for line in recent_lines if filter_level == "All" or filter_level in line]
    st.code("".join(filtered), language="log")

    info_count = sum(1 for line in recent_lines if "INFO" in line)
    warning_count = sum(1 for line in recent_lines if "WARNING" in line)
    error_count = sum(1 for line in recent_lines if "ERROR" in line)
    st.caption(
        f"Last **{len(recent_lines)}** lines  ·  "
        f"**{info_count}** info  ·  "
        f"**{warning_count}** warnings  ·  "
        f"**{error_count}** errors"
    )


# ═══════════════════════════════════════════════════════════════════════
# Apache Errors (flat single view with inline filters)
# ═══════════════════════════════════════════════════════════════════════


def _render_apache_logs(app: AppComponents) -> None:
    """Apache error log viewer — flat layout with inline filter bar."""
    detected_logs = app.apache_parser.log_paths

    if not detected_logs:
        _render_apache_no_logs(app)
        return

    # ── Top bar: log selector + actions ──────────────────────────────
    h1, h2, h3 = st.columns([3, 1, 1])
    with h1:
        selected_log = st.selectbox("Log File", detected_logs, key="apache_log_sel")
    with h2:
        if st.button("Refresh", use_container_width=True, key="apache_refresh"):
            invalidate()
            st.rerun()
    with h3:
        if st.button("Clear Log", use_container_width=True, key="apache_clear"):
            _open_clear_log_dialog(app, selected_log)

    if not selected_log:
        return

    stats = get_log_stats(app.apache_parser, selected_log)

    # Empty-state for empty files instead of a row of zero metrics
    if not stats.get("exists") or stats.get("line_count", 0) == 0:
        empty_state(
            "No log entries found",
            f"{selected_log} has no readable content",
        )
        return

    metrics_grid(
        [
            Metric("File Size", f"{stats['size_mb']:.2f} MB"),
            Metric("Total Lines", stats["line_count"]),
            Metric("Errors", stats["error_count"]),
            Metric("Warnings", stats["warning_count"]),
        ],
    )

    st.divider()

    # ── Filter bar (single row) ──────────────────────────────────────
    f1, f2, f3, f4 = st.columns([1, 2, 1, 1])
    with f1:
        severity = st.selectbox(
            "Severity",
            ["All", "error", "warn", "notice", "info", "debug"],
            key="apache_sev_filter",
        )
    with f2:
        search_term = st.text_input(
            "Search",
            key="apache_search_term",
            placeholder="Filter messages...",
        )
    with f3:
        view_mode = st.selectbox(
            "Mode",
            ["Tail", "Parse"],
            key="apache_view_mode",
            help="Tail = raw tail of recent lines. Parse = structured entries with filters.",
        )
    with f4:
        line_count = st.number_input(
            "Lines",
            min_value=10,
            max_value=2000,
            value=100,
            step=10,
            key="apache_line_count",
        )

    # ── Output ───────────────────────────────────────────────────────
    if view_mode == "Tail" and severity == "All" and not search_term:
        tail_lines = app.apache_parser.tail_log(selected_log, int(line_count))
        if tail_lines:
            st.code("\n".join(tail_lines), language="log")
        else:
            empty_state("No log entries found")
    else:
        sev_filter = None if severity == "All" else severity
        search = search_term or None
        with st.spinner("Parsing..."):
            logs = app.apache_parser.read_logs(
                selected_log,
                lines=int(line_count),
                severity_filter=sev_filter,
                search_term=search,
            )

        if not logs:
            empty_state("No matching entries", "Try a broader filter or longer line count")
        else:
            st.caption(f"Showing latest {min(50, len(logs))} of {len(logs)} matches")
            for log in reversed(logs[-50:]):
                _render_apache_log_entry(log)

    # ── Statistics + Export expanders (secondary) ────────────────────
    with st.expander("Statistics"):
        _render_apache_stats(app, selected_log, stats)

    with st.expander("Export"):
        _render_apache_export(app, selected_log)


def _render_apache_no_logs(app: AppComponents) -> None:
    """Shown when no Apache log file is detected."""
    st.warning("No Apache log files detected.")
    default_log_path = app.config.get_setting("apache.log_paths", ["/var/log/apache2/error.log"])
    custom_path = st.text_input(
        "Enter Apache error log path:",
        value=default_log_path[0] if default_log_path else "",
        key="apache_custom",
    )
    if custom_path and st.button("Add Custom Log Path", key="apache_add_custom"):
        resolved = os.path.realpath(custom_path)
        if not any(resolved.startswith(d) for d in app.apache_parser._allowed_log_dirs):
            st.error("Path is outside allowed log directories.")
        elif not os.path.exists(resolved):
            st.error("File does not exist.")
        else:
            app.apache_parser.log_paths.append(resolved)
            st.success(f"Added: {resolved}")
            invalidate()
            st.rerun()


def _render_apache_log_entry(log: dict[str, Any]) -> None:
    """Render one parsed Apache log entry as an expandable row."""
    severity = log.get("severity", "info")
    level = (
        "error" if severity == "error"
        else "warning" if severity in ("warn", "warning")
        else "healthy"
    )

    with st.expander(f"{log.get('timestamp', 'N/A')} — {severity.upper()}"):
        st.markdown(status_badge(severity.upper(), level), unsafe_allow_html=True)
        st.text(f"Message: {log.get('message', log.get('raw', ''))}")
        if log.get("client"):
            st.text(f"Client: {log['client']}")
        if log.get("module"):
            st.text(f"Module: {log['module']}")
        st.code(log.get("raw", ""), language="log")


def _open_clear_log_dialog(app: AppComponents, log_path: str) -> None:
    """Confirm log clearing."""

    def _on_confirm() -> None:
        success, message = app.apache_parser.clear_log(log_path)
        if success:
            st.success(message)
            invalidate()
            st.rerun()
        else:
            st.error(message)

    show_confirm(
        title="Confirm Clear Log",
        warning=f"This will clear the contents of `{log_path}`. This cannot be undone.",
        confirm_label="Clear",
        on_confirm=_on_confirm,
        key_prefix="apache_clear_dlg",
    )


def _render_apache_stats(app: AppComponents, selected_log: str, stats: dict[str, Any]) -> None:
    """Apache statistics — severity distribution + hourly histogram + top errors."""
    if not stats.get("exists") or not stats.get("readable"):
        st.error("Cannot read log file for statistics")
        return

    all_logs = app.apache_parser.read_logs(selected_log, lines=0)
    if not all_logs:
        st.info("No log data available for statistics")
        return

    severity_counts: dict[str, int] = {}
    hourly_counts: dict[str, int] = {}
    error_messages: dict[str, int] = {}

    for log in all_logs:
        sev = log.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        try:
            ts = log.get("timestamp", "")
            if "T" in ts:
                hour = ts.split("T")[1][:2]
                hourly_counts[hour] = hourly_counts.get(hour, 0) + 1
        except (ValueError, IndexError, TypeError) as e:
            logging.debug("Skipped unparseable timestamp: %s", e)
            continue
        if sev == "error":
            msg = log.get("message", log.get("raw", ""))[:100]
            error_messages[msg] = error_messages.get(msg, 0) + 1

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Severity Distribution**")
        if severity_counts and PLOTLY_AVAILABLE and PANDAS_AVAILABLE:
            df_sev = pd.DataFrame(list(severity_counts.items()), columns=["Severity", "Count"])
            fig = px.pie(df_sev, values="Count", names="Severity")
            st.plotly_chart(fig, use_container_width=True)
        elif severity_counts:
            for sev, count in severity_counts.items():
                st.text(f"{sev}: {count}")

    with col2:
        st.markdown("**Hourly Distribution**")
        if hourly_counts and PLOTLY_AVAILABLE:
            hours = sorted(hourly_counts.keys())
            counts = [hourly_counts[h] for h in hours]
            fig = go.Figure(data=[go.Bar(x=hours, y=counts)])
            fig.update_layout(xaxis_title="Hour", yaxis_title="Count")
            st.plotly_chart(fig, use_container_width=True)
        elif hourly_counts:
            for h, c in sorted(hourly_counts.items()):
                st.text(f"Hour {h}: {c}")

    section("Top Error Messages")
    if error_messages and PANDAS_AVAILABLE:
        top_errors = sorted(error_messages.items(), key=lambda x: x[1], reverse=True)[:10]
        df_err = pd.DataFrame(top_errors, columns=["Error Message", "Count"])
        st.dataframe(df_err, hide_index=True, use_container_width=True)
    elif error_messages:
        for msg, count in sorted(error_messages.items(), key=lambda x: x[1], reverse=True)[:10]:
            st.text(f"({count}) {msg}")
    else:
        st.info("No errors found in the log")


def _render_apache_export(app: AppComponents, selected_log: str) -> None:
    """Apache log export."""
    col1, col2 = st.columns(2)
    with col1:
        export_format = st.selectbox("Export Format", ["json", "csv", "txt"], key="apache_export_fmt")
    with col2:
        custom_filename = st.text_input("Custom filename (optional)", key="apache_export_name")

    if st.button("Export", key="apache_export_btn"):
        with st.spinner(f"Exporting as {export_format}..."):
            success, message = app.apache_parser.export_logs(
                selected_log,
                output_format=export_format,
                output_file=custom_filename if custom_filename else None,
            )

        if success:
            file_path = message
            st.success(f"Logs exported to {file_path}")
            try:
                if os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        st.download_button(
                            label=f"Download {os.path.basename(file_path)}",
                            data=f.read(),
                            file_name=os.path.basename(file_path),
                            mime="application/octet-stream",
                            key="apache_download",
                        )
            except (OSError, PermissionError) as e:
                st.error(f"Cannot read exported file: {e}")
        else:
            st.error(message)


# ═══════════════════════════════════════════════════════════════════════
# Activity Feed
# ═══════════════════════════════════════════════════════════════════════


def _render_activity_feed(app: AppComponents) -> None:
    """Recent backup activity feed."""
    recent = get_recent_activity(app.visualizer, 15)

    if not recent:
        empty_state("No recent activity", "Activity will appear after backups are created")
        return

    for activity in recent:
        time_str = relative_time(activity["timestamp"])
        item_type = activity.get("item_type", "unknown")
        backup_type = activity.get("backup_type", "full")
        importance = activity.get("importance", "normal")

        type_level = "info" if item_type == "project" else "inactive"
        type_label = item_type.capitalize()

        bt_map = {"incremental": "running", "full": "info"}
        bt_level = bt_map.get(backup_type, "info")

        imp_map = {"critical": "critical", "high": "warning", "normal": "healthy", "low": "inactive"}
        imp_level = imp_map.get(importance, "healthy")

        col1, col2, col3 = st.columns([1, 4, 1])
        with col1:
            st.markdown(
                status_badge(type_label, type_level) + " " + status_badge(backup_type, bt_level),
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(f"**{activity['item_name']}** — {activity.get('backup_name', '')}")
            tags = activity.get("tags", [])
            if tags:
                st.text(f"Tags: {', '.join(tags)}")
        with col3:
            st.markdown(status_badge(importance, imp_level), unsafe_allow_html=True)
            st.text(f"{activity.get('size_mb', 0):.1f} MB")
            st.markdown(f"*{time_str}*")

        st.divider()
