"""CSS theme discovery for Klimt."""
from __future__ import annotations

import re
from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"
THEMES_DIR = WEB_DIR / "themes"
STATE_PATH = Path.home() / ".klimt" / "theme"
DEFAULT_THEME = "editorial"
_THEME_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def valid_theme_name(name: str) -> bool:
    return bool(_THEME_NAME_RE.fullmatch(str(name or "")))


def theme_path(name: str) -> Path | None:
    if not valid_theme_name(name):
        return None
    path = THEMES_DIR / f"{name}.css"
    try:
        path.relative_to(THEMES_DIR)
    except ValueError:
        return None
    return path if path.is_file() else None


def list_theme_names() -> list[str]:
    try:
        paths = THEMES_DIR.glob("*.css")
    except OSError:
        return []
    names = [p.stem for p in paths if valid_theme_name(p.stem) and p.is_file()]
    return sorted(dict.fromkeys(names), key=str.lower)


def default_theme() -> str:
    names = list_theme_names()
    if DEFAULT_THEME in names:
        return DEFAULT_THEME
    return names[0] if names else DEFAULT_THEME


def load_theme() -> str:
    try:
        name = STATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return default_theme()
    return name if theme_path(name) else default_theme()


def save_theme(name: str) -> None:
    if not theme_path(name):
        raise ValueError(f"unknown theme: {name}")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(name + "\n", encoding="utf-8")
