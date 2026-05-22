"""Application entrypoint: spawns the pywebview window and wires the JS bridge."""
from __future__ import annotations

import copy
import importlib
import json
import os
import contextlib
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import webview

from . import __version__, commands, prompt, skills, tools
from .api import ChatSession
from .model_config import default_model_name, list_model_configs, list_model_names

WEB_DIR = Path(__file__).parent / "web"
ASSETS_DIR = Path(__file__).parent / "assets"
ICON_PATH = ASSETS_DIR / "klimt-icon.png"

def _build_system_prompt() -> str:
    return prompt.build_system_prompt(tools.SCHEMAS, skills.list_skills())


def _new_session() -> ChatSession:
    return ChatSession(
        model=default_model_name(),
        system=_build_system_prompt(),
        context_window=int(os.environ.get("KLIMT_CONTEXT_WINDOW", "128000")),
    )


class _SingleTabApi:
    """One independent chat tab. Exposed through the multi-tab Api below."""

    def __init__(self, session: ChatSession, tab_id: str, emit) -> None:
        self._session = session
        self._tab_id = tab_id
        self._emit_to_window = emit
        self._session_choices: list[dict[str, Any]] = []
        self._busy = False
        self._busy_lock = threading.Lock()
        self._generation = 0
        self._active_base: int | None = None

    def _emit(self, event: dict) -> None:
        self._emit_to_window(self._tab_id, event)

    def _emit_current(self, generation: int, event: dict) -> None:
        with self._busy_lock:
            if generation != self._generation:
                return
        self._emit(event)

    def _sync_input_history(self) -> None:
        self._emit({"type": "input_history", "items": self._session.input_history})
        self._emit({"type": "session", "name": self._session.session_name, "model": self._session.model})
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
                reasoning = msg.get("reasoning")
                if reasoning:
                    self._emit({"type": "reasoning", "content": reasoning})
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

    def _done(self) -> None:
        self._emit({"type": "done"})

    def send(self, text: str) -> dict:
        background = False
        try:
            command = text.strip()
            spec = commands.classify(command)
            if spec:
                if spec.busy == "block" and self._is_busy():
                    self._emit({"type": "error", "message": "session is still busy; press Esc to interrupt"})
                    return {"ok": False, "error": "session busy"}
                handled = self._handle_command(command, spec)
                if handled:
                    return {"ok": True}

            renamed = self._session.maybe_title_from_first_input(command)
            self._session.remember_input(command)
            self._session.persist()
            if renamed:
                self._emit({"type": "text", "content": f"session named **{self._session.session_name}**"})
            self._sync_input_history()
            with self._busy_lock:
                if self._busy:
                    self._emit({"type": "error", "message": "session is still busy; press Esc to interrupt"})
                    return {"ok": False, "error": "session busy"}
                self._busy = True
                self._generation += 1
                generation = self._generation
                session = self._session
                self._active_base = len(session.history)

            background = True
            threading.Thread(target=self._stream_worker, args=(session, text, generation), daemon=True).start()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            try:
                self._emit({"type": "error", "message": msg})
            except Exception:
                pass
            return {"ok": False, "error": msg}
        finally:
            if not background:
                with contextlib.suppress(Exception):
                    self._done()

    def _is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy

    def _handle_command(self, command: str, spec: commands.CommandSpec) -> bool:
        if command.startswith("!"):
            for e in commands.run_shell(self._session, command[1:].strip()):
                self._emit(e)
            self._session.persist()
            return True
        if command == "/new":
            self._new()
            return True
        if command == "/session":
            self._session_picker()
            return True
        if command == "/sessions" or command.startswith("/sessions "):
            self._sessions(command[9:].strip())
            return True
        if command == "/compact" or command.startswith("/compact "):
            self._compact(command[8:].strip())
            return True
        if command == "/help":
            self._help()
            return True
        if command == "/hotkeys":
            self._hotkeys()
            return True
        if command == "/skills":
            self._skills()
            return True
        if command == "/reload":
            self._reload()
            return True
        if command == "/quit":
            self._quit()
            return True
        if command == "/name" or command.startswith("/name "):
            self._name(command[5:].strip())
            return True
        if command == "/model" or command.startswith("/model "):
            self._model(command[6:].strip())
            return True
        if command.startswith("/"):
            for e in commands.load_skill(self._session, command[1:].strip()):
                self._emit(e)
            self._session.persist()
            return True
        return False

    def _stream_worker(self, session: ChatSession, text: str, generation: int) -> None:
        emit = lambda event: self._emit_current(generation, event)
        try:
            session.stream(text, emit)
            if generation == self._generation:
                self._sync_input_history()
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            finish = False
            with self._busy_lock:
                if generation == self._generation:
                    self._busy = False
                    self._active_base = None
                    finish = True
            if finish:
                self._done()

    def interrupt(self) -> dict:
        with self._busy_lock:
            was_busy = self._busy
            old_session = self._session
            base = self._active_base
            if was_busy:
                # Invalidate the active worker immediately so any late events are
                # ignored, but keep _busy true until the replacement session is
                # installed. Otherwise a fast next send can race onto the old
                # ChatSession.
                self._generation += 1

        if not was_busy:
            old_session.interrupt()
            return {"ok": True}

        # Python cannot safely kill a worker thread. Instead, cancel the old
        # request, abandon that ChatSession so late persistence is ignored, and
        # swap in a clean copy of the pre-turn session immediately.
        old_session.abandon()
        restored = _new_session()
        restored.model = old_session.model
        restored.reload_client()
        restored.session_name = old_session.session_name
        restored.input_history = list(old_session.input_history)
        restored.history = copy.deepcopy(old_session.history[:base]) if base is not None else []
        restored.persist()

        with self._busy_lock:
            self._session = restored
            self._session_choices = []
            self._busy = False
            self._active_base = None

        self._sync_input_history()
        self._done()
        return {"ok": True}

    def state(self) -> dict:
        return {
            "id": self._tab_id,
            "model": self._session.model,
            "session": self._session.session_name,
            "input_history": self._session.input_history,
            "context": self._session.context_usage(),
            "busy": self._is_busy(),
        }

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
            "models": list_model_names(),
            "session": self._session.session_name,
            "input_history": self._session.input_history,
            "context": self._session.context_usage(),
            "skills": skills.list_skills(),
            "commands": [
                {"usage": usage, "description": description}
                for usage, description in commands.command_rows()
            ],
            # Prefer the explicit name. Keep `tools` for compatibility with old JS.
            "available_tools": available_tools,
            "tools": available_tools,
        }

    def _help(self) -> None:
        self._emit({"type": "text", "content": commands.help_markdown(self._session_help_lines)})

    def _hotkeys(self) -> None:
        self._emit({"type": "text", "content": commands.hotkeys_markdown()})

    @staticmethod
    def _session_help_lines() -> list[str]:
        return [
            "",
            "## `/sessions`",
            "",
            "`/sessions` lists saved sessions for this folder. Below the list it shows these subcommands:",
            "",
            "- `/sessions resume <number|name>` — resume a session from the latest list, or by name.",
            "- `/sessions delete <number|name>` — delete a saved session. Deleting the active session starts a new one.",
            "- `/sessions clear confirm` — delete all saved sessions for this folder and start a new one.",
        ]

    def _skills(self) -> None:
        items = skills.list_skills()
        if not items:
            self._emit({"type": "text", "content": "_no skills found under `~/.klimt/skills`_"})
            return

        self._emit({"type": "text", "content": self._format_skills_table(items)})

    def _format_skills_table(self, items: list[dict[str, Any]]) -> str:
        lines = [
            "## Available skills",
            "",
            "| skill | description |",
            "|---|---|",
        ]
        for s in items:
            name = self._md_escape(s.get("name") or "")
            desc = self._md_escape(s.get("description") or "(no description)")
            lines.append(f"| `/{name}` | {desc} |")
        return "\n".join(lines)

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

    @staticmethod
    def _md_escape(text: object) -> str:
        return str(text or "").replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|")

    @staticmethod
    def _format_session_time(ts: object) -> str:
        try:
            value = float(ts or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return "unknown"
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")

    def _session_picker(self) -> None:
        sessions = self._session.list_sessions()
        self._session_choices = sessions
        if not sessions:
            self._emit({"type": "text", "content": "_no saved sessions for this folder_"})
            return

        self._emit({
            "type": "select",
            "title": "Session",
            "placeholder": "choose session...",
            "options": [
                {
                    "label": f"{s.get('name') or ''} · {self._format_session_time(s.get('updated'))}",
                    "value": f"/sessions resume {s.get('name') or ''}",
                    "current": (s.get("name") or "") == self._session.session_name,
                }
                for s in sessions
            ],
        })

    def _list_sessions(self) -> None:
        sessions = self._session.list_sessions()
        self._session_choices = sessions
        if not sessions:
            self._emit({"type": "text", "content": "_no saved sessions for this folder_"})
            return

        lines = [
            "## Sessions",
            "",
            "| # | name | model | updated | messages | inputs |",
            "|---:|---|---|---:|---:|---:|",
        ]
        for i, s in enumerate(sessions, start=1):
            name = self._md_escape(s.get("name") or "")
            model = self._md_escape(s.get("model") or "")
            updated = self._format_session_time(s.get("updated"))
            messages = int(s.get("messages") or 0)
            inputs = int(s.get("inputs") or 0)
            lines.append(f"| {i} | `{name}` | `{model}` | {updated} | {messages} | {inputs} |")
        lines.extend([
            "",
            "Commands:",
            "",
            "- `/sessions resume <number|name>` — resume a session from this list, or by name.",
            "- `/sessions delete <number|name>` — delete a saved session. Deleting the active session starts a new one.",
            "- `/sessions clear confirm` — delete all saved sessions for this folder and start a new one.",
        ])
        self._emit({"type": "text", "content": "\n".join(lines)})

    def _resolve_session_name(self, arg: str) -> str:
        """Resolve a session name or index from the latest `/sessions` list."""
        name = arg.strip()
        if not name:
            return ""
        if not name.isdecimal():
            return name

        index = int(name)
        if index <= 0:
            return name

        if not self._session_choices:
            self._session_choices = self._session.list_sessions()

        if 1 <= index <= len(self._session_choices):
            return str(self._session_choices[index - 1].get("name") or "")

        return name

    def _new(self) -> None:
        self._session.interrupt()
        self._session = _new_session()
        self._session_choices = []
        self._emit({"type": "clear"})
        self._sync_input_history()
        self._emit({"type": "text", "content": f"new session **{self._md_escape(self._session.session_name)}**"})

    def _sessions(self, arg: str) -> None:
        if not arg:
            self._list_sessions()
            return

        cmd, _, rest = arg.partition(" ")
        if cmd == "resume" and rest.strip():
            self._resume_session(rest.strip())
            return
        if cmd == "delete" and rest.strip():
            self._delete_session(rest.strip())
            return
        if cmd == "clear" and rest.strip() == "confirm":
            self._clear_sessions()
            return

        self._emit({"type": "text", "content": "_usage: `/sessions`, `/sessions resume <number|name>`, `/sessions delete <number|name>`, or `/sessions clear confirm`_"})

    def _delete_session(self, target: str) -> None:
        name = self._resolve_session_name(target)
        if not self._session.store.exists(name):
            self._emit({"type": "text", "content": f"_unknown session: `{self._md_escape(target)}`_"})
            return

        active = name == self._session.session_name
        self._session.store.delete(name)
        self._session_choices = []
        if active:
            self._new()
            self._emit({"type": "text", "content": f"deleted active session **{self._md_escape(name)}**"})
            return

        self._emit({"type": "text", "content": f"deleted session **{self._md_escape(name)}**"})
        self._list_sessions()

    def _clear_sessions(self) -> None:
        self._session.interrupt()
        self._session.store.clear()
        self._session = _new_session()
        self._session_choices = []
        self._emit({"type": "clear"})
        self._sync_input_history()
        self._emit({"type": "text", "content": "deleted all sessions for this folder"})
        self._emit({"type": "text", "content": f"new session **{self._md_escape(self._session.session_name)}**"})

    def _resume_session(self, name: str) -> None:
        if not name:
            self._emit({"type": "text", "content": "_usage: `/sessions resume <number|name>`_"})
            return

        requested = name.strip()
        resolved = self._resolve_session_name(requested)
        if not self._session.load_session(resolved):
            hint = ""
            if requested.isdecimal():
                hint = "\n\nRun `/sessions` to refresh the numbered list."
            self._emit({
                "type": "text",
                "content": f"_unknown session: `{self._md_escape(requested)}`_{hint}",
            })
            return

        self._session_choices = []
        self._replay_session()
        self._sync_input_history()
        self._emit({"type": "text", "content": f"resumed session **{self._md_escape(self._session.session_name)}**"})

    def _name(self, name: str) -> None:
        if not name:
            current = self._session.session_name
            self._emit({"type": "text", "content": f"current session: **{current}**\n\n_usage: `/name <session-name>`_"})
            return
        self._session.rename_session(name)
        self._sync_input_history()
        self._emit({"type": "text", "content": f"session named **{self._session.session_name}**"})

    def _model(self, model: str) -> None:
        configs = list_model_configs()
        choices = [cfg.name for cfg in configs]
        if not model:
            if not choices:
                self._emit({"type": "text", "content": "_no models configured; create `~/.klimt/models.json`_"})
                return
            self._emit({
                "type": "select",
                "title": "Model",
                "placeholder": "choose model...",
                "options": [
                    {
                        "label": f"{cfg.name} · {cfg.provider} · {cfg.provider_model()}",
                        "value": f"/model {cfg.name}",
                        "current": cfg.name == self._session.model,
                    }
                    for cfg in configs
                ],
            })
            return

        requested = model.strip()
        if requested not in choices:
            self._emit({
                "type": "text",
                "content": (
                    f"_unknown model: `{self._md_escape(requested)}`_\n\n"
                    "Configured choices: "
                    + (", ".join(f"`{self._md_escape(x)}`" for x in choices) if choices else "_none; create `~/.klimt/models.json`_")
                ),
            })
            return

        self._session.interrupt()
        self._session.model = requested
        self._session.reload_client()
        self._session.persist()
        self._sync_input_history()
        self._emit({"type": "text", "content": f"model set to **{self._md_escape(requested)}**"})

    def _reload(self) -> None:
        """Reload local config, prompt layers, skill/tool modules, model config, and CSS."""
        importlib.reload(prompt)
        importlib.reload(skills)
        importlib.reload(tools)
        self._session.system = _build_system_prompt()
        self._session.context_window = int(os.environ.get("KLIMT_CONTEXT_WINDOW", "128000"))
        self._session.reload_client()
        self._emit({"type": "reload_css"})
        self._sync_input_history()
        self._emit({"type": "text", "content": "reloaded config, skills, tools, model endpoint, and CSS"})

    def _quit(self) -> None:
        """Exit the process immediately."""
        os._exit(0)


class Api:
    """Exposed to JS via window.pywebview.api.*"""

    def __init__(self, session: ChatSession) -> None:
        self._window = None  # set by attach_window
        self._tabs_lock = threading.Lock()
        self._tabs: dict[str, _SingleTabApi] = {}
        self._first_tab_id = "tab-1"
        self._tabs[self._first_tab_id] = _SingleTabApi(session, self._first_tab_id, self._emit)

    def attach_window(self, window) -> None:
        self._window = window

    def _emit(self, tab_id: str, event: dict) -> None:
        if self._window is None:
            return
        payload = json.dumps({**event, "tabId": tab_id})
        self._window.evaluate_js(f"window.klimt.handleEvent({payload})")

    def _tab(self, tab_id: str | None) -> _SingleTabApi:
        with self._tabs_lock:
            if tab_id and tab_id in self._tabs:
                return self._tabs[tab_id]
            return self._tabs[self._first_tab_id]

    def info(self) -> dict:
        available_tools = [
            {
                "name": schema.get("function", {}).get("name", ""),
                "description": schema.get("function", {}).get("description", ""),
            }
            for schema in tools.SCHEMAS
        ]
        with self._tabs_lock:
            tab_states = [tab.state() for tab in self._tabs.values()]
        first = self._tab(self._first_tab_id).state()
        return {
            "version": __version__,
            "model": first["model"],
            "models": list_model_names(),
            "session": first["session"],
            "input_history": first["input_history"],
            "context": first["context"],
            "skills": skills.list_skills(),
            "commands": [
                {"usage": usage, "description": description}
                for usage, description in commands.command_rows()
            ],
            "available_tools": available_tools,
            "tools": available_tools,
            "tabs": tab_states,
            "active_tab": self._first_tab_id,
        }

    def send(self, text: str, tab_id: str | None = None) -> dict:
        return self._tab(tab_id).send(text)

    def interrupt(self, tab_id: str | None = None) -> dict:
        return self._tab(tab_id).interrupt()

    def new_tab(self) -> dict:
        with self._tabs_lock:
            tab_id = "tab-" + uuid.uuid4().hex[:8]
            self._tabs[tab_id] = _SingleTabApi(_new_session(), tab_id, self._emit)
            return {"ok": True, "tab": self._tabs[tab_id].state()}

    def close_tab(self, tab_id: str) -> dict:
        with self._tabs_lock:
            if tab_id not in self._tabs:
                return {"ok": False, "error": "unknown tab"}
            if len(self._tabs) <= 1:
                return {"ok": False, "error": "cannot close last tab"}
            tab = self._tabs[tab_id]
            if tab._is_busy():
                return {"ok": False, "error": "cannot close a busy tab; press Esc to interrupt it first"}
            self._tabs.pop(tab_id)
            if tab_id == self._first_tab_id:
                self._first_tab_id = next(iter(self._tabs))
        tab.interrupt()
        return {"ok": True, "active_tab": self._first_tab_id}

    def tabs(self) -> dict:
        with self._tabs_lock:
            return {"tabs": [tab.state() for tab in self._tabs.values()], "active_tab": self._first_tab_id}


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
