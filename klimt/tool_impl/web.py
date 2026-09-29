"""HTTP-based tools: webfetch and websearch (web + images).

Search is backed by the Tavily API (https://api.tavily.com), which returns
clean JSON built for agent use. The key is read from the ``TAVILY_API_KEY``
environment variable; no secret is stored in the tree.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .html_extract import (
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

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _tavily_search(query: str, *, include_images: bool, max_results: int) -> dict:
    """POST a query to the Tavily search API and return the parsed JSON.

    Raises RuntimeError with a caller-friendly message on auth/HTTP errors.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not set")

    payload: dict = {"query": query, "max_results": max_results}
    if include_images:
        payload["include_images"] = True
        payload["include_image_descriptions"] = True

    req = urllib.request.Request(
        TAVILY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=WEBFETCH_TIMEOUT) as r:  # noqa: S310
            raw = r.read(WEBFETCH_MAX_BYTES)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        raise RuntimeError(f"Tavily HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Tavily request failed: {e.reason}") from e
    return json.loads(raw.decode("utf-8", errors="replace"))


def websearch(query: str, category: str = "web") -> str:
    query = query.strip()
    if not query:
        return "error: empty query"

    if category == "images":
        return _websearch_images(query)

    try:
        data = _tavily_search(
            query, include_images=False, max_results=WEBSEARCH_MAX_RESULTS
        )
    except RuntimeError as e:
        return f"error: {e}"

    results = [
        {
            "title": clean_text(item.get("title", "")),
            "url": clean_text(item.get("url", "")),
            "snippet": clean_text(item.get("content", "")),
        }
        for item in data.get("results", [])
        if item.get("title") and item.get("url")
    ][:WEBSEARCH_MAX_RESULTS]

    answer = clean_text(data.get("answer") or "")

    if not results and not answer:
        return f"no results for: {query}"

    lines = [f"query: {query}", ""]
    if answer:
        lines.append(f"answer: {answer}")
        lines.append("")
    for i, item in enumerate(results, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   {item['url']}")
        if item["snippet"]:
            lines.append(f"   {item['snippet']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _websearch_images(query: str) -> str:
    try:
        data = _tavily_search(
            query, include_images=True, max_results=WEBSEARCH_MAX_RESULTS
        )
    except RuntimeError as e:
        return f"error: {e}"

    results = [
        {
            "title": clean_text(item.get("description") or item.get("title") or ""),
            "image_url": clean_text(item.get("url", "")),
        }
        for item in data.get("images", [])
        if item.get("url")
    ][:WEBSEARCH_MAX_IMAGE_RESULTS]

    if not results:
        return f"no image results for: {query}"

    lines = [f"query: {query}", ""]
    for i, item in enumerate(results, 1):
        title = item["title"] or "(no description)"
        lines.append(f"{i}. {title}")
        lines.append(f"   image:     {item['image_url']}")
        lines.append(f"   thumbnail: {item['image_url']}")
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
