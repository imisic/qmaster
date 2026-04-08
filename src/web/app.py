"""Streamlit Web Dashboard for Quartermaster — thin dispatcher."""

import html
import os
import sys

import streamlit as st

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.state import init_app_state
from web.theme import apply_theme
from web.views import PAGE_MAP

APP_VERSION = "2.0.1"

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
SIDEBAR_NAV: list[dict[str, str]] = [
    {"type": "page", "key": "Dashboard"},
    {"type": "header", "label": "BACKUPS"},
    {"type": "page", "key": "Projects"},
    {"type": "page", "key": "Databases"},
    {"type": "page", "key": "Storage & Retention"},
    {"type": "header", "label": "UTILITIES"},
    {"type": "page", "key": "Claude Cleanup"},
    {"type": "page", "key": "Logs"},
    {"type": "page", "key": "Tools"},
]

VALID_PAGES = [item["key"] for item in SIDEBAR_NAV if item["type"] == "page"]

# Restore page from URL on first load so browser refresh keeps you put
if "nav_page" not in st.session_state:
    qp_page = st.query_params.get("page")
    st.session_state["nav_page"] = qp_page if qp_page in VALID_PAGES else VALID_PAGES[0]

with st.sidebar:
    st.markdown('<div class="sidebar-brand">Quartermaster</div>', unsafe_allow_html=True)

    for item in SIDEBAR_NAV:
        if item["type"] == "header":
            st.markdown(
                f'<div class="sidebar-section-header">{html.escape(item["label"])}</div>',
                unsafe_allow_html=True,
            )
        elif item["type"] == "divider":
            st.markdown('<hr class="sidebar-divider">', unsafe_allow_html=True)
        elif item["type"] == "page":
            is_active = st.session_state["nav_page"] == item["key"]
            btn_type = "primary" if is_active else "secondary"
            if st.button(
                item["key"],
                key=f"nav_btn_{item['key']}",
                use_container_width=True,
                type=btn_type,
            ):
                st.session_state["nav_page"] = item["key"]
                st.query_params["page"] = item["key"]
                st.rerun()

    st.markdown("---")
    st.markdown(
        f'<span style="color:#8b949e;font-size:0.75rem">Quartermaster v{APP_VERSION}</span>',
        unsafe_allow_html=True,
    )

page = st.session_state["nav_page"]

# Keep URL in sync with the resolved page
if st.query_params.get("page") != page:
    st.query_params["page"] = page

# ── Dispatch to Page ─────────────────────────────────────────────────
PAGE_MAP[page](app)
