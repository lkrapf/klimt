"""Application entrypoint: spawns the pywebview window and wires the JS bridge."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import webview

from . import __version__, skills, tools
from .api import ChatSession

WEB_DIR = Path(__file__).parent / "web"
SYSTEM_PROMPT_PATH = Path.home() / ".klimt" / "AGENTS.md"


def _build_system_prompt() -> str:
    base = os.environ.get("KLIMT_SYSTEM")
    if base is None:
        try:
            base = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            base = ""

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


def _new_session() -> ChatSession:
    return ChatSession(
        model=os.environ.get("KLIMT_MODEL", os.environ["AZURE_OPENAI_DEPLOYMENT"]),
        system=_build_system_prompt(),
        context_window=int(os.environ.get("KLIMT_CONTEXT_WINDOW", "128000")),
    )


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

    def _sync_input_history(self) -> None:
        self._emit({"type": "input_history", "items": self._session.input_history})
        self._emit({"type": "session", "name": self._session.session_name})
        self._emit({"type": "context", **self._session.context_usage()})

    def _replay_session(self) -> None:
        self._emit({"type": "clear"})
        tool_names = {}
        tool_args = {}
        for msg in self._session.history:
            role = msg.get("role")
            if role == "user":
                content = msg.get("content") or ""
                replay_role = "system" if content.startswith("[Klimt compacted prior context") else "user"
                self._emit({"type": "message", "role": replay_role, "content": content})
            elif role == "assistant":
                content = msg.get("content")
                if content:
                    self._emit({"type": "message", "role": "assistant", "content": content})
                for tc in msg.get("tool_calls") or []:
                    tid = tc.get("id") or ""
                    fn = tc.get("function") or {}
                    tool_names[tid] = fn.get("name") or "tool"
                    try:
                        tool_args[tid] = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        tool_args[tid] = {"_raw": fn.get("arguments") or ""}
            elif role == "tool":
                tid = msg.get("tool_call_id") or ""
                self._emit({
                    "type": "tool",
                    "name": tool_names.get(tid, "tool"),
                    "args": tool_args.get(tid, {}),
                    "result": msg.get("content") or "",
                })

    def send(self, text: str) -> dict:
        try:
            command = text.strip()
            if command == "/resume" or command.startswith("/resume "):
                self._resume(command[7:].strip())
                return {"ok": True}

            renamed = self._session.maybe_title_from_first_input(command)
            self._session.remember_input(command)
            self._session.persist()
            if renamed:
                self._emit({"type": "text", "content": f"session named **{self._session.session_name}**"})
            self._sync_input_history()
            if command == "/compact" or command.startswith("/compact "):
                self._compact(command[8:].strip())
                return {"ok": True}
            if command == "/help":
                self._help()
                return {"ok": True}
            if command == "/skills":
                self._skills()
                return {"ok": True}
            if command == "/reload":
                self._reload()
                return {"ok": True}
            if command == "/quit":
                self._quit()
                return {"ok": True}
            if command == "/name" or command.startswith("/name "):
                self._name(command[5:].strip())
                return {"ok": True}
            self._session.stream(text, self._emit)
            self._sync_input_history()
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
        self._sync_input_history()
        return {"ok": True}

    def info(self) -> dict:
        available_tools = [
            {
                "name": schema.get("function", {}).get("name", ""),
                "description": schema.get("function", {}).get("description", ""),
            }
            for schema in tools.SCHEMAS
        ]
        return {
            "version": __version__,
            "model": self._session.model,
            "session": self._session.session_name,
            "input_history": self._session.input_history,
            "context": self._session.context_usage(),
            "skills": skills.list_skills(),
            # Prefer the explicit name. Keep `tools` for compatibility with old JS.
            "available_tools": available_tools,
            "tools": available_tools,
        }

    def _help(self) -> None:
        lines = [
            "## Commands",
            "",
            "- `!cmd` — run a shell command directly and show the result as a tool box.",
            "- `/help` — show this help.",
            "- `/skills` — list available skills with short descriptions.",
            "- `/compact [N]` — compact older context, keeping the last N history messages raw (default 8).",
            "- `/reload` — reload `~/.klimt/AGENTS.md`, skills, tools, Azure client config, and CSS.",
            "- `/quit` — close Klimt.",
            "- `/resume [name]` — resume a saved session for this folder; without a name resumes the most recent.",
            "- `/name <name>` — name the current session.",
            "- `/<skill>` — load `~/.klimt/skills/<skill>/SKILL.md` into the conversation.",
            "",
            "## Keys",
            "",
            "- `Enter` — send",
            "- `Shift+Enter` — newline",
        ]

        items = skills.list_skills()
        if items:
            lines.extend(["", "## Available skills", ""])
            for s in items:
                desc = s["description"] or "(no description)"
                lines.append(f"- `/{s['name']}` — {desc}")

        self._emit({"type": "text", "content": "\n".join(lines)})

    def _skills(self) -> None:
        items = skills.list_skills()
        if not items:
            self._emit({"type": "text", "content": "_no skills found under `~/.klimt/skills`_"})
            return

        lines = ["## Available skills", ""]
        for s in items:
            desc = s["description"] or "(no description)"
            lines.append(f"- `/{s['name']}` — {desc}")
        self._emit({"type": "text", "content": "\n".join(lines)})

    def _compact(self, arg: str) -> None:
        keep_recent = 8
        if arg:
            try:
                keep_recent = int(arg)
            except ValueError:
                self._emit({"type": "text", "content": "_usage: `/compact [recent-message-count]`_"})
                return
        self._emit({"type": "text", "content": f"compacting context, keeping last {keep_recent} messages raw..."})
        result = self._session.compact(keep_recent)
        self._replay_session()
        self._sync_input_history()
        self._emit({"type": "text", "content": result})

    def _resume(self, name: str) -> None:
        if not name:
            sessions = self._session.list_sessions()
            if not sessions:
                self._emit({"type": "text", "content": "_no saved sessions for this folder_"})
                return
            name = sessions[0]["name"]

        if not self._session.load_session(name):
            self._emit({"type": "text", "content": f"_unknown session: `{name}`_"})
            return

        self._replay_session()
        self._sync_input_history()
        self._emit({"type": "text", "content": f"resumed session **{self._session.session_name}**"})

    def _name(self, name: str) -> None:
        if not name:
            current = self._session.session_name
            self._emit({"type": "text", "content": f"current session: **{current}**\n\n_usage: `/name <session-name>`_"})
            return
        self._session.rename_session(name)
        self._sync_input_history()
        self._emit({"type": "text", "content": f"session named **{self._session.session_name}**"})

    def _reload(self) -> None:
        """Reload local config, skill/tool modules, model config, and CSS."""
        importlib.reload(skills)
        importlib.reload(tools)
        self._session.system = _build_system_prompt()
        self._session.model = os.environ.get(
            "KLIMT_MODEL",
            os.environ["AZURE_OPENAI_DEPLOYMENT"],
        )
        self._session.context_window = int(os.environ.get("KLIMT_CONTEXT_WINDOW", "128000"))
        self._session.reload_client()
        self._emit({"type": "reload_css"})
        self._emit({"type": "text", "content": "reloaded config, skills, tools, and CSS"})

    def _quit(self) -> None:
        """Exit the process immediately."""
        os._exit(0)


def main() -> None:
    api = Api(_new_session())

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
