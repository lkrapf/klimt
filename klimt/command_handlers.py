"""Slash-command handlers.

Each handler is a module-level function taking a CommandContext and the
already-stripped argument string. The bridge (`_SingleTabApi`) wraps itself
in a CommandContext and dispatches via HANDLERS.

This keeps app.py focused on bridge/threading concerns and gives commands
one obvious home.
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from . import agents, commands, presenters, prompt, skills, themes, tools
from .api import ChatSession
from .model_config import list_model_classes, list_model_configs
from .session_factory import build_system_prompt, new_session

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .tab_api import _SingleTabApi


@dataclass
class CommandContext:
    """Adapter around the tab bridge for command handlers.

    Handlers read and mutate the tab's session, session choices, and theme,
    and emit UI events. The context centralizes accessors so handlers don't
    poke at private tab attributes directly.
    """
    tab: "_SingleTabApi"

    # session ------------------------------------------------------------
    @property
    def session(self) -> ChatSession:
        return self.tab._session

    @session.setter
    def session(self, value: ChatSession) -> None:
        self.tab._session = value

    # bridge -------------------------------------------------------------
    def emit(self, event: dict) -> None:
        self.tab._emit(event)

    def sync(self) -> None:
        self.tab._sync_input_history()

    def replay(self) -> None:
        self.tab._replay_session()

    def new_session(self, *, cwd: str | None = None, model: str | None = None) -> ChatSession:
        return new_session(cwd, model=model)

    def build_system_prompt(self, cwd: str) -> str:
        return build_system_prompt(cwd, self.session.model)

    def get_theme(self) -> str:
        return self.tab._get_theme()

    def set_theme(self, name: str) -> None:
        self.tab._set_theme(name)

    @property
    def pending_back(self) -> list[dict] | None:
        return self.tab._pending_back

    @pending_back.setter
    def pending_back(self, value: list[dict] | None) -> None:
        self.tab._pending_back = value

    @property
    def pending_sessions(self) -> list[dict] | None:
        return self.tab._pending_sessions

    @pending_sessions.setter
    def pending_sessions(self, value: list[dict] | None) -> None:
        self.tab._pending_sessions = value


Handler = Callable[[CommandContext, str], None]


# --- handlers ---------------------------------------------------------------


def help_(ctx: CommandContext, arg: str) -> None:
    ctx.emit({"type": "text", "content": commands.help_markdown()})


def hotkeys(ctx: CommandContext, arg: str) -> None:
    ctx.emit({"type": "text", "content": commands.hotkeys_markdown()})


def skills_(ctx: CommandContext, arg: str) -> None:
    ctx.emit({"type": "text", "content": presenters.skills_markdown(skills.list_skills())})


def agents_(ctx: CommandContext, arg: str) -> None:
    items = agents.list_agents(ctx.session.cwd)
    ctx.emit({"type": "text", "content": presenters.agents_markdown(items, list_model_classes())})


def cd(ctx: CommandContext, arg: str) -> None:
    session = ctx.session
    if not arg:
        ctx.emit({"type": "text", "content": f"cwd: `{presenters.md_escape(session.cwd)}`\n\n_usage: `/cd <path>`_"})
        return

    wanted = Path(arg).expanduser()
    if not wanted.is_absolute():
        wanted = Path(session.cwd) / wanted
    try:
        resolved = wanted.resolve(strict=True)
    except FileNotFoundError:
        ctx.emit({"type": "text", "content": f"_no such directory: `{presenters.md_escape(arg)}`_"})
        return
    if not resolved.is_dir():
        ctx.emit({"type": "text", "content": f"_not a directory: `{presenters.md_escape(arg)}`_"})
        return

    session.cwd = str(resolved)
    session.system = ctx.build_system_prompt(session.cwd)
    session.store = session.store.for_folder(session.cwd)
    # Do NOT persist here — that would write the current session into the new
    # folder's store, making it appear in /sessions for the wrong directory.
    ctx.sync()
    ctx.emit({"type": "text", "content": f"cwd set to `{presenters.md_escape(session.cwd)}`"})


def compact(ctx: CommandContext, arg: str) -> None:
    keep_recent = 8
    if arg:
        try:
            keep_recent = int(arg)
        except ValueError:
            ctx.emit({"type": "text", "content": "_usage: `/compact [recent-message-count]`_"})
            return
    ctx.emit({"type": "text", "content": f"compacting context, keeping last {keep_recent} messages raw..."})
    result = ctx.session.compact(keep_recent)
    ctx.replay()
    ctx.sync()
    ctx.emit({"type": "text", "content": result})


def new(ctx: CommandContext, arg: str) -> None:
    prior_model = ctx.session.model
    ctx.session.interrupt()
    ctx.session = ctx.new_session(cwd=ctx.session.cwd, model=prior_model)
    ctx.emit({"type": "clear"})
    ctx.sync()
    ctx.emit({"type": "text", "content": f"new session **{presenters.md_escape(ctx.session.session_name)}**"})


def session_(ctx: CommandContext, arg: str) -> None:
    _resume_session(ctx, arg, usage="/session <name>")


def sessions(ctx: CommandContext, arg: str) -> None:
    items = ctx.session.list_sessions()
    if not items:
        ctx.emit({"type": "text", "content": "_no saved sessions for this folder_"})
        return
    ctx.pending_sessions = items
    ctx.emit({"type": "text", "content": presenters.sessions_markdown(items)})


def save(ctx: CommandContext, arg: str) -> None:
    session = ctx.session
    already_kept = session.kept
    if arg:
        session.rename_session(arg)
    else:
        session.keep()
    ctx.sync()
    if already_kept and not arg:
        state = "saved" if session.kept else "not saved (in memory only)"
        ctx.emit({
            "type": "text",
            "content": f"session **{presenters.md_escape(session.session_name)}** \u2014 {state}\n\n_usage: `/save <name>` to rename_",
        })
        return
    ctx.emit({"type": "text", "content": f"saved session **{presenters.md_escape(session.session_name)}**"})


def theme(ctx: CommandContext, arg: str) -> None:
    choices = themes.list_theme_names()
    current = ctx.get_theme()
    if not arg:
        ctx.emit({"type": "text", "content": presenters.themes_markdown(choices, current)})
        return

    requested = arg.strip()
    if requested not in choices:
        ctx.emit({"type": "text", "content": presenters.unknown_choice_markdown("theme", requested, choices)})
        return

    ctx.set_theme(requested)
    ctx.emit({"type": "theme", "name": requested})
    ctx.emit({"type": "text", "content": f"theme set to **{presenters.md_escape(requested)}**"})


def model(ctx: CommandContext, arg: str) -> None:
    configs = list_model_configs()
    choices = [cfg.name for cfg in configs]
    if not arg:
        ctx.emit({"type": "text", "content": presenters.models_markdown(configs, ctx.session.model)})
        return

    requested = arg.strip()
    if requested not in choices:
        ctx.emit({
            "type": "text",
            "content": presenters.unknown_choice_markdown(
                "model",
                requested,
                choices,
                empty_hint="_none; create `~/.klimt/models.json`_",
            ),
        })
        return

    ctx.session.interrupt()
    ctx.session.model = requested
    ctx.session.reload_client()
    # Rebuild the system prompt so the runtime tool manifest matches the new
    # model's capabilities (e.g. drops or restores the `visual` tool).
    ctx.session.system = ctx.build_system_prompt(ctx.session.cwd)
    ctx.session.persist()
    ctx.sync()
    ctx.emit({"type": "text", "content": f"model set to **{presenters.md_escape(requested)}**"})


def reload_(ctx: CommandContext, arg: str) -> None:
    """Reload local config, prompt layers, skill/tool modules, model config, and CSS."""
    importlib.reload(prompt)
    importlib.reload(skills)
    importlib.reload(tools)
    ctx.session.system = ctx.build_system_prompt(ctx.session.cwd)
    ctx.session.reload_client()
    ctx.emit({"type": "theme", "name": ctx.get_theme()})
    ctx.emit({"type": "reload_css"})
    ctx.sync()
    ctx.emit({"type": "text", "content": "reloaded config, skills, tools, model endpoint, and CSS"})


def quit_(ctx: CommandContext, arg: str) -> None:
    """Exit the process immediately."""
    os._exit(0)


def back(ctx: CommandContext, arg: str) -> None:
    turns = ctx.session.back_turns()
    if not turns:
        ctx.emit({"type": "text", "content": "_nothing to go back to_"})
        return
    from . import presenters
    ctx.pending_back = turns
    ctx.emit({"type": "text", "content": presenters.back_markdown(turns)})


def load_skill(ctx: CommandContext, name: str) -> None:
    """Fallback handler for `/<skill>`. Loads the named skill into history."""
    for e in commands.load_skill(ctx.session, name):
        ctx.emit(e)
    ctx.session.persist()


# --- sessions sub-helpers ----------------------------------------------------


def _delete_session_by_name(ctx: CommandContext, name: str) -> None:
    if not ctx.session.store.exists(name):
        ctx.emit({"type": "text", "content": f"_unknown session: `{presenters.md_escape(name)}`_"})
        return

    active = name == ctx.session.session_name
    ctx.session.store.delete(name)
    if active:
        new(ctx, "")
        ctx.emit({"type": "text", "content": f"deleted active session **{presenters.md_escape(name)}**"})
        return

    ctx.emit({"type": "text", "content": f"deleted session **{presenters.md_escape(name)}**"})
    # Re-show the updated list.
    items = ctx.session.list_sessions()
    if items:
        ctx.pending_sessions = items
        ctx.emit({"type": "text", "content": presenters.sessions_markdown(items)})


def _clear_sessions(ctx: CommandContext) -> None:
    prior_model = ctx.session.model
    ctx.session.interrupt()
    old_cwd = ctx.session.cwd
    ctx.session.store.clear()
    ctx.session = ctx.new_session(cwd=old_cwd, model=prior_model)
    ctx.emit({"type": "clear"})
    ctx.sync()
    ctx.emit({"type": "text", "content": "deleted all sessions for this folder"})
    ctx.emit({"type": "text", "content": f"new session **{presenters.md_escape(ctx.session.session_name)}**"})


def _resume_session_by_name(ctx: CommandContext, name: str) -> None:
    if not ctx.session.load_session(name):
        ctx.emit({"type": "text", "content": f"_unknown session: `{presenters.md_escape(name)}`_"})
        return

    ctx.replay()
    ctx.sync()
    ctx.emit({"type": "text", "content": f"resumed session **{presenters.md_escape(ctx.session.session_name)}**"})


# --- dispatch ----------------------------------------------------------------


# Map "/name" → (handler, prefix_length_to_strip_for_arg).
# `arg_offset` is the length of "/name" so `command[arg_offset:].strip()` yields
# the argument. For commands like "/new" with no arg, this still works.
HANDLERS: dict[str, tuple[Handler, int]] = {
    "/help": (help_, len("/help")),
    "/hotkeys": (hotkeys, len("/hotkeys")),
    "/skills": (skills_, len("/skills")),
    "/agents": (agents_, len("/agents")),
    "/cd": (cd, len("/cd")),
    "/compact": (compact, len("/compact")),
    "/new": (new, len("/new")),
    "/session": (session_, len("/session")),
    "/sessions": (sessions, len("/sessions")),
    "/save": (save, len("/save")),
    "/theme": (theme, len("/theme")),
    "/model": (model, len("/model")),
    "/reload": (reload_, len("/reload")),
    "/quit": (quit_, len("/quit")),
    "/back": (back, len("/back")),
}


def dispatch(ctx: CommandContext, command: str) -> bool:
    """Run the handler for `command`. Returns True if a handler ran."""
    if not command.startswith("/"):
        return False

    head, _, _ = command.partition(" ")
    spec = HANDLERS.get(head)
    if spec is not None:
        handler, prefix_len = spec
        arg = command[prefix_len:].strip()
        handler(ctx, arg)
        return True

    # /<skill> fallback
    load_skill(ctx, command[1:].strip())
    return True
