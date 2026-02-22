"""Streamlit Web Dashboard for Quartermaster — thin dispatcher."""

import os
import sys

import streamlit as st

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.state import init_app_state
from web.theme import apply_theme
from web.views import PAGE_MAP

APP_VERSION = "2.0"

# ── Page Config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="Quartermaster",
    page_icon="QM",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Theme ────────────────────────────────────────────────────────────
apply_theme()

# ── Initialize Components ────────────────────────────────────────────
app = init_app_state()

# ── Sidebar Navigation ──────────────────────────────────────────────
ALL_PAGES = ["Dashboard", "Projects", "Databases", "Storage & Cleanup", "Logs & Diagnostics", "HTML Cleaner"]

with st.sidebar:
    st.markdown('<div class="sidebar-brand">Quartermaster</div>', unsafe_allow_html=True)

    page = st.radio(
        "Navigation",
        ALL_PAGES,
        key="nav_page",
        label_visibility="collapsed",
    )

    # Settings expander (absorbed from old Settings page)
    with st.expander("Settings"):
        st.markdown("**Project Defaults**")
        st.text(f"Schedule:  {app.config.get_setting('defaults.project.schedule', 'daily')}")
        st.text(f"Retention: {app.config.get_setting('defaults.project.retention_days', 30)}d")
        st.text(f"Time:      {app.config.get_setting('defaults.project.time', '02:00')}")

        st.markdown("**Database Defaults**")
        st.text(f"Schedule:  {app.config.get_setting('defaults.database.schedule', 'daily')}")
        st.text(f"Retention: {app.config.get_setting('defaults.database.retention_days', 14)}d")
        st.text(f"Time:      {app.config.get_setting('defaults.database.time', '03:00')}")

        st.markdown("**Storage Paths**")
        paths = app.config.get_storage_paths()
        st.markdown(f'<span class="mono-text">Local: {paths["local"]}</span>', unsafe_allow_html=True)
        if paths.get("sync"):
            st.markdown(f'<span class="mono-text">Sync: {paths["sync"]}</span>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown(
        f'<span style="color:#6b7280;font-size:0.75rem">Quartermaster v{APP_VERSION}</span>',
        unsafe_allow_html=True,
    )

# ── Dispatch to Page ─────────────────────────────────────────────────
PAGE_MAP[page](app)
