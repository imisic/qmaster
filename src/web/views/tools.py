"""Tools page — combines HTML Cleaner, Web Scraper, and Text Sanitizer as tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from web.views.html_cleaner import render_html_cleaner
from web.views.text_sanitizer import render_text_sanitizer
from web.views.web_scraper import render_web_scraper

if TYPE_CHECKING:
    from web.state import AppComponents


def render_tools(app: AppComponents) -> None:
    """Render the Tools page with three tools as tabs."""
    st.markdown('<div class="page-title">Tools</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-subtitle">HTML cleaning, web scraping, and PII sanitization</div>',
        unsafe_allow_html=True,
    )

    tab_html, tab_scraper, tab_sanitizer = st.tabs(
        ["HTML Cleaner", "Web Scraper", "Text Sanitizer"]
    )

    with tab_html:
        render_html_cleaner(app)
    with tab_scraper:
        render_web_scraper(app)
    with tab_sanitizer:
        render_text_sanitizer(app)
