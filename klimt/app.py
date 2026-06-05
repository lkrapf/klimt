"""Application entrypoint: spawns the pywebview window and wires the JS bridge."""
from __future__ import annotations

import os
from pathlib import Path

import webview

from . import themes
from .session_factory import new_session
from .tab_api import Api

WEB_DIR = Path(__file__).parent / "web"
ASSETS_DIR = Path(__file__).parent / "assets"
ICON_PATH = ASSETS_DIR / "klimt-icon.png"


def _set_macos_icon() -> None:
    """Set the runtime Dock/app-switcher icon when launched with `python -m`.

    A real `.app` bundle still needs `CFBundleIconFile`/`.icns` in its
    Info.plist. For an unbundled Python process, macOS lets us override the
    icon for the running NSApplication, which is good enough for `python -m`.
    """
    if os.uname().sysname != "Darwin" or not ICON_PATH.exists():
        return

    try:
        from AppKit import NSApplication, NSImage
    except Exception:
        return

    image = NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH))
    if image:
        NSApplication.sharedApplication().setApplicationIconImage_(image)


def main() -> None:
    _set_macos_icon()
    api = Api(new_session())

    window = webview.create_window(
        title="Klimt",
        url=f"{WEB_DIR / 'index.html'}?theme={themes.load_theme()}",
        js_api=api,
        width=900,
        height=720,
        min_size=(500, 400),
        text_select=True,
    )
    api.attach_window(window)
    webview.start()
