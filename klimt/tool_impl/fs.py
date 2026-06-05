"""File-system tools: read, edit, write, glob, grep."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from threading import Event

from .limits import (
    GLOB_MAX_RESULTS,
    GREP_MAX_BYTES,
    GREP_MAX_LINES,
    GREP_TIMEOUT,
    READ_MAX_BYTES,
    READ_MAX_LINES,
)
from .paths import resolve_path
from .shell import kill_process_tree


def read(path: str, offset: int = 1, limit: int | None = None, cwd: str | None = None) -> str:
    p = resolve_path(path, cwd)
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        return f"error: binary file not shown: {p}"

    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    total = len(lines)

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


def edit(path: str, edits: list[dict[str, str]], cwd: str | None = None) -> str:
    if not edits:
        return "error: edits must not be empty"

    p = resolve_path(path, cwd)
    original = p.read_bytes()
    matches: list[tuple[int, int, bytes, bytes]] = []

    for i, e in enumerate(edits, start=1):
        old_text = e.get("oldText")
        new_text = e.get("newText")
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


def write(path: str, content: str, cwd: str | None = None) -> str:
    p = resolve_path(path, cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"


def glob_(pattern: str, search_path: str | None, cwd: str | None) -> str:
    pattern = (pattern or "").strip()
    if not pattern:
        return "error: empty pattern"
    root = resolve_path(search_path or ".", cwd)
    if not root.exists():
        return f"error: path does not exist: {root}"
    if not root.is_dir():
        return f"error: not a directory: {root}"

    matches: list[Path] = []
    try:
        # Path.glob honours `**` for recursive matches and treats it as the
        # explicit "any number of directories" pattern, which is what callers
        # expect.
        for p in root.glob(pattern):
            matches.append(p)
            if len(matches) > GLOB_MAX_RESULTS * 4:
                # Guard against pathological patterns; we still sort below.
                break
    except (OSError, ValueError) as e:
        return f"error: {type(e).__name__}: {e}"

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    matches.sort(key=_mtime, reverse=True)
    truncated = len(matches) > GLOB_MAX_RESULTS
    matches = matches[:GLOB_MAX_RESULTS]

    if not matches:
        return f"no matches for {pattern!r} under {root}"

    lines = [
        f"root: {root}",
        f"pattern: {pattern}",
        f"matches: {len(matches)}{' (truncated)' if truncated else ''}",
        "",
    ]
    for p in matches:
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = p
        suffix = "/" if p.is_dir() else ""
        lines.append(f"{rel}{suffix}")
    if truncated:
        lines.append(f"[truncated to {GLOB_MAX_RESULTS} most recent matches]")
    return "\n".join(lines)


def grep(
    pattern: str,
    *,
    path: str | None,
    glob_filter: str | None,
    case_insensitive: bool,
    cancel: Event | None,
    cwd: str | None,
) -> str:
    pattern = pattern or ""
    if not pattern:
        return "error: empty pattern"
    ag = shutil.which("ag")
    if not ag:
        return (
            "error: `ag` (the_silver_searcher) is not installed; install it to use grep "
            "(macOS: `brew install the_silver_searcher`; Debian/Ubuntu: `apt install silversearcher-ag`)"
        )

    target = resolve_path(path or ".", cwd)
    if not target.exists():
        return f"error: path does not exist: {target}"

    args = [ag, "--nocolor", "--numbers", "--noheading", "--silent"]
    if case_insensitive:
        args.append("-i")
    if glob_filter:
        args.extend(["-G", glob_filter])
    args.append("--")
    args.append(pattern)
    args.append(str(target))

    try:
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        return "error: `ag` is not installed"

    deadline = time.monotonic() + GREP_TIMEOUT
    while p.poll() is None:
        if cancel and cancel.is_set():
            kill_process_tree(p)
            return "error: interrupted"
        if time.monotonic() >= deadline:
            kill_process_tree(p)
            return f"error: grep timed out after {GREP_TIMEOUT}s"
        time.sleep(0.05)

    stdout, stderr = p.communicate()
    # ag exits 1 when no matches are found; not an error.
    if p.returncode not in (0, 1):
        err = stderr.strip() or f"ag exited with status {p.returncode}"
        return f"error: {err}"

    if not stdout:
        return f"no matches for {pattern!r} under {target}"

    lines = stdout.splitlines()
    truncated_lines = len(lines) > GREP_MAX_LINES
    lines = lines[:GREP_MAX_LINES]
    body = "\n".join(lines)
    truncated_bytes = False
    if len(body.encode("utf-8")) > GREP_MAX_BYTES:
        body = body.encode("utf-8")[:GREP_MAX_BYTES].decode("utf-8", errors="ignore")
        truncated_bytes = True

    header = [f"pattern: {pattern}", f"path: {target}"]
    if glob_filter:
        header.append(f"glob: {glob_filter}")
    header.append(f"matches: {len(lines)}")
    notes = []
    if truncated_lines:
        notes.append(f"line limit {GREP_MAX_LINES}")
    if truncated_bytes:
        notes.append(f"byte limit {GREP_MAX_BYTES}")
    if notes:
        header.append(f"[truncated: {' / '.join(notes)}]")
    return "\n".join(header) + "\n\n" + body
