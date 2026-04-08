"""Tools page — combines HTML Cleaner, Web Scraper, and Text Sanitizer as tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from web.components import page_header
from web.views.html_cleaner import _render_html_cleaner
from web.views.text_sanitizer import _render_text_sanitizer
from web.views.web_scraper import _render_web_scraper

if TYPE_CHECKING:
    from web.state import AppComponents


def render_tools(app: AppComponents) -> None:
    """Render the Tools page with three tools as tabs."""
    page_header("Tools", "HTML cleaning, web scraping, and PII sanitization")

    tab_html, tab_scraper, tab_sanitizer = st.tabs(
        ["HTML Cleaner", "Web Scraper", "Text Sanitizer"]
    )

    with tab_html:
        _render_html_cleaner(app)
    with tab_scraper:
        _render_web_scraper(app)
    with tab_sanitizer:
        _render_text_sanitizer(app)
