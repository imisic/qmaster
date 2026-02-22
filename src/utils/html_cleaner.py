"""HTML cleaning and conversion utilities."""

import re

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify


class HtmlCleaner:
    """Clean and convert HTML content to various formats."""

    # Tags to always remove entirely (structural/minimal modes)
    _REMOVE_TAGS = ["script", "style", "noscript"]

    # Elements that never produce visible content (markdown/text modes)
    _INVISIBLE_TAGS = [
        "script", "style", "noscript", "meta", "link",
        "svg", "template",
    ]

    # Form elements stripped for markdown/text
    _FORM_TAGS = ["input", "select", "textarea", "button"]

    # CSS utility classes that mark non-visible content
    _HIDDEN_CLASS_RE = re.compile(
        r"\bd-none\b|\bd-print-none\b|\bhidden\b|\bsr-only\b"
        r"|\bvisually-hidden\b|\binvalid-feedback\b|\bvalid-feedback\b"
    )

    # Inline style patterns that hide content
    _HIDDEN_STYLE_RE = re.compile(
        r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE,
    )

    # Block-level elements (for text extraction line-break handling)
    _BLOCK_TAGS = frozenset({
        "address", "article", "aside", "blockquote", "dd", "details",
        "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
        "nav", "ol", "p", "pre", "section", "summary", "table", "tbody",
        "tfoot", "thead", "tr", "ul",
    })

    # Tags whose cells signal layout when they contain block content
    _BLOCK_CONTENT_TAGS = frozenset({
        "table", "div", "p", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
        "h6", "blockquote", "form", "section", "article", "header",
        "footer", "nav", "figure", "details",
    })

    # Presentational attributes to strip
    _PRESENTATIONAL_ATTRS = {
        "style", "bgcolor", "background", "border", "cellpadding",
        "cellspacing", "width", "height", "align", "valign",
        "color", "face", "size", "noshade",
    }

    # Attributes to keep in structural mode
    _STRUCTURAL_KEEP = {
        "class", "id", "name", "type", "for", "href", "src", "alt",
        "title", "role", "action", "method", "value", "placeholder",
        "colspan", "rowspan",
    }

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _collapse_blank_lines(text: str) -> str:
        """Collapse 3+ consecutive blank lines to 2."""
        return re.sub(r"\n{3,}", "\n\n", text)

    @staticmethod
    def _strip_trailing_whitespace(text: str) -> str:
        """Strip trailing whitespace from each line."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    # ── pre-cleaning (shared by markdown & text) ─────────────────────

    def _pre_clean(self, soup: BeautifulSoup, strip_forms: bool = True,
                   strip_table_links: bool = True) -> None:
        """Remove invisible, form, and hidden-class elements from *soup*."""
        for tag in soup.find_all(self._INVISIBLE_TAGS):
            tag.decompose()

        if strip_forms:
            for tag in soup.find_all(self._FORM_TAGS):
                tag.decompose()

        # Elements hidden via CSS utility classes
        for tag in soup.find_all(class_=self._HIDDEN_CLASS_RE):
            tag.decompose()

        # Elements with aria-hidden="true"
        for tag in soup.find_all(attrs={"aria-hidden": "true"}):
            tag.decompose()

        # Elements hidden via inline styles (display:none, visibility:hidden)
        for tag in soup.find_all(style=self._HIDDEN_STYLE_RE):
            tag.decompose()

        # Presentational table markup that carries no content
        for tag in soup.find_all(["col", "colgroup"]):
            tag.decompose()

        # Strip junk images: tracking pixels (1x1) and data: URIs
        for img in list(soup.find_all("img")):
            src = img.get("src", "")
            w, h = img.get("width", ""), img.get("height", "")
            if w == "1" and h == "1":
                img.decompose()
                continue
            if src.startswith("data:"):
                alt = img.get("alt", "").strip()
                if alt:
                    img.replace_with(alt)
                else:
                    img.decompose()

        # Inside tables: unwrap <a> tags (keep text, drop navigation hrefs)
        if strip_table_links:
            for table in soup.find_all("table"):
                for a_tag in table.find_all("a"):
                    a_tag.unwrap()

    # ── table fixups (markdown only) ─────────────────────────────────

    @staticmethod
    def _has_own_headers(table) -> bool:
        """Check if *table* has its own <th>/<thead> (not from nested tables)."""
        if table.find("thead", recursive=False):
            return True
        for child in table.children:
            if getattr(child, "name", None) == "tr":
                if child.find("th", recursive=False):
                    return True
            elif getattr(child, "name", None) in ("tbody", "tfoot"):
                for tr in child.find_all("tr", recursive=False):
                    if tr.find("th", recursive=False):
                        return True
        return False

    def _unwrap_layout_tables(self, soup: BeautifulSoup) -> None:
        """Unwrap single-column wrapper tables used for layout.

        Email and legacy HTML use ``<table>`` for positioning.  Nested
        layout tables produce garbled markdown.  We unwrap single-column
        tables whose cells contain block-level content or that consist
        of a single row (always a wrapper).  Only checks the table's
        *own* headers — nested data tables are left alone.
        """
        changed = True
        while changed:
            changed = False
            for table in list(soup.find_all("table")):
                if self._has_own_headers(table):
                    continue

                rows = table.find_all("tr", recursive=False)
                for sec in table.find_all(["tbody", "tfoot"], recursive=False):
                    rows.extend(sec.find_all("tr", recursive=False))

                if not rows:
                    continue

                # Every row must have exactly one cell
                if not all(
                    len(r.find_all(["td", "th"], recursive=False)) == 1
                    for r in rows
                ):
                    continue

                # Single-row table is always a wrapper.
                # Multi-row: need block content as a layout signal.
                if len(rows) > 1:
                    has_block = any(
                        cell.find(self._BLOCK_CONTENT_TAGS)
                        for r in rows
                        for cell in r.find_all(["td", "th"], recursive=False)
                    )
                    if not has_block:
                        continue

                # Unwrap: cells → rows → sections → table
                for sec in table.find_all(["thead", "tbody", "tfoot"],
                                          recursive=False):
                    for tr in sec.find_all("tr", recursive=False):
                        for cell in tr.find_all(["td", "th"], recursive=False):
                            cell.unwrap()
                        tr.unwrap()
                    sec.unwrap()
                for tr in table.find_all("tr", recursive=False):
                    for cell in tr.find_all(["td", "th"], recursive=False):
                        cell.unwrap()
                    tr.unwrap()
                table.unwrap()
                changed = True

    def _promote_first_row_to_header(self, soup: BeautifulSoup) -> None:
        """Promote first row ``<td>`` → ``<th>`` for headerless tables.

        Tables pasted from Excel / Sheets use ``<td>`` everywhere.
        Without ``<th>``, markdownify skips the separator row, producing
        invalid markdown.
        """
        for table in soup.find_all("table"):
            if self._has_own_headers(table):
                continue
            first_tr = table.find("tr")
            if not first_tr:
                continue
            for td in first_tr.find_all("td", recursive=False):
                td.name = "th"

    # ── public conversion methods ────────────────────────────────────

    def to_markdown(self, html: str) -> str:
        """Convert HTML to Markdown."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            self._pre_clean(soup)
            self._unwrap_layout_tables(soup)
            self._promote_first_row_to_header(soup)
            result = markdownify(
                str(soup),
                heading_style="ATX",
                bullets="-",
            )
            result = self._strip_trailing_whitespace(result)
            result = self._collapse_blank_lines(result)
            return result.strip()
        except Exception as e:
            return f"Error converting to Markdown: {e}"

    def to_structural(self, html: str) -> str:
        """Strip presentational attributes, keep structural ones."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup.find_all(self._REMOVE_TAGS):
                tag.decompose()

            for tag in soup.find_all(True):
                attrs_to_remove = []
                for attr in list(tag.attrs):
                    if attr.startswith("aria-"):
                        continue
                    if attr.startswith("data-") or attr.startswith("on"):
                        attrs_to_remove.append(attr)
                    elif attr in self._PRESENTATIONAL_ATTRS:
                        attrs_to_remove.append(attr)
                    elif attr not in self._STRUCTURAL_KEEP:
                        attrs_to_remove.append(attr)
                for attr in attrs_to_remove:
                    del tag[attr]

            return soup.prettify().strip()
        except Exception as e:
            return f"Error cleaning structural HTML: {e}"

    def to_minimal(self, html: str) -> str:
        """Strip nearly all attributes and extra tags for minimal HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup.find_all(self._REMOVE_TAGS + ["svg", "link", "meta"]):
                tag.decompose()

            media_tags = {"img", "video", "audio", "source", "iframe"}

            for tag in soup.find_all(True):
                saved = {}
                tag_name = tag.name

                if tag_name == "a" and tag.get("href"):
                    saved["href"] = tag["href"]
                if tag_name in media_tags and tag.get("src"):
                    saved["src"] = tag["src"]
                if tag_name == "img" and tag.get("alt"):
                    saved["alt"] = tag["alt"]

                tag.attrs = saved

            return soup.prettify().strip()
        except Exception as e:
            return f"Error cleaning minimal HTML: {e}"

    def to_text(self, html: str) -> str:
        """Extract plain text from HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            self._pre_clean(soup, strip_table_links=False)

            # Replace <br> with newlines before extraction
            for br in soup.find_all("br"):
                br.replace_with("\n")

            # Preserve image alt text (images have no text nodes)
            for img in soup.find_all("img"):
                alt = img.get("alt", "").strip()
                if alt:
                    img.replace_with(alt)
                else:
                    img.decompose()

            # Strip whitespace-only text nodes between table cells so
            # that cells stay on one line instead of splitting per-cell.
            for tr in soup.find_all("tr"):
                for child in list(tr.children):
                    if isinstance(child, NavigableString) and not child.strip():
                        child.extract()

            # Insert newlines around block elements, tabs between cells.
            # Block elements *inside* table cells are skipped so that
            # cell content stays on a single line.
            for tag in soup.find_all(True):
                if tag.name in ("td", "th"):
                    tag.append("\t")
                elif tag.name in self._BLOCK_TAGS:
                    if not tag.find_parent(["td", "th"]):
                        tag.insert_before("\n")
                        tag.append("\n")

            text = soup.get_text()
            # Collapse horizontal whitespace (preserve newlines and tabs)
            text = re.sub(r"[^\S\n\t]+", " ", text)
            text = text.replace("\t", "  ")
            text = self._collapse_blank_lines(text)
            text = self._strip_trailing_whitespace(text)
            return text.strip()
        except Exception as e:
            return f"Error extracting text: {e}"
