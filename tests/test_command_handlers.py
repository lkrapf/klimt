"""Command dispatch unit tests.

These exercise the HANDLERS table and the dispatch function via a fake
CommandContext, without spinning up pywebview or a real ChatSession. The
goal is to lock the arg parsing and routing behavior that used to live
in _SingleTabApi._handle_command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from klimt import command_handlers


@dataclass
class FakeSession:
    cwd: str = "/tmp"
    model: str = "test-model"
    session_name: str = "session-x"
    kept: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    input_history: list[str] = field(default_factory=list)
    persisted: int = 0
    interrupted: int = 0

    def persist(self) -> None:
        self.persisted += 1

    def interrupt(self) -> None:
        self.interrupted += 1

    def list_sessions(self) -> list[dict[str, Any]]:
        return []


class FakeCtx:
    """Minimal CommandContext-compatible double."""

    def __init__(self) -> None:
        self.session = FakeSession()
        self.session_choices: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.synced = 0
        self.replays = 0
        self.theme = "dark"

    def emit(self, event: dict) -> None:
        self.events.append(event)

    def sync(self) -> None:
        self.synced += 1

    def replay(self) -> None:
        self.replays += 1

    def new_session(self, *, cwd: str | None = None, model: str | None = None) -> FakeSession:
        s = FakeSession(cwd=cwd or "/tmp", model=model or "test-model")
        return s

    def build_system_prompt(self, cwd: str) -> str:
        return f"system for {cwd}"

    def get_theme(self) -> str:
        return self.theme

    def set_theme(self, name: str) -> None:
        self.theme = name


def _text_events(events: list[dict[str, Any]]) -> list[str]:
    return [e.get("content", "") for e in events if e.get("type") == "text"]


# --- routing ---------------------------------------------------------------


def test_dispatch_returns_false_for_non_slash() -> None:
    ctx = FakeCtx()
    assert command_handlers.dispatch(ctx, "hello there") is False
    assert ctx.events == []


def test_dispatch_routes_help() -> None:
    ctx = FakeCtx()
    assert command_handlers.dispatch(ctx, "/help") is True
    assert ctx.events and ctx.events[0]["type"] == "text"
    assert "Commands" in ctx.events[0]["content"]


def test_dispatch_unknown_slash_falls_through_to_skill_loader(tmp_path) -> None:
    ctx = FakeCtx()
    ctx.session.cwd = str(tmp_path)
    assert command_handlers.dispatch(ctx, "/no-such-skill") is True
    # load_skill emits a text event explaining the failure for unknown skills.
    assert any("unknown skill" in (e.get("content") or "") for e in ctx.events)


def test_dispatch_strips_arg_prefix() -> None:
    ctx = FakeCtx()
    # /theme without arg lists themes; with an unknown arg shows an error.
    command_handlers.dispatch(ctx, "/theme nope")
    txt = "\n".join(_text_events(ctx.events))
    assert "unknown theme" in txt


# --- specific handlers -----------------------------------------------------


def test_help_emits_command_table() -> None:
    ctx = FakeCtx()
    command_handlers.help_(ctx, "")
    txt = ctx.events[0]["content"]
    assert "/help" in txt
    assert "/quit" in txt


def test_hotkeys_emits_table() -> None:
    ctx = FakeCtx()
    command_handlers.hotkeys(ctx, "")
    assert "Hotkeys" in ctx.events[0]["content"]


def test_compact_rejects_non_integer_arg() -> None:
    ctx = FakeCtx()
    command_handlers.compact(ctx, "not-a-number")
    assert "_usage:" in ctx.events[-1]["content"]


def test_theme_lists_when_no_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_handlers.themes, "list_theme_names", lambda: ["dark", "light"])
    ctx = FakeCtx()
    command_handlers.theme(ctx, "")
    assert "Themes" in ctx.events[0]["content"]


def test_theme_switches_when_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_handlers.themes, "list_theme_names", lambda: ["dark", "light"])
    ctx = FakeCtx()
    command_handlers.theme(ctx, "light")
    assert ctx.theme == "light"
    assert any(e.get("type") == "theme" and e.get("name") == "light" for e in ctx.events)


def test_theme_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_handlers.themes, "list_theme_names", lambda: ["dark"])
    ctx = FakeCtx()
    command_handlers.theme(ctx, "ghost")
    assert "unknown theme" in _text_events(ctx.events)[-1]


def test_sessions_dispatch_no_arg_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeCtx()
    monkeypatch.setattr(FakeSession, "list_sessions", lambda self: [])
    command_handlers.sessions(ctx, "")
    assert "no saved sessions" in _text_events(ctx.events)[-1]


def test_sessions_dispatch_unknown_subcommand_shows_usage() -> None:
    ctx = FakeCtx()
    command_handlers.sessions(ctx, "wat")
    assert "_usage:" in _text_events(ctx.events)[-1]


def test_save_renames_when_arg_given() -> None:
    ctx = FakeCtx()
    called: list[str] = []

    def fake_rename(self: FakeSession, name: str) -> None:
        called.append(name)
        self.session_name = name
        self.kept = True

    FakeSession.rename_session = fake_rename  # type: ignore[attr-defined]
    try:
        command_handlers.save(ctx, "my-name")
    finally:
        del FakeSession.rename_session
    assert called == ["my-name"]
    assert "saved session" in _text_events(ctx.events)[-1]
