"""HTTP-based tools: webfetch and websearch (web + images)."""
from __future__ import annotations

import urllib.parse
import urllib.request

from .html_extract import (
    StartpageHTMLParser,
    StartpageImageParser,
    clean_text,
    html_to_text,
    is_html_content,
)
from .limits import (
    WEBFETCH_MAX_BYTES,
    WEBFETCH_MAX_TEXT_CHARS,
    WEBFETCH_TIMEOUT,
    WEBSEARCH_MAX_IMAGE_RESULTS,
    WEBSEARCH_MAX_RESULTS,
)


def _startpage_fetch(query: str, category: str) -> str:
    """Fetch raw HTML from Startpage for the given query and category."""
    url = "https://www.startpage.com/sp/search?" + urllib.parse.urlencode(
        {"query": query, "cat": category}
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=WEBFETCH_TIMEOUT) as r:  # noqa: S310
        raw = r.read(WEBFETCH_MAX_BYTES)
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def websearch(query: str, category: str = "web") -> str:
    query = query.strip()
    if not query:
        return "error: empty query"

    if category == "images":
        return _websearch_images(query)

    html_text = _startpage_fetch(query, "web")
    parser = StartpageHTMLParser()
    parser.feed(html_text)
    results = [
        {
            "title": clean_text(item.get("title", "")),
            "url": clean_text(item.get("url", "")),
            "snippet": clean_text(item.get("snippet", "")),
        }
        for item in parser.results
        if item.get("title") and item.get("url")
    ][:WEBSEARCH_MAX_RESULTS]

    if not results:
        return f"no results for: {query}"

    lines = [f"query: {query}", ""]
    for i, item in enumerate(results, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   {item['url']}")
        if item["snippet"]:
            lines.append(f"   {item['snippet']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _websearch_images(query: str) -> str:
    html_text = _startpage_fetch(query, "images")
    parser = StartpageImageParser()
    parser.feed(html_text)
    results = [
        {
            "title": clean_text(item.get("title", "")),
            "image_url": clean_text(item.get("image_url", "")),
            "thumbnail_url": clean_text(item.get("thumbnail_url", "")),
            "source_url": clean_text(item.get("source_url", "")),
        }
        for item in parser.results
        if item.get("image_url")
    ][:WEBSEARCH_MAX_IMAGE_RESULTS]

    if not results:
        return f"no image results for: {query}"

    lines = [f"query: {query}", ""]
    for i, item in enumerate(results, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   image:     {item['image_url']}")
        lines.append(f"   thumbnail: {item['thumbnail_url']}")
        if item["source_url"]:
            lines.append(f"   source:    {item['source_url']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def webfetch(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "error: only http:// and https:// URLs are supported"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Klimt/0 webfetch",
            "Accept": "text/*, application/json, application/xml, application/xhtml+xml, */*;q=0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=WEBFETCH_TIMEOUT) as r:  # noqa: S310
        raw = r.read(WEBFETCH_MAX_BYTES + 1)
        truncated = len(raw) > WEBFETCH_MAX_BYTES
        raw = raw[:WEBFETCH_MAX_BYTES]
        charset = r.headers.get_content_charset() or "utf-8"
        body = raw.decode(charset, errors="replace")
        final_url = r.geturl()
        content_type = r.headers.get("Content-Type", "")
        headers = "".join(f"{k}: {v}\n" for k, v in r.headers.items())
        note = f"\n[truncated to {WEBFETCH_MAX_BYTES} bytes]" if truncated else ""

        if is_html_content(content_type, final_url):
            text, links = html_to_text(body, final_url)
            text_truncated = len(text) > WEBFETCH_MAX_TEXT_CHARS
            text = text[:WEBFETCH_MAX_TEXT_CHARS].rstrip()
            text_note = (
                f"\n[visible text truncated to {WEBFETCH_MAX_TEXT_CHARS} chars]"
                if text_truncated else ""
            )
            link_lines = ""
            if links:
                link_lines = "\n--- links ---\n" + "".join(
                    f"- {title}: {href}\n" for title, href in links
                )
            return (
                f"url: {final_url}\n"
                f"status: {r.status} {r.reason}\n"
                f"--- headers ---\n{headers}"
                "--- body: visible text extracted from HTML ---\n"
                f"{text or '[no visible text extracted]'}{text_note}{note}"
                f"{link_lines}"
            )

        return (
            f"url: {final_url}\n"
            f"status: {r.status} {r.reason}\n"
            f"--- headers ---\n{headers}"
            f"--- body ---\n{body}{note}"
        )
