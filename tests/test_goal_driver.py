"""Drive-loop tests for the goal worker in the tab bridge.

These run _goal_worker synchronously (in the calling thread) against a fake
session, so the real bridge code path executes without pywebview. This is the
layer the unit-level command tests don't reach; it caught a missing import once.
"""
from __future__ import annotations

from typing import Any

import threading

from klimt import goal as goal_mod
from klimt.goal import Goal
from klimt.tab_api import _SingleTabApi


class FakeSession:
    def __init__(self, verdicts: list[tuple[bool, str]], fail_turns: set[int] | None = None) -> None:
        self.goal: Goal | None = None
        self.history: list[dict[str, Any]] = []
        self._verdicts = list(verdicts)
        self.streamed: list[str] = []
        self.persisted = 0
        self._fail_turns = set(fail_turns or ())
        self._attempt = 0
        self._cancel = threading.Event()

    def stream(self, text: str, emit, attachments=None) -> None:
        self._attempt += 1
        # stream appends the directive as a user message before running, mirroring
        # the real ChatSession; a failing turn raises after that append.
        self.history.append({"role": "user", "content": text})
        if self._attempt in self._fail_turns:
            raise ConnectionResetError("[Errno 54] Connection reset by peer")
        self.streamed.append(text)
        self.history.append({"role": "assistant", "content": f"worked on: {text}"})

    def evaluate_goal(self) -> tuple[bool, str]:
        if self._verdicts:
            return self._verdicts.pop(0)
        return False, "no verdict left"

    def persist(self) -> None:
        self.persisted += 1

    def remember_input(self, text: str) -> None:
        pass


def _tab(session: FakeSession) -> tuple[_SingleTabApi, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    tab = _SingleTabApi(session, "tab-1", lambda tab_id, event: events.append(event))
    # generation 1 is what _start_goal would have set; align the worker with it.
    tab._generation = 1
    tab._busy = True
    return tab, events


def _texts(events: list[dict[str, Any]]) -> list[str]:
    return [e.get("content", "") for e in events if e.get("type") == "text"]


def test_goal_worker_stops_when_condition_met() -> None:
    session = FakeSession(verdicts=[(False, "not yet"), (True, "done")])
    session.goal = Goal(condition="finish it", max_turns=10)
    tab, events = _tab(session)

    tab._goal_worker(session, generation=1)

    assert len(session.streamed) == 2
    assert session.goal is None  # cleared on success
    assert any("Goal achieved" in t for t in _texts(events))


def test_goal_worker_respects_turn_budget() -> None:
    session = FakeSession(verdicts=[(False, "nope")] * 5)
    session.goal = Goal(condition="loop forever", max_turns=3)
    tab, events = _tab(session)

    tab._goal_worker(session, generation=1)

    # Runs exactly max_turns then hard-stops; goal stays active for the user.
    assert len(session.streamed) == 3
    assert session.goal is not None
    assert any("goal stopped" in t and "turn budget" in t for t in _texts(events))


def test_goal_worker_breaks_on_generation_change() -> None:
    session = FakeSession(verdicts=[(False, "nope")] * 5)
    session.goal = Goal(condition="x", max_turns=10)
    tab, events = _tab(session)
    tab._generation = 2  # simulate an interrupt bumping generation

    tab._goal_worker(session, generation=1)

    # Stale worker must not drive any turns.
    assert session.streamed == []


def test_goal_worker_retries_transient_error_and_continues(monkeypatch) -> None:
    monkeypatch.setattr(goal_mod, "RETRY_BACKOFF_SECONDS", (0, 0, 0, 0))
    # turn 1 raises, retry (turn 2) succeeds, evaluator then says met.
    session = FakeSession(verdicts=[(True, "done")], fail_turns={1})
    session.goal = Goal(condition="survive a blip", max_turns=10)
    tab, events = _tab(session)

    tab._goal_worker(session, generation=1)

    assert session.streamed == ["survive a blip"]  # one successful turn
    assert session.goal is None  # reached completion despite the error
    assert any("Retry 1/" in t for t in _texts(events))
    # failed turn's dangling user message was rolled back; history has the
    # retry's user + assistant only.
    assert session.history == [
        {"role": "user", "content": "survive a blip"},
        {"role": "assistant", "content": "worked on: survive a blip"},
    ]


def test_goal_worker_gives_up_after_consecutive_errors(monkeypatch) -> None:
    monkeypatch.setattr(goal_mod, "RETRY_BACKOFF_SECONDS", (0, 0, 0, 0))
    monkeypatch.setattr(goal_mod, "MAX_CONSECUTIVE_ERRORS", 3)
    # every turn fails.
    session = FakeSession(verdicts=[], fail_turns={1, 2, 3, 4, 5})
    session.goal = Goal(condition="never works", max_turns=10)
    tab, events = _tab(session)

    tab._goal_worker(session, generation=1)

    assert session.streamed == []
    assert session.goal is not None  # stays active for the user to retry
    assert any("goal paused after 3 consecutive errors" in t for t in _texts(events))


def test_goal_worker_error_counter_resets_on_good_turn(monkeypatch) -> None:
    monkeypatch.setattr(goal_mod, "RETRY_BACKOFF_SECONDS", (0, 0, 0, 0))
    monkeypatch.setattr(goal_mod, "MAX_CONSECUTIVE_ERRORS", 2)
    # fail, ok, fail, ok(met): with cap=2, two isolated failures would trip the
    # cap if the counter did NOT reset on the good turn between them.
    session = FakeSession(verdicts=[(False, "keep going"), (True, "done")], fail_turns={1, 3})
    session.goal = Goal(condition="flaky net", max_turns=10)
    tab, events = _tab(session)

    tab._goal_worker(session, generation=1)

    assert len(session.streamed) == 2  # two good turns
    assert session.goal is None
    assert not any("paused" in t for t in _texts(events))


def test_goal_worker_uses_continuation_directive_after_first_turn() -> None:
    session = FakeSession(verdicts=[(False, "still failing"), (True, "ok")])
    session.goal = Goal(condition="tests green", max_turns=10)
    tab, _ = _tab(session)

    tab._goal_worker(session, generation=1)

    assert session.streamed[0] == "tests green"  # initial directive
    assert "still failing" in session.streamed[1]  # continuation carries reason
    assert "tests green" in session.streamed[1]
