"""Application entrypoint: spawns the pywebview window and wires the JS bridge."""
from __future__ import annotations

import json
import os
from pathlib import Path

import webview

from . import skills
from .api import ChatSession

WEB_DIR = Path(__file__).parent / "web"
SYSTEM_PROMPT_PATH = Path.home() / ".klimt" / "AGENTS.md"


def _build_system_prompt() -> str:
    base = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    items = skills.list_skills()
    if not items:
        return base
    lines = [
        "",
        "## Available skills",
        "",
        "These skills are available on demand. The user invokes one with",
        "`/<name>`, which loads its full SKILL.md into the conversation.",
        "If a user's task matches a skill, suggest the invocation.",
        "",
    ]
    for s in items:
        desc = s["description"] or "(no description)"
        lines.append(f"- `/{s['name']}` — {desc}")
    return base + "\n".join(lines) + "\n"


class Api:
    """Exposed to JS via window.pywebview.api.*"""

    def __init__(self, session: ChatSession) -> None:
        self._session = session
        self._window = None  # set by attach_window

    def attach_window(self, window) -> None:
        self._window = window

    def _emit(self, event: dict) -> None:
        payload = json.dumps(event)
        self._window.evaluate_js(f"window.klimt.handleEvent({payload})")

    def send(self, text: str) -> dict:
        try:
            self._session.stream(text, self._emit)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            try:
                self._emit({"type": "error", "message": msg})
            except Exception:
                pass
            return {"ok": False, "error": msg}

    def reset(self) -> dict:
        self._session.reset()
        return {"ok": True}

    def info(self) -> dict:
        return {"model": self._session.model}


def main() -> None:
    session = ChatSession(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        system=_build_system_prompt(),
    )
    api = Api(session)

    window = webview.create_window(
        title="Klimt",
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=900,
        height=720,
        min_size=(500, 400),
        text_select=True,
    )
    api.attach_window(window)
    webview.start()
