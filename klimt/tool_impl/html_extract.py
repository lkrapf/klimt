"""HTML parsers and text/link extraction used by web tools.

Three parsers:
- HTMLTextParser: compact visible text + hyperlinks from arbitrary HTML.
- StartpageHTMLParser: Startpage organic web result blocks.
- StartpageImageParser: Startpage image result blocks.

Plus helper utilities (`clean_text`, `clean_visible_text`, `is_html_content`,
`html_to_text`).
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


class StartpageImageParser(html.parser.HTMLParser):
    """Parse image search results from Startpage HTML.

    Image results are in ``<div class="image-container ..." data-thumbnail-url="...">``
    elements. The raw image URL is encoded in the thumbnail URL's `piurl`
    query parameter; the source page URL appears in a sibling `<span>`.
    """

    _skip_tags = {"script", "style", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_skip = 0
        self._in_source_span = False

    @staticmethod
    def _decode_piurl(thumbnail_url: str) -> str:
        """Extract and decode the piurl parameter from a Startpage proxy URL."""
        try:
            qs = urllib.parse.urlparse(thumbnail_url).query
            params = urllib.parse.parse_qs(qs)
            piurls = params.get("piurl") or params.get("piUrl")
            if piurls:
                return piurls[0]
        except Exception:
            pass
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._in_skip += 1
            return
        if self._in_skip:
            return

        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class") or ""

        if tag == "div" and "image-container" in cls:
            thumbnail_url = attrs_dict.get("data-thumbnail-url") or ""
            title = attrs_dict.get("aria-label") or ""
            # Strip "click to expand image: " prefix Startpage adds.
            if title.lower().startswith("click to expand image:"):
                title = title[len("click to expand image:"):].strip()
            raw_image_url = self._decode_piurl(thumbnail_url)
            self._current = {
                "title": title,
                "thumbnail_url": thumbnail_url,
                "image_url": raw_image_url,
                "source_url": "",
            }
            return

        if self._current is not None and tag == "span" and "css-1pi0dfq" in cls:
            self._in_source_span = True

    def handle_data(self, data: str) -> None:
        if self._in_skip or self._current is None:
            return
        if self._in_source_span:
            self._current["source_url"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            if self._in_skip:
                self._in_skip -= 1
            return
        if self._in_skip:
            return
        if tag == "span" and self._in_source_span:
            self._in_source_span = False
        if tag == "div" and self._current is not None:
            if self._current.get("image_url"):
                self.results.append(self._current)
            self._current = None
            self._in_source_span = False


class StartpageHTMLParser(html.parser.HTMLParser):
    """Parse organic web search results from Startpage HTML.

    Each result is wrapped in ``<div class="result ...">``. Nested ``<div>``s
    require tracking depth to find the matching closing tag.
    """

    _skip_tags = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._depth = 0  # div depth inside the current result
        self._in_title_link = False
        self._in_description = False
        self._skip_depth = 0

    @staticmethod
    def _has_class(cls: str, name: str) -> bool:
        return name in cls.split()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class") or ""

        # Start of a result block: <div class="result ...">
        if (
            tag == "div"
            and self._current is None
            and self._has_class(cls, "result")
        ):
            self._current = {"title": "", "url": "", "snippet": ""}
            self._depth = 1
            return

        if self._current is None:
            return

        if tag == "div":
            self._depth += 1
            return

        # Title link: <a class="result-title result-link ..." href="...">
        if (
            tag == "a"
            and self._has_class(cls, "result-title")
            and self._has_class(cls, "result-link")
        ):
            self._current["url"] = attrs_dict.get("href") or ""
            self._in_title_link = True
        # Description/snippet: <p class="description ...">
        elif tag == "p" and self._has_class(cls, "description"):
            self._in_description = True

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._current is None:
            return
        if self._in_title_link:
            self._current["title"] += data
        elif self._in_description:
            self._current["snippet"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or self._current is None:
            return
        if tag == "a" and self._in_title_link:
            self._in_title_link = False
        elif tag == "p" and self._in_description:
            self._in_description = False
        elif tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                if self._current.get("title") and self._current.get("url"):
                    self.results.append(self._current)
                self._current = None
                self._in_title_link = False
                self._in_description = False
