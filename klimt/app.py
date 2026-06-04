"""Application entrypoint: spawns the pywebview window and wires the JS bridge."""
from __future__ import annotations

import copy
import importlib
import json
import os
import contextlib
import threading
import uuid
import webbrowser
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
from typing import Any

import webview

from . import __version__, agents, commands, completion, prompt, skills, themes, tools
from .api import ChatSession
from .model_config import default_model_name, list_model_configs, list_model_names

WEB_DIR = Path(__file__).parent / "web"
ASSETS_DIR = Path(__file__).parent / "assets"
ICON_PATH = ASSETS_DIR / "klimt-icon.png"

def _build_system_prompt(cwd: str | None = None) -> str:
    catalog = agents.build_catalog_manifest(agents.list_agents(cwd))
    return prompt.build_system_prompt(
        tools.SCHEMAS,
        skills.list_skills(),
        cwd=cwd,
        agent_manifest=catalog,
    )


def _new_session(cwd: str | None = None, model: str | None = None) -> ChatSession:
    cwd = str(Path(cwd or os.getcwd()).expanduser().resolve())
    chosen = (model or "").strip() or default_model_name()
    # If the caller passes a model that no longer resolves (e.g. removed from
    # models.json between sessions), fall back rather than crash.
    try:
        from .model_config import resolve_model_config
        resolve_model_config(chosen)
    except KeyError:
        chosen = default_model_name()
    return ChatSession(
        model=chosen,
        system=_build_system_prompt(cwd),
        cwd=cwd,
    )


class _SingleTabApi:
    """One independent chat tab. Exposed through the multi-tab Api below."""

    def __init__(self, session: ChatSession, tab_id: str, emit, get_theme=None, set_theme=None) -> None:
        self._session = session
        self._tab_id = tab_id
        self._emit_to_window = emit
        self._get_theme = get_theme or themes.default_theme
        self._set_theme = set_theme or (lambda name: None)
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
        self._emit({"type": "cwd", "path": self._session.cwd})
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
                if command.startswith("!"):
                    # Shell commands can block arbitrarily long; run off the bridge thread
                    # so the UI doesn't freeze on the "working..." spinner while waiting.
                    # sync=True tells JS to skip the thinking indicator — output arrives
                    # as a tool event, not as an LLM stream.
                    background = True
                    threading.Thread(
                        target=self._shell_worker,
                        args=(command,),
                        daemon=True,
                    ).start()
                    return {"ok": True}
                handled = self._handle_command(command, spec)
                if handled:
                    return {"ok": True}

            self._session.maybe_title_from_first_input(command)
            self._session.remember_input(command)
            # No-op until the session is kept; auto-titling above only sets the
            # tab label. Use /save to persist to disk.
            self._session.persist()
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
        if command == "/new":
            self._new()
            return True
        if command == "/session" or command.startswith("/session "):
            self._resume_session(command[8:].strip(), usage="/session <name>")
            return True
        if command == "/sessions" or command.startswith("/sessions "):
            self._sessions(command[9:].strip())
            return True
        if command == "/compact" or command.startswith("/compact "):
            self._compact(command[8:].strip())
            return True
        if command == "/cd" or command.startswith("/cd "):
            self._cd(command[3:].strip())
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
        if command == "/agents":
            self._agents()
            return True
        if command == "/reload":
            self._reload()
            return True
        if command == "/theme" or command.startswith("/theme "):
            self._theme(command[6:].strip())
            return True
        if command == "/quit":
            self._quit()
            return True
        if command == "/save" or command.startswith("/save "):
            self._save(command[5:].strip())
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

    def _shell_worker(self, command: str) -> None:
        try:
            for e in commands.run_shell(self._session, command[1:].strip()):
                self._emit(e)
            self._session.persist()
        except Exception as e:  # noqa: BLE001
            with contextlib.suppress(Exception):
                self._emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            with contextlib.suppress(Exception):
                self._done()

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
        restored = _new_session(old_session.cwd)
        restored.model = old_session.model
        restored.reload_client()
        restored.session_name = old_session.session_name
        restored.kept = old_session.kept
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
            "cwd": self._session.cwd,
            "busy": self._is_busy(),
        }

    def complete(self, text: str, cursor: int | None = None) -> dict:
        try:
            return completion.complete(self._session, text, cursor)
        except Exception as e:  # noqa: BLE001
            pos = int(cursor or 0)
            return {"range": {"start": pos, "end": pos}, "items": [], "error": f"{type(e).__name__}: {e}"}

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
            "cwd": self._session.cwd,
            "skills": skills.list_skills(),
            "commands": [
                {"usage": usage, "description": description}
                for usage, description in commands.command_rows()
            ],
            "theme": self._get_theme(),
            "themes": themes.list_theme_names(),
            # Prefer the explicit name. Keep `tools` for compatibility with old JS.
            "available_tools": available_tools,
            "tools": available_tools,
        }

    def _help(self) -> None:
        self._emit({"type": "text", "content": commands.help_markdown()})

    def _hotkeys(self) -> None:
        self._emit({"type": "text", "content": commands.hotkeys_markdown()})

    def _skills(self) -> None:
        items = skills.list_skills()
        if not items:
            self._emit({"type": "text", "content": "_no skills found under `~/.klimt/skills`_"})
            return

        self._emit({"type": "text", "content": self._format_skills_table(items)})

    def _agents(self) -> None:
        items = agents.list_agents(self._session.cwd)
        lines = [
            "## Available agents",
            "",
            "| agent | mode | tools | model | description | source |",
            "|---|---|---|---|---|---|",
        ]
        for a in items:
            tools_label = ", ".join(a.tools) if a.tools else "none"
            desc = self._table_cell(a.description or "(no description)")
            model_label = self._table_cell(a.model or "(inherits parent)")
            lines.append(
                f"| `{self._md_escape(a.name)}` | {a.mode} | {self._table_cell(tools_label)} | {model_label} | {desc} | {a.source} |"
            )

        from .model_config import list_model_classes
        classes = list_model_classes()
        if classes:
            lines.extend([
                "",
                "Model classes from `~/.klimt/models.json`: "
                + ", ".join(f"`{self._md_escape(c)}`" for c in classes)
                + ". Use as the `model` argument to `agent` or as the `model:` field in an agent file.",
            ])
        self._emit({"type": "text", "content": "\n".join(lines)})

    def _format_skills_table(self, items: list[dict[str, Any]]) -> str:
        lines = [
            "## Available skills",
            "",
            "| skill | description |",
            "|---|---|",
        ]
        for s in items:
            name = self._md_escape(s.get("name") or "")
            desc = self._table_cell(s.get("description") or "(no description)")
            lines.append(f"| `/{name}` | {desc} |")
        return "\n".join(lines)

    def _cd(self, arg: str) -> None:
        if not arg:
            self._emit({"type": "text", "content": f"cwd: `{self._md_escape(self._session.cwd)}`\n\n_usage: `/cd <path>`_"})
            return

        wanted = Path(arg).expanduser()
        if not wanted.is_absolute():
            wanted = Path(self._session.cwd) / wanted
        try:
            resolved = wanted.resolve(strict=True)
        except FileNotFoundError:
            self._emit({"type": "text", "content": f"_no such directory: `{self._md_escape(arg)}`_"})
            return
        if not resolved.is_dir():
            self._emit({"type": "text", "content": f"_not a directory: `{self._md_escape(arg)}`_"})
            return

        self._session.cwd = str(resolved)
        self._session.system = _build_system_prompt(self._session.cwd)
        self._session.store = self._session.store.for_folder(self._session.cwd)
        self._session.persist()
        self._sync_input_history()
        self._emit({"type": "text", "content": f"cwd set to `{self._md_escape(self._session.cwd)}`"})

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
    def _table_cell(text: object) -> str:
        """Minimal escaping for a plain (non-code-span) Markdown table cell."""
        return str(text or "").replace("|", "\\|")

    @staticmethod
    def _md_escape(text: object) -> str:
        """Escaping for values embedded in backtick code spans or inline code."""
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
            name = self._table_cell(s.get("name") or "")
            model = self._table_cell(s.get("model") or "")
            updated = self._format_session_time(s.get("updated"))
            messages = int(s.get("messages") or 0)
            inputs = int(s.get("inputs") or 0)
            lines.append(f"| {i} | {name} | {model} | {updated} | {messages} | {inputs} |")
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
        prior_model = self._session.model
        self._session.interrupt()
        self._session = _new_session(self._session.cwd, model=prior_model)
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
            self._resume_session(rest.strip(), usage="/sessions resume <number|name>")
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
        prior_model = self._session.model
        self._session.interrupt()
        old_cwd = self._session.cwd
        self._session.store.clear()
        self._session = _new_session(old_cwd, model=prior_model)
        self._session_choices = []
        self._emit({"type": "clear"})
        self._sync_input_history()
        self._emit({"type": "text", "content": "deleted all sessions for this folder"})
        self._emit({"type": "text", "content": f"new session **{self._md_escape(self._session.session_name)}**"})

    def _resume_session(self, name: str, usage: str) -> None:
        if not name:
            self._emit({"type": "text", "content": f"_usage: `{usage}`_"})
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

    def _save(self, name: str) -> None:
        already_kept = self._session.kept
        self._session.rename_session(name) if name else self._session.keep()
        self._sync_input_history()
        if already_kept and not name:
            state = "saved" if self._session.kept else "not saved (in memory only)"
            self._emit({"type": "text", "content": f"session **{self._md_escape(self._session.session_name)}** — {state}\n\n_usage: `/save <name>` to rename_"})
            return
        self._emit({"type": "text", "content": f"saved session **{self._md_escape(self._session.session_name)}**"})

    def _theme(self, theme: str) -> None:
        choices = themes.list_theme_names()
        current = self._get_theme()
        if not theme:
            if not choices:
                self._emit({"type": "text", "content": "_no themes found under `klimt/web/themes`_"})
                return
            rows = [
                "## Themes",
                "",
                "| name | current |",
                "|---|---|",
            ]
            for name in choices:
                rows.append(f"| `{self._table_cell(name)}` | {'yes' if name == current else ''} |")
            rows.extend(["", "_usage: `/theme <name>`; use Tab to complete names._"])
            self._emit({"type": "text", "content": "\n".join(rows)})
            return

        requested = theme.strip()
        if requested not in choices:
            self._emit({
                "type": "text",
                "content": (
                    f"_unknown theme: `{self._md_escape(requested)}`_\n\n"
                    "Available choices: "
                    + (", ".join(f"`{self._md_escape(x)}`" for x in choices) if choices else "_none_")
                ),
            })
            return

        self._set_theme(requested)
        self._emit({"type": "theme", "name": requested})
        self._emit({"type": "text", "content": f"theme set to **{self._md_escape(requested)}**"})

    def _model(self, model: str) -> None:
        configs = list_model_configs()
        choices = [cfg.name for cfg in configs]
        if not model:
            if not choices:
                self._emit({"type": "text", "content": "_no models configured; create `~/.klimt/models.json`_"})
                return
            rows = [
                "## Models",
                "",
                "| name | provider | model | current |",
                "|---|---|---|---|",
            ]
            for cfg in configs:
                current = "yes" if cfg.name == self._session.model else ""
                rows.append(
                    f"| {self._table_cell(cfg.name)} | {self._table_cell(cfg.provider)} | {self._table_cell(cfg.provider_model())} | {current} |"
                )
            rows.extend(["", "_usage: `/model <name>`; use Tab to complete names._"])
            self._emit({"type": "text", "content": "\n".join(rows)})
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
        self._session.system = _build_system_prompt(self._session.cwd)
        self._session.reload_client()
        self._emit({"type": "theme", "name": self._get_theme()})
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
        self._theme = themes.load_theme()
        self._tabs[self._first_tab_id] = _SingleTabApi(session, self._first_tab_id, self._emit, self._get_theme, self._set_theme)

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

    def _get_theme(self) -> str:
        return self._theme

    def _set_theme(self, name: str) -> None:
        themes.save_theme(name)
        self._theme = name

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
            "cwd": first["cwd"],
            "skills": skills.list_skills(),
            "commands": [
                {"usage": usage, "description": description}
                for usage, description in commands.command_rows()
            ],
            "available_tools": available_tools,
            "tools": available_tools,
            "theme": self._get_theme(),
            "themes": themes.list_theme_names(),
            "tabs": tab_states,
            "active_tab": self._first_tab_id,
        }

    def send(self, text: str, tab_id: str | None = None) -> dict:
        return self._tab(tab_id).send(text)

    def interrupt(self, tab_id: str | None = None) -> dict:
        return self._tab(tab_id).interrupt()

    def complete(self, text: str, cursor: int | None = None, tab_id: str | None = None) -> dict:
        return self._tab(tab_id).complete(text, cursor)

    def new_tab(self, model: str | None = None) -> dict:
        with self._tabs_lock:
            tab_id = "tab-" + uuid.uuid4().hex[:8]
            session = _new_session(model=model)
            self._tabs[tab_id] = _SingleTabApi(session, tab_id, self._emit, self._get_theme, self._set_theme)
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

    # Allowed URL schemes for external open. Anything else is refused so a
    # rendered link cannot redirect the Klimt window or invoke arbitrary
    # handlers (file:, javascript:, custom protocols, ...).
    _ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})

    def open_url(self, url: str) -> dict:
        """Open a URL in the user's default browser, never in the Klimt window.

        Called from JS when an anchor in rendered content is clicked. The JS
        side preventDefaults the navigation regardless; this side validates
        the scheme before handing off to the OS.
        """
        try:
            target = str(url or "").strip()
            if not target:
                return {"ok": False, "error": "empty url"}
            parsed = urlparse(target)
            scheme = (parsed.scheme or "").lower()
            if scheme not in self._ALLOWED_URL_SCHEMES:
                return {"ok": False, "error": f"refused scheme: {scheme or '(none)'}"}
            webbrowser.open(target)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
        url=f"{WEB_DIR / 'index.html'}?theme={themes.load_theme()}",
        js_api=api,
        width=900,
        height=720,
        min_size=(500, 400),
        text_select=True,
    )
    api.attach_window(window)
    webview.start()
