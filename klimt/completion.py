"""Composer tab completion for commands, models, sessions, and paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import commands, skills
from .model_config import list_model_names

MAX_ITEMS = 100


@dataclass(frozen=True)
class TextRange:
    start: int
    end: int


def complete(session: Any, text: str, cursor: int | None = None) -> dict[str, Any]:
    text = text or ""
    cursor = max(0, min(len(text), len(text) if cursor is None else int(cursor)))

    command_start = _command_start(text)
    if command_start is not None and cursor >= command_start:
        after = text[command_start:]
        local_cursor = cursor - command_start
        if after.startswith("/"):
            result = _slash_completion(session, after, local_cursor)
            if result and result.get("items"):
                return _offset_result(result, command_start)
        if after.startswith("!"):
            result = _path_completion(session.cwd, after, local_cursor, offset=1, dirs_only=False)
            if result:
                return _offset_result(result, command_start)

    return _path_completion(session.cwd, text, cursor, offset=0, dirs_only=False) or _empty(cursor)


def _command_start(text: str) -> int | None:
    stripped = text.lstrip()
    if not stripped:
        return 0
    start = len(text) - len(stripped)
    if stripped.startswith(("/", "!")):
        return start
    return None


def _slash_completion(session: Any, text: str, cursor: int) -> dict[str, Any] | None:
    head = _first_token(text)
    head_range = _token_range(text, cursor)
    if head_range and head_range.start == 0 and cursor <= head_range.end:
        names = [spec.name for spec in commands.SPECS if spec.name not in {"!", "/<skill>"}]
        names.extend("/" + s.get("name", "") for s in skills.list_skills() if s.get("name"))
        return _choice_result(head_range, text[head_range.start:cursor], names, "command")

    if head == "/cd":
        arg = _argument_range(text, len(head), cursor)
        return _path_completion(session.cwd, text, cursor, offset=arg.start, dirs_only=True)

    if head == "/model":
        arg = _argument_range(text, len(head), cursor)
        return _choice_result(arg, text[arg.start:cursor], list_model_names(), "model")

    if head == "/session":
        arg = _argument_range(text, len(head), cursor)
        names = [str(s.get("name") or "") for s in session.list_sessions()]
        return _choice_result(arg, text[arg.start:cursor], names, "session")

    if head == "/sessions":
        parts = _words_before_cursor(text, cursor)
        if len(parts) <= 2:
            arg = _argument_range(text, len(head), cursor)
            return _choice_result(arg, text[arg.start:cursor], ["resume", "delete", "clear"], "subcommand")
        if len(parts) >= 3 and parts[1] in {"resume", "delete"}:
            arg_start = _nth_argument_start(text, 3)
            arg = TextRange(arg_start, len(text))
            names = [str(s.get("name") or "") for s in session.list_sessions()]
            return _choice_result(arg, text[arg.start:cursor], names, "session")

    return None


def _first_token(text: str) -> str:
    return text.split(None, 1)[0] if text.split(None, 1) else ""


def _words_before_cursor(text: str, cursor: int) -> list[str]:
    return text[:cursor].split()


def _argument_range(text: str, command_end: int, cursor: int) -> TextRange:
    start = command_end
    while start < len(text) and text[start].isspace():
        start += 1
    if cursor < start:
        cursor = start
    return TextRange(start, len(text))


def _nth_argument_start(text: str, n: int) -> int:
    """Return the start offset of the nth whitespace-separated token, 1-indexed."""
    in_word = False
    count = 0
    for i, ch in enumerate(text):
        if ch.isspace():
            in_word = False
        elif not in_word:
            in_word = True
            count += 1
            if count == n:
                return i
    return len(text)


def _token_range(text: str, cursor: int) -> TextRange | None:
    if not text:
        return TextRange(0, 0)
    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1
    return TextRange(start, end)


@dataclass(frozen=True)
class PathToken:
    range: TextRange
    prefix: str
    quote: str


def _path_completion(cwd: str, text: str, cursor: int, offset: int, dirs_only: bool) -> dict[str, Any] | None:
    token = _path_token(text, cursor, offset)
    if not token:
        return None

    raw_prefix = _unescape_token(token.prefix, token.quote)
    candidates = _path_candidates(cwd, raw_prefix, token.quote, dirs_only)
    return _items_result(token.range, candidates, "path")


def _path_token(text: str, cursor: int, offset: int = 0) -> PathToken | None:
    cursor = max(offset, min(len(text), cursor))
    start = offset
    quote = ""
    escaped = False
    i = offset
    while i < cursor:
        ch = text[i]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif quote:
            if ch == quote:
                quote = ""
                start = i + 1
        elif ch in {"'", '"'}:
            quote = ch
            start = i
        elif ch.isspace():
            start = i + 1
        i += 1

    end = cursor
    escaped = False
    q = quote
    while end < len(text):
        ch = text[end]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif q:
            if ch == q:
                break
        elif ch.isspace():
            break
        end += 1

    if start < len(text) and text[start] in {"'", '"'}:
        quote = text[start]
        replace_start = start + 1
        replace_end = end
    else:
        quote = ""
        replace_start = start
        replace_end = end

    prefix = text[replace_start:cursor]
    return PathToken(TextRange(replace_start, replace_end), prefix, quote)


def _path_candidates(cwd: str, raw_prefix: str, quote: str, dirs_only: bool) -> list[str]:
    base = Path(cwd).expanduser()

    if raw_prefix.endswith("/"):
        parent_text = raw_prefix
        name_prefix = ""
    else:
        parent_text, name_prefix = os.path.split(raw_prefix)

    if parent_text:
        lookup = Path(os.path.expanduser(parent_text))
        if not lookup.is_absolute():
            lookup = base / lookup
        display_parent = parent_text
    else:
        lookup = base
        display_parent = ""

    try:
        entries = list(lookup.iterdir())
    except OSError:
        return []

    out: list[str] = []
    for entry in sorted(entries, key=lambda p: (not p.is_dir(), p.name.lower())):
        if not entry.name.startswith(name_prefix):
            continue
        if dirs_only and not entry.is_dir():
            continue
        value = os.path.join(display_parent, entry.name) if display_parent else entry.name
        if entry.is_dir():
            value += "/"
        out.append(_escape_path(value, quote))
        if len(out) >= MAX_ITEMS:
            break
    return out


def _unescape_token(value: str, quote: str) -> str:
    if quote == "'":
        return value
    out: list[str] = []
    escaped = False
    for ch in value:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


def _escape_path(value: str, quote: str) -> str:
    if quote:
        return value
    out: list[str] = []
    for ch in value:
        if ch.isspace() or ch in "\\'\"`$&;()[]{}<>|*?!#":
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _choice_result(token_range: TextRange, prefix: str, choices: list[str], kind: str) -> dict[str, Any]:
    items = [c for c in choices if c.startswith(prefix)]
    return _items_result(token_range, sorted(dict.fromkeys(items)), kind)


def _items_result(token_range: TextRange, items: list[str], kind: str) -> dict[str, Any]:
    return {
        "range": {"start": token_range.start, "end": token_range.end},
        "items": [{"value": item, "label": item, "kind": kind} for item in items[:MAX_ITEMS]],
    }


def _offset_result(result: dict[str, Any], offset: int) -> dict[str, Any]:
    r = result.get("range") or {}
    return {
        **result,
        "range": {"start": int(r.get("start") or 0) + offset, "end": int(r.get("end") or 0) + offset},
    }


def _empty(cursor: int) -> dict[str, Any]:
    return {"range": {"start": cursor, "end": cursor}, "items": []}
