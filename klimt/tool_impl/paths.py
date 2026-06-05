"""Shared path resolution used by file-based tools."""
from __future__ import annotations

import os
from pathlib import Path


def resolve_path(path: str, cwd: str | None = None) -> Path:
    """Expand and anchor a tool-supplied path against the session cwd."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    base = Path(cwd or os.getcwd()).expanduser()
    return base / p
