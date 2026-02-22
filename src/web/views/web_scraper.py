"""Web Scraper view for the Quartermaster dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import streamlit as st

from utils.web_scraper import PLAYWRIGHT_AVAILABLE, WebScraper
from web.cache import invalidate

if TYPE_CHECKING:
    from web.state import AppComponents

_scraper: WebScraper | None = None


def _get_scraper() -> WebScraper:
    global _scraper
    if _scraper is None:
        _scraper = WebScraper()
    return _scraper


def render_web_scraper(app: AppComponents | None = None) -> None:
    """Render the Web Scraper page."""
    st.title("Web Scraper")
    st.caption("Fetch URLs and convert to clean Markdown")

    # ── URL input ────────────────────────────────────────────────────
    url_input = st.text_area(
        "URLs (one per line)",
        height=150,
        key="web_scraper_urls",
        placeholder="https://example.com\nhttps://example.com/about",
    )

    # ── Mode selection ───────────────────────────────────────────────
    mode = st.radio(
        "Scrape mode",
        ["Listed URLs only", "Crawl domain"],
        horizontal=True,
        label_visibility="collapsed",
    )

    # ── JS rendering ────────────────────────────────────────────────
    js_options = ["Auto-detect", "Always", "Never"]
    js_rendering = st.selectbox(
        "JS rendering", js_options, index=0,
        help="Auto fetches static HTML first, retries with headless browser when content looks JS-rendered",
    )
    js_mode = {"Never": "never", "Auto-detect": "auto", "Always": "always"}[js_rendering]

    if js_mode != "never" and not PLAYWRIGHT_AVAILABLE:
        st.warning("Install: `pip install playwright && playwright install chromium`")
        js_mode = "never"

    max_pages = 50
    if mode == "Crawl domain":
        max_pages = st.slider("Max pages to crawl", 10, 100, 50)

    # Clear output on input change
    prev_input = st.session_state.get("web_scraper_prev_input", "")
    prev_mode = st.session_state.get("web_scraper_prev_mode", "")
    current_key = f"{url_input}|{mode}|{max_pages}|{js_mode}"
    if current_key != prev_input or mode != prev_mode:
        st.session_state.pop("web_scraper_output", None)
        st.session_state.pop("web_scraper_stats", None)

    # ── Scrape button ────────────────────────────────────────────────
    st.markdown("---")
    if st.button("Scrape", type="primary", use_container_width=True):
        raw_urls = [line.strip() for line in (url_input or "").splitlines() if line.strip()]
        if not raw_urls:
            st.warning("Enter at least one URL.")
            st.stop()

        st.session_state["web_scraper_prev_input"] = current_key
        st.session_state["web_scraper_prev_mode"] = mode

        progress_bar = st.progress(0, text="Starting...")

        def on_progress(current: int, total: int, url: str) -> None:
            if total > 0:
                progress_bar.progress(
                    min(current / total, 1.0),
                    text=f"({current}/{total}) {url}" if url != "Done" else "Done!",
                )

        if mode == "Listed URLs only":
            pages = _get_scraper().scrape_urls(raw_urls, js_mode=js_mode, progress_callback=on_progress)
        else:
            pages = _get_scraper().crawl_domain(raw_urls, max_pages=max_pages, js_mode=js_mode, progress_callback=on_progress)

        progress_bar.empty()

        # Show per-URL errors
        errors = [p for p in pages if p.error]
        for p in errors:
            st.warning(f"{p.url}: {p.error}")

        successful = [p for p in pages if not p.error]
        if not successful and errors:
            st.error("All URLs failed.")
            st.stop()

        output = _get_scraper().format_output(pages)
        total_words = sum(p.word_count for p in successful)

        st.session_state["web_scraper_output"] = output
        st.session_state["web_scraper_stats"] = {
            "pages": len(successful),
            "errors": len(errors),
            "words": total_words,
        }
        invalidate()
        st.rerun()

    # ── Output ───────────────────────────────────────────────────────
    output = st.session_state.get("web_scraper_output")
    stats = st.session_state.get("web_scraper_stats")

    if output is not None and stats is not None:
        st.markdown(
            f"**{stats['pages']} pages** scraped | "
            f"**{stats['words']:,}** words"
            + (f" | **{stats['errors']}** errors" if stats["errors"] else "")
        )
        st.code(output, language="markdown")
        st.download_button(
            "Download Markdown",
            data=output,
            file_name=f"scraped_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
            mime="text/markdown",
        )
