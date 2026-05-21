"""Tool implementations exposed to the model.

Tools: read, edit, write, bash, webfetch, websearch. Each returns a string. Errors are returned
as strings (not raised) so the model can see them and recover.
"""
from __future__ import annotations

import html.parser
import os
import signal
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Event
from typing import Any, Dict

BASH_TIMEOUT = 120  # seconds
WEBFETCH_TIMEOUT = 30  # seconds
WEBFETCH_MAX_BYTES = 2_000_000
WEBSEARCH_MAX_RESULTS = 5
WEBFETCH_MAX_TEXT_CHARS = 200_000
WEBFETCH_MAX_LINKS = 40
READ_MAX_LINES = 2000
READ_MAX_BYTES = 50_000

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a UTF-8 text file from disk, with optional 1-indexed line offset and line limit. Output is capped and includes line numbers plus a continuation hint when truncated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "offset": {"type": "integer", "description": "Optional 1-indexed start line. Defaults to 1.", "minimum": 1},
                    "limit": {"type": "integer", "description": f"Optional maximum number of lines to return. Capped at {READ_MAX_LINES}.", "minimum": 1},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Edit a single file using exact text replacement. Every edits[].oldText must match a unique, non-overlapping region of the original file. If two changes affect the same block or nearby lines, merge them into one edit instead of emitting overlapping edits. Do not include large unchanged regions just to connect distant changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the existing file to edit."},
                    "edits": {
                        "type": "array",
                        "description": "Exact replacements to apply. Each entry is matched against the original file, not incrementally after earlier entries.",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {"type": "string", "description": "Exact text for one targeted replacement. It must be unique in the original file and must not overlap with any other edits[].oldText in the same call."},
                                "newText": {"type": "string", "description": "Replacement text for this edit."},
                            },
                            "required": ["oldText", "newText"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write text to a file, overwriting any existing content. Parent directories are created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": f"Execute a bash command. Returns stdout, stderr, and exit code. Timeout {BASH_TIMEOUT}s.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "webfetch",
            "description": "Read a URL over HTTP(S). Returns response metadata and text body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "websearch",
            "description": "Search the web with DuckDuckGo and return compact result titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                },
                "required": ["query"],
            },
        },
    },
]


def _read(path: str, offset: int = 1, limit: int | None = None) -> str:
    p = Path(path).expanduser()
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        return f"error: binary file not shown: {p}"

    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if text and not text.endswith(("\n", "\r")):
        # splitlines(keepends=True) already includes the unterminated final line.
        pass

    start = max(1, int(offset or 1))
    requested = READ_MAX_LINES if limit is None else max(1, int(limit))
    requested = min(requested, READ_MAX_LINES)

    start_index = min(start - 1, total)
    selected: list[tuple[int, str]] = []
    used = 0
    truncated_by_bytes = False
    for line_no, line in enumerate(lines[start_index:], start=start):
        if len(selected) >= requested:
            break
        encoded_len = len(line.encode("utf-8"))
        if selected and used + encoded_len > READ_MAX_BYTES:
            truncated_by_bytes = True
            break
        selected.append((line_no, line))
        used += encoded_len

    end = selected[-1][0] if selected else start - 1
    body = "".join(f"{line_no:6d}\t{line}" for line_no, line in selected)
    if body and not body.endswith("\n"):
        body += "\n"

    out = [f"{p} lines {start}-{end} of {total}", body.rstrip("\n")]
    more = end < total
    if more:
        reasons = []
        if len(selected) >= requested:
            reasons.append(f"line limit {requested}")
        if truncated_by_bytes:
            reasons.append(f"byte limit {READ_MAX_BYTES}")
        reason = " / ".join(reasons) or "truncated"
        out.append(f"[truncated: {reason}; use offset={end + 1} to continue]")
    return "\n".join(part for part in out if part)


def _edit(path: str, edits: list[dict[str, str]]) -> str:
    if not edits:
        return "error: edits must not be empty"

    p = Path(path).expanduser()
    original = p.read_bytes()
    matches: list[tuple[int, int, bytes, bytes]] = []

    for i, edit in enumerate(edits, start=1):
        old_text = edit.get("oldText")
        new_text = edit.get("newText")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            return f"error: edit {i}: oldText and newText must be strings"
        if old_text == "":
            return f"error: edit {i}: oldText must not be empty"

        old = old_text.encode("utf-8")
        new = new_text.encode("utf-8")
        count = original.count(old)
        if count == 0:
            return f"error: edit {i}: oldText not found"
        if count > 1:
            return f"error: edit {i}: oldText is not unique ({count} matches)"
        start = original.index(old)
        matches.append((start, start + len(old), old, new))

    ordered = sorted(matches, key=lambda item: item[0])
    for (start, end, _, _), (next_start, _, _, _) in zip(ordered, ordered[1:]):
        if end > next_start:
            return "error: edits overlap; merge nearby changes into one edit"

    updated = original
    for start, end, _old, new in sorted(matches, key=lambda item: item[0], reverse=True):
        updated = updated[:start] + new + updated[end:]

    p.write_bytes(updated)
    delta = len(updated) - len(original)
    return f"edited {p}: {len(edits)} replacement(s), {len(original)} -> {len(updated)} bytes ({delta:+d})"


def _write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"



class _HTMLTextParser(html.parser.HTMLParser):
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
            text = _clean_text("".join(self._link_text))
            href = self._link_href
            if text and href.startswith(("http://", "https://")):
                self.links.append((text, href))
            self._link_href = None
            self._link_text = []
        if tag in self._block_tags:
            self.parts.append("\n")


class _DuckDuckGoHTMLParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._field: str | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class") or ""
        classes = set(cls.split())

        if tag == "div" and "result" in classes and self._current is None:
            self._current = {"title": "", "url": "", "snippet": ""}
            self._depth = 1
            return

        if self._current is not None:
            if tag == "div":
                self._depth += 1
            if tag == "a" and "result__a" in classes:
                self._current["url"] = _unwrap_ddg_url(attrs_dict.get("href") or "")
                self._field = "title"
            elif "result__snippet" in classes:
                self._field = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._field:
            self._current[self._field] += data

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag == "a" and self._field == "title":
            self._field = None
        elif tag in {"a", "div"} and self._field == "snippet":
            self._field = None
        if tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                if self._current.get("title") and self._current.get("url"):
                    self.results.append(self._current)
                self._current = None
                self._field = None



def _unwrap_ddg_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/"):
        href = "https://html.duckduckgo.com" + href
    parsed = urllib.parse.urlparse(href)
    qs = urllib.parse.parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return qs["uddg"][0]
    return href


def _clean_text(s: str) -> str:
    return " ".join(s.split())


def _clean_visible_text(s: str) -> str:
    lines = [_clean_text(line) for line in s.splitlines()]
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


def _is_html_content(content_type: str, url: str) -> bool:
    ctype = content_type.lower()
    if "text/html" in ctype or "application/xhtml+xml" in ctype:
        return True
    return not ctype and url.lower().split("?", 1)[0].endswith((".html", ".htm"))


def _html_to_text(body: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    parser = _HTMLTextParser(base_url)
    parser.feed(body)
    text = _clean_visible_text("".join(parser.parts))

    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for title, href in parser.links:
        key = href
        if key in seen:
            continue
        seen.add(key)
        links.append((title, href))
        if len(links) >= WEBFETCH_MAX_LINKS:
            break
    return text, links


def _websearch(query: str) -> str:
    query = query.strip()
    if not query:
        return "error: empty query"

    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Klimt/0 websearch",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=WEBFETCH_TIMEOUT) as r:  # noqa: S310
        raw = r.read(WEBFETCH_MAX_BYTES)
        charset = r.headers.get_content_charset() or "utf-8"

    parser = _DuckDuckGoHTMLParser()
    parser.feed(raw.decode(charset, errors="replace"))
    results = [
        {
            "title": _clean_text(item.get("title", "")),
            "url": _clean_text(item.get("url", "")),
            "snippet": _clean_text(item.get("snippet", "")),
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


def _webfetch(url: str) -> str:
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

        if _is_html_content(content_type, final_url):
            text, links = _html_to_text(body, final_url)
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


def _format_result(returncode: int | str, stdout: str, stderr: str) -> str:
    return (
        f"exit={returncode}\n"
        f"--- stdout ---\n{stdout}"
        f"--- stderr ---\n{stderr}"
    )


def _kill_process_tree(p: subprocess.Popen[str]) -> None:
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        p.terminate()

    try:
        p.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        p.kill()


def _bash(command: str, cancel: Event | None = None) -> str:
    p = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + BASH_TIMEOUT

    while p.poll() is None:
        if cancel and cancel.is_set():
            _kill_process_tree(p)
            stdout, stderr = p.communicate()
            return _format_result("interrupted", stdout, stderr)
        if time.monotonic() >= deadline:
            _kill_process_tree(p)
            stdout, stderr = p.communicate()
            return _format_result("timeout", stdout, stderr)
        time.sleep(0.1)

    stdout, stderr = p.communicate()
    return _format_result(p.returncode, stdout, stderr)


def run(name: str, args: Dict[str, Any], cancel: Event | None = None) -> str:
    try:
        if name == "read":
            return _read(args["path"], args.get("offset", 1), args.get("limit"))
        if name == "edit":
            return _edit(args["path"], args["edits"])
        if name == "write":
            return _write(args["path"], args["content"])
        if name == "bash":
            return _bash(args["command"], cancel)
        if name == "webfetch":
            return _webfetch(args["url"])
        if name == "websearch":
            return _websearch(args["query"])
        return f"error: unknown tool {name!r}"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"
