"""run_shell (bang command) behavior."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from klimt import commands


def _session(cwd: Path) -> SimpleNamespace:
    return SimpleNamespace(
        _cancel=threading.Event(),
        cwd=str(cwd),
        history=[],
    )


def test_run_shell_executes_when_cancel_is_stale(tmp_path: Path):
    """Regression: a stale, pre-set _cancel must not abort a fresh bang command.

    Idle interrupts (Esc on a non-busy tab) leave session._cancel set with no
    one to clear it. Subsequent ! commands previously saw the flag, the bash
    poll loop killed the subprocess immediately, and the user got
    `exit=interrupted` with no output and no history append.
    """
    session = _session(tmp_path)
    session._cancel.set()  # simulate stuck-set state

    events = commands.run_shell(session, "echo hello")

    assert len(events) == 1, events
    ev = events[0]
    assert ev["type"] == "tool"
    assert ev["name"] == "bash"
    assert "exit=0" in ev["result"]
    assert "hello" in ev["result"]
    # And the history must be appended (the original bug skipped this).
    assert session.history and session.history[-1]["role"] == "user"
    assert "echo hello" in session.history[-1]["content"]
    # The fresh action owned the event lifecycle: it is no longer set.
    assert not session._cancel.is_set()


def test_run_shell_empty_command_is_noop(tmp_path: Path):
    session = _session(tmp_path)
    assert commands.run_shell(session, "") == []
    assert session.history == []


def test_run_shell_appends_history_on_success(tmp_path: Path):
    session = _session(tmp_path)
    events = commands.run_shell(session, "echo ok")
    assert events[0]["type"] == "tool"
    assert "exit=0" in events[0]["result"]
    assert "ok" in events[0]["result"]
    assert session.history[-1]["role"] == "user"
    assert session.history[-1]["content"].startswith("$ echo ok\n")
