"""`visual` tool: load a local image for a multimodal model.

The tool returns a JSON envelope as its result string. The provider adapters
in `klimt.providers` detect that envelope at message-assembly time and splice
the image into the outgoing payload (an `image` block on Anthropic, or a
follow-up `user` message with `image_url` content parts on OpenAI chat-
completions).

History stays a flat list of string-content messages, so persistence and
compaction keep working. The base64 payload lives only inside that envelope.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .limits import VISUAL_MAX_BYTES
from .paths import resolve_path

# Marker key on the result-string JSON object. Providers and the frontend use
# this to recognise an image envelope without having to know the schema.
ENVELOPE_KEY = "_klimt_image"

# (extension, magic-byte signature) -> MIME type. We sniff content rather than
# trust the extension so an oddly-named screenshot still works.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # plus "WEBP" at offset 8; checked below
)


def _sniff_media_type(raw: bytes) -> str | None:
    for sig, mime in _SIGNATURES:
        if raw.startswith(sig):
            if mime == "image/webp" and not (len(raw) >= 12 and raw[8:12] == b"WEBP"):
                continue
            return mime
    return None


def visual(path: str, note: str | None = None, cwd: str | None = None) -> str:
    p = resolve_path(path, cwd)
    if not p.exists():
        return f"error: path does not exist: {p}"
    if not p.is_file():
        return f"error: not a file: {p}"

    raw = p.read_bytes()
    size = len(raw)
    if size == 0:
        return f"error: empty file: {p}"
    if size > VISUAL_MAX_BYTES:
        return (
            f"error: image too large: {size} bytes "
            f"(cap {VISUAL_MAX_BYTES}); resize or crop first"
        )

    media_type = _sniff_media_type(raw)
    if not media_type:
        return f"error: unsupported image format (expected PNG, JPEG, GIF, or WebP): {p}"

    envelope: dict[str, Any] = {
        ENVELOPE_KEY: True,
        "media_type": media_type,
        "data": base64.b64encode(raw).decode("ascii"),
        "path": str(p),
        "bytes": size,
    }
    if note:
        envelope["note"] = str(note)
    return json.dumps(envelope, ensure_ascii=False)


def parse_envelope(content: Any) -> dict[str, Any] | None:
    """Return the parsed envelope dict, or None if `content` isn't one.

    Accepts a JSON string (the on-history form) or an already-parsed dict.
    """
    if isinstance(content, dict):
        return content if content.get(ENVELOPE_KEY) is True else None
    if not isinstance(content, str):
        return None
    stripped = content.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or parsed.get(ENVELOPE_KEY) is not True:
        return None
    return parsed


def envelope_summary(env: dict[str, Any]) -> str:
    """Short human-readable string used as a placeholder in down-converted text."""
    path = env.get("path") or "(image)"
    size = env.get("bytes")
    mime = env.get("media_type") or "image"
    parts = [f"image attached: {path}", mime]
    if isinstance(size, int):
        parts.append(f"{size} bytes")
    note = env.get("note")
    if note:
        parts.append(f"note: {note}")
    return "[" + "; ".join(parts) + "]"
