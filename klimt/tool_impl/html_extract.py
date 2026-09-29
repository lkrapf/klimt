"""HTML parsers and text/link extraction used by webfetch.

Parsers:
- HTMLTextParser: compact visible text + hyperlinks from arbitrary HTML.

Plus helper utilities (`clean_text`, `clean_visible_text`, `is_html_content`,
`html_to_text`). Web search no longer parses HTML; it uses the Tavily JSON
API (see web.py).
"""
from __future__ import annotations

import html.parser
import urllib.parse

from .limits import WEBFETCH_MAX_LINKS


def clean_text(s: str) -> str:
    return " ".join(s.split())


def clean_visible_text(s: str) -> str:
    lines = [clean_text(line) for line in s.splitlines()]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if compact and not blank:
                compact.append("")
            blank = True
            continue
        compact.append(line)
        blank = False
    return "\n".join(compact).strip()


def is_html_content(content_type: str, url: str) -> bool:
    ctype = content_type.lower()
    if "text/html" in ctype or "application/xhtml+xml" in ctype:
        return True
    return not ctype and url.lower().split("?", 1)[0].endswith((".html", ".htm"))


class HTMLTextParser(html.parser.HTMLParser):
    """Extract compact visible text and links from HTML."""

    _skip_tags = {"script", "style", "noscript", "template", "svg"}
    _block_tags = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip_depth = 0
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._block_tags:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            self._link_href = urllib.parse.urljoin(self.base_url, href or "") if href else None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._link_href:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._link_href:
            text = clean_text("".join(self._link_text))
            href = self._link_href
            if text and href.startswith(("http://", "https://")):
                self.links.append((text, href))
            self._link_href = None
            self._link_text = []
        if tag in self._block_tags:
            self.parts.append("\n")


def html_to_text(body: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    parser = HTMLTextParser(base_url)
    parser.feed(body)
    text = clean_visible_text("".join(parser.parts))

    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for title, href in parser.links:
        if href in seen:
            continue
        seen.add(href)
        links.append((title, href))
        if len(links) >= WEBFETCH_MAX_LINKS:
            break
    return text, links

