"""Tool implementations exposed to the model.

Tools: read, write, bash, webfetch, websearch. Each returns a string. Errors are returned
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

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the contents of a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                },
                "required": ["path"],
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


def _read(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8")


def _write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"


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
        headers = "".join(f"{k}: {v}\n" for k, v in r.headers.items())
        note = f"\n[truncated to {WEBFETCH_MAX_BYTES} bytes]" if truncated else ""
        return (
            f"url: {r.geturl()}\n"
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
            return _read(args["path"])
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
