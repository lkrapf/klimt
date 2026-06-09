"""CSS theme discovery for Klimt.

Themes are loaded from two locations, in priority order:
  1. ~/.klimt/themes/   (user themes — takes precedence on name collision)
  2. <package>/web/themes/  (bundled defaults shipped with Klimt)

Bundled themes are served directly by pywebview as static files.
User themes cannot be served that way (they live outside the package tree),
so they are delivered via the JS bridge: JS calls get_theme_css(name) on
the Python API, which reads the file and returns the CSS text for injection.
"""
from __future__ import annotations

import re
from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"
BUNDLED_THEMES_DIR = WEB_DIR / "themes"
USER_THEMES_DIR = Path.home() / ".klimt" / "themes"
STATE_PATH = Path.home() / ".klimt" / "theme"
DEFAULT_THEME = "editorial"
_THEME_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def valid_theme_name(name: str) -> bool:
    return bool(_THEME_NAME_RE.fullmatch(str(name or "")))


def theme_path(name: str) -> Path | None:
    """Return the Path for *name*, checking user dir before bundled dir."""
    if not valid_theme_name(name):
        return None
    for base in (USER_THEMES_DIR, BUNDLED_THEMES_DIR):
        path = base / f"{name}.css"
        try:
            path.relative_to(base)  # guard against traversal
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def is_user_theme(name: str) -> bool:
    """True if *name* resolves to a file in the user themes directory."""
    if not valid_theme_name(name):
        return False
    path = USER_THEMES_DIR / f"{name}.css"
    try:
        path.relative_to(USER_THEMES_DIR)
    except ValueError:
        return False
    return path.is_file()


def list_theme_names() -> list[str]:
    """Merged, deduplicated list of all theme names; user themes win on collision."""
    names: dict[str, None] = {}
    for base in (USER_THEMES_DIR, BUNDLED_THEMES_DIR):
        try:
            paths = base.glob("*.css")
        except OSError:
            continue
        for p in paths:
            if valid_theme_name(p.stem) and p.is_file():
                names.setdefault(p.stem, None)
    return sorted(names, key=str.lower)


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
