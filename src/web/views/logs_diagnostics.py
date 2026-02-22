"""Logs & Diagnostics page - merges Backup Logs, Apache Logs, and Activity Feed."""

import os
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
from web.components.data_table import relative_time
from web.components.empty_state import empty_state
from web.components.status_badge import status_badge
from web.state import AppComponents


def render_logs_diagnostics(app: AppComponents) -> None:
    """Render the Logs & Diagnostics page."""
    st.markdown('<div class="page-title">Logs & Diagnostics</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-subtitle">View backup logs, Apache errors, and activity history</div>', unsafe_allow_html=True
    )

    view_options = ["Apache Errors", "Backup Log", "Activity Feed"]
    selected_view = st.segmented_control(
        "View",
        view_options,
        default="Apache Errors",
        key="logs_view_selector",
        label_visibility="collapsed",
    )

    if not selected_view:
        selected_view = "Apache Errors"

    st.markdown("---")

    if selected_view == "Apache Errors":
        _render_apache_logs(app)
    elif selected_view == "Backup Log":
        _render_backup_log(app)
    elif selected_view == "Activity Feed":
        _render_activity_feed(app)


# ═══════════════════════════════════════════════════════════════════════
# Tab 1: Backup Log
# ═══════════════════════════════════════════════════════════════════════


def _render_backup_log(app: AppComponents) -> None:
    """Backup log viewer."""
    log_file = app.config.get_storage_paths()["local"] / "logs" / "backup.log"

    if not log_file.exists():
        empty_state("No log file found", "Logs will appear after the first backup operation")
        return

    # Controls
    ctrl1, ctrl2, ctrl3 = st.columns([2, 1, 1])
    with ctrl1:
        lines_to_show = st.slider("Lines to show", 10, 500, 100, key="log_lines")
    with ctrl2:
        filter_level = st.selectbox("Level filter", ["All", "INFO", "WARNING", "ERROR"], key="log_level")
    with ctrl3:
        if st.button("Refresh", use_container_width=True, key="log_refresh"):
            invalidate()
            st.rerun()

    # Read log
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, PermissionError) as e:
        st.error(f"Cannot read log file: {e}")
        return

    recent_lines = lines[-lines_to_show:] if len(lines) > lines_to_show else lines

    # Filter
    filtered = [line for line in recent_lines if filter_level == "All" or filter_level in line]

    # Display (st.code for read-only, not st.text_area)
    st.code("".join(filtered), language="log")

    # Stats
    st.markdown("---")
    st.markdown('<div class="section-header">Log Statistics</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        info_count = sum(1 for line in lines if "INFO" in line)
        st.metric("Info", info_count)
    with col2:
        warning_count = sum(1 for line in lines if "WARNING" in line)
        st.metric("Warnings", warning_count)
    with col3:
        error_count = sum(1 for line in lines if "ERROR" in line)
        st.metric("Errors", error_count)


# ═══════════════════════════════════════════════════════════════════════
# Tab 2: Apache Errors
# ═══════════════════════════════════════════════════════════════════════


def _render_apache_logs(app: AppComponents) -> None:
    """Apache error log viewer."""
    detected_logs = app.apache_parser.log_paths

    if not detected_logs:
        st.warning("No Apache log files detected.")
        default_log_path = app.config.get_setting("apache.log_paths", ["/var/log/apache2/error.log"])
        custom_path = st.text_input(
            "Enter Apache error log path:", value=default_log_path[0] if default_log_path else "", key="apache_custom"
        )
        if custom_path and st.button("Add Custom Log Path", key="apache_add_custom"):
            app.apache_parser.log_paths.append(custom_path)
            st.success(f"Added: {custom_path}")
            st.rerun()
        return

    # Header
    h1, h2, h3 = st.columns([3, 1, 1])
    with h1:
        selected_log = st.selectbox("Log File", detected_logs, key="apache_log_sel")
    with h2:
        if st.button("Refresh", type="primary", use_container_width=True, key="apache_refresh"):
            invalidate()
            st.rerun()
    with h3:
        if st.button("Clear Log", use_container_width=True, key="apache_clear"):
            _show_clear_log_dialog(app, selected_log)

    if not selected_log:
        return

    stats = get_log_stats(app.apache_parser, selected_log)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("File Size", f"{stats['size_mb']:.2f} MB")
    with col2:
        st.metric("Total Lines", stats["line_count"])
    with col3:
        st.metric("Errors", stats["error_count"])
    with col4:
        st.metric("Warnings", stats["warning_count"])

    st.markdown("---")

    # Sub-tabs for Apache functionality
    atab1, atab2, atab3, atab4 = st.tabs(["View Logs", "Search & Filter", "Statistics", "Export"])

    with atab1:
        _render_apache_view(app, selected_log)

    with atab2:
        _render_apache_search(app, selected_log)

    with atab3:
        _render_apache_stats(app, selected_log, stats)

    with atab4:
        _render_apache_export(app, selected_log)


@st.dialog("Confirm Clear Log")
def _show_clear_log_dialog(app: AppComponents, log_path: str) -> None:
    """Confirm log clearing."""
    st.warning(f"This will clear the contents of `{log_path}`. This cannot be undone.")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<div class="danger-btn">', unsafe_allow_html=True)
        if st.button("Clear", use_container_width=True, key="apache_clear_confirm"):
            success, message = app.apache_parser.clear_log(log_path)
            if success:
                st.success(message)
                invalidate()
                st.rerun()
            else:
                st.error(message)
        st.markdown("</div>", unsafe_allow_html=True)
    with col2:
        if st.button("Cancel", use_container_width=True, key="apache_clear_cancel"):
            st.rerun()


def _render_apache_view(app: AppComponents, selected_log: str) -> None:
    """Apache log viewer sub-tab."""
    view_mode = st.radio("View Mode", ["Tail (Latest)", "Full Parse"], horizontal=True, key="apache_view_mode")

    if view_mode == "Tail (Latest)":
        lines_to_show = st.slider("Number of lines", 10, 100, 50, key="apache_tail_lines")
        tail_lines = app.apache_parser.tail_log(selected_log, lines_to_show)
        if tail_lines:
            st.code("\n".join(tail_lines), language="log")
        else:
            empty_state("No log entries found")
    else:
        lines_to_parse = st.slider("Lines to parse (0 for all)", 0, 1000, 100, key="apache_parse_lines")
        logs = app.apache_parser.read_logs(selected_log, lines=lines_to_parse)

        if logs:
            for log in reversed(logs[-50:]):
                severity = log.get("severity", "info")
                level = "error" if severity == "error" else "warning" if severity in ("warn", "warning") else "healthy"
                badge = status_badge(severity.upper(), level)

                with st.expander(f"{log.get('timestamp', 'N/A')} — {severity.upper()}"):
                    st.markdown(badge, unsafe_allow_html=True)
                    st.write(f"**Message:** {log.get('message', log.get('raw', ''))}")
                    if log.get("client"):
                        st.write(f"**Client:** {log['client']}")
                    if log.get("module"):
                        st.write(f"**Module:** {log['module']}")
                    st.code(log.get("raw", ""), language="log")
        else:
            empty_state("No log entries found")


def _render_apache_search(app: AppComponents, selected_log: str) -> None:
    """Apache search & filter sub-tab."""
    col1, col2 = st.columns(2)
    with col1:
        severity_filter = st.selectbox(
            "Severity",
            ["All", "error", "warn", "notice", "info", "debug"],
            key="apache_sev_filter",
        )
        search_term = st.text_input("Search term", key="apache_search_term")
    with col2:
        lines_to_search = st.number_input("Lines to search", min_value=0, value=500, key="apache_search_lines")

    if st.button("Search", key="apache_search_btn"):
        severity = None if severity_filter == "All" else severity_filter
        search = search_term if search_term else None

        with st.spinner("Searching..."):
            filtered_logs = app.apache_parser.read_logs(
                selected_log,
                lines=lines_to_search,
                severity_filter=severity,
                search_term=search,
            )

        if filtered_logs:
            st.success(f"Found {len(filtered_logs)} matching entries")
            for log in reversed(filtered_logs[-50:]):
                severity = log.get("severity", "info")
                level = "error" if severity == "error" else "warning" if severity in ("warn", "warning") else "healthy"

                with st.expander(f"{log.get('timestamp', 'N/A')} — {severity.upper()}"):
                    st.markdown(status_badge(severity.upper(), level), unsafe_allow_html=True)
                    st.write(f"**Message:** {log.get('message', log.get('raw', ''))}")
                    st.code(log.get("raw", ""), language="log")
        else:
            st.info("No matching entries found")


def _render_apache_stats(app: AppComponents, selected_log: str, stats: dict[str, Any]) -> None:
    """Apache statistics sub-tab."""
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
        except Exception:
            continue

        if sev == "error":
            msg = log.get("message", log.get("raw", ""))[:100]
            error_messages[msg] = error_messages.get(msg, 0) + 1

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Severity Distribution**")
        if severity_counts and PLOTLY_AVAILABLE and PANDAS_AVAILABLE:
            df_sev = pd.DataFrame(list(severity_counts.items()), columns=["Severity", "Count"])
            fig = px.pie(df_sev, values="Count", names="Severity", title="Log Entries by Severity")
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
            fig.update_layout(title="Log Entries by Hour", xaxis_title="Hour", yaxis_title="Count")
            st.plotly_chart(fig, use_container_width=True)
        elif hourly_counts:
            for h, c in sorted(hourly_counts.items()):
                st.text(f"Hour {h}: {c}")

    st.markdown("---")
    st.markdown("**Top Error Messages**")
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
    """Apache log export sub-tab."""
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
            st.success(message)
            file_path = message.split("to ")[-1]
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
# Tab 3: Activity Feed
# ═══════════════════════════════════════════════════════════════════════


def _render_activity_feed(app: AppComponents) -> None:
    """Recent backup activity feed (moved from Dashboard)."""
    recent = get_recent_activity(app.visualizer, 15)

    if not recent:
        empty_state("No recent activity", "Activity will appear after backups are created")
        return

    for activity in recent:
        time_str = relative_time(activity["timestamp"])
        item_type = activity.get("item_type", "unknown")
        backup_type = activity.get("backup_type", "full")
        importance = activity.get("importance", "normal")

        # Badges
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
