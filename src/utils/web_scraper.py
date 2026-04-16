"""Web scraping and domain crawling utilities."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify

from utils.html_cleaner import HtmlCleaner, _fix_markdown_links

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightTimeoutError = Exception  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

_cleaner = HtmlCleaner()

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Analytics-heavy sites rarely reach networkidle. The goto() already waited for
# DOM content; this is a short bonus window for JS hydration, not a requirement.
_NETWORKIDLE_TIMEOUT_MS = 5_000

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
    def _fetch_on_context(ctx, url: str, timeout: int = 30) -> tuple[str, str]:
        """Fetch a URL on an existing Playwright browser context."""
        try:
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    log.debug("networkidle timeout for %s; using DOM content", url)
                return page.content(), ""
            finally:
                page.close()
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
            log.warning("Playwright fetch failed for %s: %s", url, reason)
            return "", reason

    @staticmethod
    @contextmanager
    def _playwright_context():
        """Yield a browser context shared across a batch, or an error string if Playwright can't launch.

        Reusing one context for all URLs in a batch looks like a real browser
        session — spinning up a fresh browser per URL trips bot-detection
        heuristics (e.g. ShieldSquare) that flag bursts of fresh fingerprints.
        """
        pw = None
        browser = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
        except Exception as e:
            if browser is not None:
                browser.close()
            if pw is not None:
                pw.stop()
            msg = str(e)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                reason = "Playwright browser not installed. Run: playwright install chromium"
            else:
                reason = f"{type(e).__name__}: {msg.splitlines()[0][:200]}"
            log.warning("Playwright launch failed: %s", reason)
            yield None, reason
            return

        try:
            yield ctx, ""
        finally:
            browser.close()
            pw.stop()

    @classmethod
    def _fetch_with_playwright(cls, url: str, timeout: int = 30) -> tuple[str, str]:
        """Single-shot Playwright fetch. Prefer ``_playwright_context`` + ``_fetch_on_context`` for batches."""
        with cls._playwright_context() as (ctx, err):
            if ctx is None:
                return "", err
            return cls._fetch_on_context(ctx, url, timeout=timeout)

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

    def _do_pw_fetch(self, url: str, timeout: int, pw_context) -> tuple[str, str]:
        """Fetch via a shared context when provided, else a single-shot browser."""
        if pw_context is not None:
            return self._fetch_on_context(pw_context, url, timeout=timeout)
        return self._fetch_with_playwright(url, timeout=timeout)

    def _fetch_and_convert(
        self, url: str, timeout: int = 10, js_mode: str = "never",
        pw_context=None,
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
                raw_html, pw_error = self._do_pw_fetch(url, timeout, pw_context)
                if not raw_html:
                    page.error = pw_error or "Playwright fetch returned empty response"
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

            if js_mode == "auto" and self._needs_js_rendering(raw_html, word_count):
                log.info("JS rendering detected for %s, retrying with Playwright", url)
                pw_html, _ = self._do_pw_fetch(url, timeout, pw_context)
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

    # Short delay between same-host requests. Keeps burst-rate bot heuristics happy
    # (e.g. ShieldSquare flags N rapid hits from a freshly-minted client).
    _SAME_HOST_DELAY_SEC = 1.5

    def scrape_urls(
        self,
        urls: list[str],
        timeout: int = 10,
        js_mode: str = "never",
        progress_callback: ProgressCallback = None,
    ) -> list[ScrapedPage]:
        """Fetch and convert a list of URLs."""
        normalized = []
        seen: set[str] = set()
        for raw in urls:
            url = self._normalize_url(raw)
            if url and url not in seen:
                normalized.append(url)
                seen.add(url)

        total = len(normalized)
        cm = self._pw_context_or_null(js_mode)
        with cm as (ctx, err):
            if ctx is None and js_mode == "always" and PLAYWRIGHT_AVAILABLE:
                pages = self._all_failed_pages(normalized, err)
            else:
                pages = self._scrape_loop(normalized, timeout, js_mode, ctx, progress_callback)

        if progress_callback:
            progress_callback(total, total, "Done")
        return pages

    def _pw_context_or_null(self, js_mode: str):
        """Return a Playwright context manager when JS is needed, else a null one."""
        if js_mode in ("auto", "always") and PLAYWRIGHT_AVAILABLE:
            return self._playwright_context()
        return nullcontext((None, ""))

    @staticmethod
    def _all_failed_pages(urls: list[str], error: str) -> list[ScrapedPage]:
        """Build a list of pre-errored ScrapedPage entries (used when the browser won't launch)."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return [
            ScrapedPage(url=u, domain=urlparse(u).netloc, scraped_at=now, error=error)
            for u in urls
        ]

    @classmethod
    def _pace_host(cls, url: str, last_hit: dict[str, float]) -> str:
        """Sleep if the URL's host was hit within ``_SAME_HOST_DELAY_SEC``. Returns the host."""
        host = urlparse(url).netloc
        wait = cls._SAME_HOST_DELAY_SEC - (time.monotonic() - last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        return host

    def _scrape_loop(
        self,
        urls: list[str],
        timeout: int,
        js_mode: str,
        pw_context,
        progress_callback: ProgressCallback,
    ) -> list[ScrapedPage]:
        """Sequential fetch loop with per-host pacing."""
        pages: list[ScrapedPage] = []
        total = len(urls)
        last_hit: dict[str, float] = {}
        for i, url in enumerate(urls):
            if progress_callback:
                progress_callback(i, total, url)
            host = self._pace_host(url, last_hit)
            page, _ = self._fetch_and_convert(url, timeout, js_mode=js_mode, pw_context=pw_context)
            pages.append(page)
            last_hit[host] = time.monotonic()
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

        with self._pw_context_or_null(js_mode) as (ctx, err):
            if ctx is None and js_mode == "always" and PLAYWRIGHT_AVAILABLE:
                pages = self._all_failed_pages(list(queue), err)
            else:
                pages = self._crawl_loop(
                    queue, visited, allowed_domains, max_pages, timeout,
                    js_mode, ctx, progress_callback,
                )

        if progress_callback:
            progress_callback(len(pages), max_pages, "Done")

        return pages

    def _crawl_loop(
        self,
        queue: deque,
        visited: set[str],
        allowed_domains: set[str],
        max_pages: int,
        timeout: int,
        js_mode: str,
        pw_context,
        progress_callback: ProgressCallback,
    ) -> list[ScrapedPage]:
        pages: list[ScrapedPage] = []
        last_hit: dict[str, float] = {}
        while queue and len(pages) < max_pages:
            url = queue.popleft()

            if progress_callback:
                progress_callback(len(pages), max_pages, url)

            host = self._pace_host(url, last_hit)
            page, raw_html = self._fetch_and_convert(
                url, timeout, js_mode=js_mode, pw_context=pw_context,
            )
            pages.append(page)
            last_hit[host] = time.monotonic()

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
