"""Web scraping and domain crawling utilities."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify

from utils.html_cleaner import HtmlCleaner, _fix_markdown_links

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

log = logging.getLogger(__name__)

_cleaner = HtmlCleaner()

_USER_AGENT = "Mozilla/5.0 (compatible; QMaster/1.0)"

# Extensions to skip during crawl
_SKIP_EXTENSIONS = frozenset({
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dmg", ".bin", ".iso",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
})

ProgressCallback = Callable[[int, int, str], None] | None


@dataclass
class ScrapedPage:
    """Result of scraping a single URL."""
    url: str
    title: str = ""
    domain: str = ""
    scraped_at: str = ""
    word_count: int = 0
    links_found: int = 0
    markdown: str = ""
    error: str = ""


class WebScraper:
    """Fetch URLs and convert their content to Markdown."""

    @staticmethod
    def _scraper_pre_clean(soup: BeautifulSoup) -> None:
        """Lighter pre-clean for scraping — keeps hidden-class elements.

        Unlike HtmlCleaner._pre_clean, this does NOT strip elements with CSS
        utility classes like Tailwind's ``hidden`` which modern frameworks use
        for responsive layouts and hydration placeholders.
        """
        for tag in soup.find_all(["script", "style", "noscript", "meta",
                                   "link", "svg", "template"]):
            tag.decompose()

    @staticmethod
    def _html_to_markdown(html: str) -> tuple[str, str, int, int]:
        """Convert raw HTML to Markdown. Returns (title, markdown, word_count, links_found)."""
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        links_found = len(soup.find_all("a", href=True))

        WebScraper._scraper_pre_clean(soup)
        md = markdownify(str(soup), heading_style="ATX", bullets="-")
        md = _fix_markdown_links(md)
        md = _cleaner._strip_trailing_whitespace(md)
        md = _cleaner._collapse_blank_lines(md)
        md = md.strip()
        word_count = len(md.split())

        return title, md, word_count, links_found

    @staticmethod
    def _fetch_with_playwright(url: str, timeout: int = 30) -> str:
        """Fetch a URL using headless Chromium. Returns HTML or empty string on failure."""
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(user_agent=_USER_AGENT)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                # Wait for JS frameworks to hydrate content
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            log.warning("Playwright fetch failed for %s: %s", url, e)
            return ""

    @staticmethod
    def _needs_js_rendering(html: str, word_count: int) -> bool:
        """Detect pages that likely need JS rendering to get real content."""
        if word_count >= 50:
            return False

        js_signals = (
            "self.__next_f" in html,
            "__NEXT_DATA__" in html,
            "__NUXT__" in html,
            'id="root"><' in html,
            'id="app"><' in html,
        )
        if any(js_signals):
            return True

        html_lower = html.lower()
        if "<noscript" in html_lower and "enable javascript" in html_lower:
            return True

        return False

    def _fetch_and_convert(
        self, url: str, timeout: int = 10, js_mode: str = "never",
    ) -> tuple[ScrapedPage, str]:
        """Fetch a single URL and convert its HTML to Markdown.

        Returns (page, raw_html) so callers can reuse HTML for link extraction.
        """
        page = ScrapedPage(
            url=url,
            domain=urlparse(url).netloc,
            scraped_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        raw_html = ""

        safe, reason = self._is_safe_url(url)
        if not safe:
            page.error = f"URL blocked: {reason}"
            return page, raw_html

        if js_mode != "never" and not PLAYWRIGHT_AVAILABLE:
            page.error = (
                "Playwright not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
            return page, raw_html

        try:
            if js_mode == "always":
                raw_html = self._fetch_with_playwright(url, timeout=timeout)
                if not raw_html:
                    page.error = "Playwright fetch returned empty response"
                    return page, raw_html
            else:
                resp = requests.get(url, timeout=timeout, headers={
                    "User-Agent": _USER_AGENT,
                })
                resp.raise_for_status()

                content_type = resp.headers.get("Content-Type", "")
                if "html" not in content_type and "text" not in content_type:
                    page.error = f"Non-HTML content type: {content_type}"
                    return page, raw_html

                raw_html = resp.text

            title, md, word_count, links_found = self._html_to_markdown(raw_html)

            # Auto-detect: re-fetch with Playwright if static content looks empty
            if js_mode == "auto" and self._needs_js_rendering(raw_html, word_count):
                log.info("JS rendering detected for %s, retrying with Playwright", url)
                pw_html = self._fetch_with_playwright(url, timeout=timeout)
                if pw_html:
                    raw_html = pw_html
                    title, md, word_count, links_found = self._html_to_markdown(raw_html)

            page.title = title
            page.markdown = md
            page.word_count = word_count
            page.links_found = links_found

        except requests.RequestException as e:
            page.error = str(e)
        except Exception as e:
            page.error = f"Unexpected error: {e}"

        return page, raw_html

    @staticmethod
    def _extract_same_domain_links(
        soup: BeautifulSoup, base_url: str, allowed_domains: set[str],
    ) -> list[str]:
        """Extract same-domain links from parsed HTML."""
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].split("#")[0].strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue

            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)

            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc not in allowed_domains:
                continue

            # Skip binary/non-HTML extensions
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in _SKIP_EXTENSIONS):
                continue

            # Normalize: drop fragment, keep scheme+netloc+path+query
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean += f"?{parsed.query}"
            links.append(clean)

        return links

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize a user-provided URL, auto-prepending https:// if needed."""
        url = url.strip()
        if not url:
            return ""
        if not re.match(r"https?://", url, re.IGNORECASE):
            url = "https://" + url
        return url

    @staticmethod
    def _is_safe_url(url: str) -> tuple[bool, str]:
        """Validate that a URL doesn't point to private/reserved networks.

        Resolves the hostname and checks the IP against blocked ranges
        to prevent SSRF attacks.
        """
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False, f"Blocked scheme: {parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return False, "No hostname in URL"

        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            return False, f"Cannot resolve hostname: {hostname}"

        for family, _, _, _, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"Blocked: {hostname} resolves to private/reserved IP {ip}"

        return True, ""

    def scrape_urls(
        self,
        urls: list[str],
        timeout: int = 10,
        js_mode: str = "never",
        progress_callback: ProgressCallback = None,
    ) -> list[ScrapedPage]:
        """Fetch and convert a list of URLs."""
        # Normalize and deduplicate
        normalized = []
        seen: set[str] = set()
        for raw in urls:
            url = self._normalize_url(raw)
            if url and url not in seen:
                normalized.append(url)
                seen.add(url)

        pages = []
        total = len(normalized)
        for i, url in enumerate(normalized):
            if progress_callback:
                progress_callback(i, total, url)
            page, _ = self._fetch_and_convert(url, timeout, js_mode=js_mode)
            pages.append(page)

        if progress_callback:
            progress_callback(total, total, "Done")

        return pages

    def crawl_domain(
        self,
        seed_urls: list[str],
        max_pages: int = 50,
        timeout: int = 10,
        js_mode: str = "never",
        progress_callback: ProgressCallback = None,
    ) -> list[ScrapedPage]:
        """BFS crawl from seed URLs, following same-domain links."""
        # Normalize seeds and collect allowed domains
        queue: deque[str] = deque()
        visited: set[str] = set()
        allowed_domains: set[str] = set()

        for raw in seed_urls:
            url = self._normalize_url(raw)
            if url and url not in visited:
                queue.append(url)
                visited.add(url)
                allowed_domains.add(urlparse(url).netloc)

        pages = []
        while queue and len(pages) < max_pages:
            url = queue.popleft()

            if progress_callback:
                progress_callback(len(pages), max_pages, url)

            page, raw_html = self._fetch_and_convert(url, timeout, js_mode=js_mode)
            pages.append(page)

            # Reuse fetched HTML for link extraction instead of re-fetching
            if not page.error and raw_html:
                try:
                    soup = BeautifulSoup(raw_html, "html.parser")
                    new_links = self._extract_same_domain_links(
                        soup, url, allowed_domains,
                    )
                    for link in new_links:
                        if link not in visited and len(visited) < max_pages * 2:
                            visited.add(link)
                            queue.append(link)
                except Exception:
                    log.warning("Link extraction failed for %s", url, exc_info=True)

        if progress_callback:
            progress_callback(len(pages), max_pages, "Done")

        return pages

    @staticmethod
    def format_output(pages: list[ScrapedPage]) -> str:
        """Format scraped pages as Markdown with YAML frontmatter."""
        blocks = []
        for page in pages:
            if page.error:
                block = (
                    f"---\n"
                    f"url: {page.url}\n"
                    f"error: {page.error}\n"
                    f"scraped_at: {page.scraped_at}\n"
                    f"---\n"
                )
            else:
                block = (
                    f"---\n"
                    f"url: {page.url}\n"
                    f"title: {page.title}\n"
                    f"domain: {page.domain}\n"
                    f"scraped_at: {page.scraped_at}\n"
                    f"word_count: {page.word_count}\n"
                    f"links_found: {page.links_found}\n"
                    f"---\n\n"
                    f"{page.markdown}\n"
                )
            blocks.append(block)

        return "\n\n".join(blocks)
