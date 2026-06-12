"""pywebview bridge classes.

`_SingleTabApi` is one chat tab: session lifecycle, busy/interrupt
mechanics, streaming worker, and slash/bang dispatch. `Api` is the
multi-tab facade exposed to JS via `window.pywebview.api.*`.

This module owns no application policy. UI text comes from presenters,
slash commands come from command_handlers, and session construction
comes from session_factory.
"""
from __future__ import annotations

import contextlib
import copy
import json
import threading
import uuid
import webbrowser
from urllib.parse import urlparse
from typing import Any

from . import __version__, command_handlers, commands, completion, compaction, skills, themes, tools
from .api import ChatSession
from .model_config import list_model_names
from .session_factory import new_session
from .tool_impl import visual as _visual
from .tool_impl.limits import VISUAL_MAX_BYTES


def _resolve_from_items(token: str, items: list[dict[str, Any]]) -> str | None:
    """Resolve a 1-based numeric token or bare name against a sessions list.

    Returns the session name string, or None if the token is invalid.
    """
    token = token.strip()
    if token.isdecimal():
        idx = int(token) - 1
        if 0 <= idx < len(items):
            return str(items[idx].get("name") or "")
        return None
    # bare name — accept as-is (let the caller verify existence)
    return token if token else None


class _SingleTabApi:
    """One independent chat tab. Exposed through the multi-tab Api below."""

    # How long interrupt() waits for the in-flight worker to flush partial
    # state into history before falling back to the pre-turn snapshot. The
    # runner's cancel path is bounded by the slowest in-flight tool call;
    # 5s covers any sane tool (network is the worst case). On timeout we
    # still recover safely — we just lose the partial assistant message.
    _INTERRUPT_DRAIN_SECONDS = 5.0

    def __init__(self, session: ChatSession, tab_id: str, emit, get_theme=None, set_theme=None) -> None:
        self._session = session
        self._tab_id = tab_id
        self._emit_to_window = emit
        self._get_theme = get_theme or themes.default_theme
        self._set_theme = set_theme or (lambda name: None)
        self._pending_back: list[dict[str, Any]] | None = None
        self._pending_sessions: list[dict[str, Any]] | None = None
        self._busy = False
        self._busy_lock = threading.Lock()
        self._generation = 0
        self._active_base: int | None = None
        self._worker: threading.Thread | None = None

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
        tool_names: dict[str, str] = {}
        tool_args: dict[str, dict[str, Any]] = {}
        for msg in self._session.history:
            role = msg.get("role")
            if role == "user":
                content = msg.get("content") or ""
                replay_role = "system" if content.startswith(compaction.COMPACTED_NOTE_PREFIX) else "user"
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

    def send(self, text: str, attachments: list | None = None) -> dict:
        background = False
        try:
            # Intercept pending interactive prompts before any other dispatch.
            if self._pending_back is not None:
                turns = self._pending_back
                self._pending_back = None
                return self._handle_back_reply(text.strip(), turns)
            if self._pending_sessions is not None:
                items = self._pending_sessions
                self._pending_sessions = None
                return self._handle_sessions_reply(text.strip(), items)

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

            err = self._validate_attachments(attachments)
            if err:
                self._emit({"type": "error", "message": err})
                return {"ok": False, "error": err}

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
            worker = threading.Thread(
                target=self._stream_worker,
                args=(session, text, generation, attachments),
                daemon=True,
            )
            self._worker = worker
            worker.start()
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

    def _validate_attachments(self, attachments: list | None) -> str | None:
        """Return an error string if any attachment is invalid, else None."""
        if not attachments:
            return None
        if not self._session.model_config().vision:
            return (
                f"error: image attachments require a vision-capable model, "
                f"but {self._session.model!r} does not support image input. "
                f"Switch to a vision-capable model (e.g. claude-sonnet-4-6) first."
            )
        import base64
        for i, att in enumerate(attachments):
            data = att.get("data") if isinstance(att, dict) else None
            if not data:
                return f"error: attachment {i}: missing base64 data"
            try:
                raw = base64.b64decode(data, validate=True)
            except Exception:
                return f"error: attachment {i}: invalid base64"
            if len(raw) == 0:
                return f"error: attachment {i}: empty image"
            if len(raw) > VISUAL_MAX_BYTES:
                return (
                    f"error: attachment {i}: image too large ({len(raw)} bytes, "
                    f"cap {VISUAL_MAX_BYTES}); resize or crop first"
                )
            media_type = _visual._sniff_media_type(raw)
            if not media_type:
                return f"error: attachment {i}: unsupported format (expected PNG, JPEG, GIF, or WebP)"
            # Normalise: replace caller-supplied media_type with sniffed one
            # and embed byte count so envelope_summary is informative.
            att["media_type"] = media_type
            att["bytes"] = len(raw)
            att["_klimt_image"] = True
        return None

    def _is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy

    def _handle_back_reply(self, text: str, turns: list[dict[str, Any]]) -> dict:
        """Handle the user's reply to a /back turn-selection prompt."""
        parts = text.lower().split()
        summarize = "summary" in parts
        token = next((p for p in parts if p != "summary"), "")
        try:
            if not token.isdecimal():
                raise ValueError
            idx = int(token) - 1  # 1-based display → 0-based list
            if idx < 0 or idx >= len(turns):
                raise ValueError
        except ValueError:
            self._emit({"type": "text", "content": "_invalid selection — /back cancelled_"})
            self._done()
            return {"ok": False, "error": "invalid back selection"}

        cut = turns[idx]["cut"]
        if summarize:
            self._emit({"type": "text", "content": "summarizing dropped turns..."})
        result = self._session.rewind_to(cut, summarize=summarize)
        self._replay_session()
        self._sync_input_history()
        self._emit({"type": "text", "content": result})
        self._done()
        return {"ok": True}

    def _handle_sessions_reply(self, text: str, items: list[dict[str, Any]]) -> dict:
        """Handle the user's reply to an interactive /sessions prompt."""
        parts = text.lower().split()
        if not parts:
            self._emit({"type": "text", "content": "_cancelled_"})
            self._done()
            return {"ok": False, "error": "cancelled"}

        ctx = command_handlers.CommandContext(self)

        # clear
        if parts[0] == "clear":
            command_handlers._clear_sessions(ctx)
            return {"ok": True}

        # delete <n>
        if parts[0] == "delete" and len(parts) >= 2:
            token = parts[1]
            name = _resolve_from_items(token, items)
            if name is None:
                self._emit({"type": "text", "content": f"_invalid selection — /sessions cancelled_"})
                self._done()
                return {"ok": False, "error": "invalid selection"}
            command_handlers._delete_session_by_name(ctx, name)
            return {"ok": True}

        # <n>  — resume
        token = parts[0]
        name = _resolve_from_items(token, items)
        if name is None:
            self._emit({"type": "text", "content": "_invalid selection — /sessions cancelled_"})
            self._done()
            return {"ok": False, "error": "invalid selection"}
        command_handlers._resume_session_by_name(ctx, name)
        return {"ok": True}

    def _handle_command(self, command: str, spec: commands.CommandSpec) -> bool:
        return command_handlers.dispatch(command_handlers.CommandContext(self), command)

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

    def _stream_worker(self, session: ChatSession, text: str, generation: int, attachments: list | None = None) -> None:
        emit = lambda event: self._emit_current(generation, event)
        try:
            session.stream(text, emit, attachments=attachments)
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
            worker = self._worker
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
        # request and wait briefly for the runner to flush the in-flight
        # iteration into history (assistant message + matching tool results,
        # marked interrupted). Once the worker has returned, history is in a
        # structurally valid state and we can snapshot it whole.
        old_session.abandon()
        drained = False
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self._INTERRUPT_DRAIN_SECONDS)
            drained = not worker.is_alive()

        restored = new_session(old_session.cwd)
        restored.model = old_session.model
        restored.reload_client()
        restored.session_name = old_session.session_name
        restored.kept = old_session.kept
        restored.input_history = list(old_session.input_history)
        if drained:
            # Worker is done mutating history. Take everything it produced,
            # including the interrupted assistant message and any tool stubs.
            restored.history = copy.deepcopy(old_session.history)
        else:
            # Worker still running; we can't safely read its history. Fall
            # back to the pre-turn slice so the session stays valid, at the
            # cost of losing whatever the worker accumulated this turn.
            restored.history = copy.deepcopy(old_session.history[:base]) if base is not None else []
        restored.persist()

        with self._busy_lock:
            self._session = restored
            self._busy = False
            self._active_base = None
            self._worker = None

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


def _tool_summary() -> list[dict[str, str]]:
    return [
        {
            "name": schema.get("function", {}).get("name", ""),
            "description": schema.get("function", {}).get("description", ""),
        }
        for schema in tools.SCHEMAS
    ]


class Api:
    """Exposed to JS via window.pywebview.api.*"""

    # Allowed URL schemes for external open. Anything else is refused so a
    # rendered link cannot redirect the Klimt window or invoke arbitrary
    # handlers (file:, javascript:, custom protocols, ...).
    _ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})

    def __init__(self, session: ChatSession) -> None:
        self._window = None  # set by attach_window
        self._tabs_lock = threading.Lock()
        self._tabs: dict[str, _SingleTabApi] = {}
        self._first_tab_id = "tab-1"
        self._theme = themes.load_theme()
        self._tabs[self._first_tab_id] = _SingleTabApi(
            session, self._first_tab_id, self._emit, self._get_theme, self._set_theme
        )

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
        available_tools = _tool_summary()
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
            "theme": self._get_theme(),
            "themes": themes.list_theme_names(),
            "user_themes": [n for n in themes.list_theme_names() if themes.is_user_theme(n)],
            "tabs": tab_states,
            "active_tab": self._first_tab_id,
        }

    def send(self, text: str, tab_id: str | None = None, attachments: list | None = None) -> dict:
        return self._tab(tab_id).send(text, attachments=attachments)

    def interrupt(self, tab_id: str | None = None) -> dict:
        return self._tab(tab_id).interrupt()

    def complete(self, text: str, cursor: int | None = None, tab_id: str | None = None) -> dict:
        return self._tab(tab_id).complete(text, cursor)

    def new_tab(self, model: str | None = None) -> dict:
        with self._tabs_lock:
            tab_id = "tab-" + uuid.uuid4().hex[:8]
            session = new_session(model=model)
            self._tabs[tab_id] = _SingleTabApi(
                session, tab_id, self._emit, self._get_theme, self._set_theme
            )
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
            return {
                "tabs": [tab.state() for tab in self._tabs.values()],
                "active_tab": self._first_tab_id,
            }

    def get_theme_css(self, name: str) -> dict:
        """Return the CSS text for a user theme (those outside the package tree).

        Bundled themes are served as static files; this bridge method exists
        solely for themes installed in ~/.klimt/themes/ which pywebview cannot
        serve directly. JS calls this when is_user_theme is true.
        """
        path = themes.theme_path(name)
        if path is None:
            return {"ok": False, "error": f"unknown theme: {name}"}
        if not themes.is_user_theme(name):
            # Bundled theme — JS should load it as a <link>, not via bridge.
            return {"ok": False, "error": "not a user theme"}
        try:
            css = path.read_text(encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "css": css}

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
