"""Drive-loop tests for the goal worker in the tab bridge.

These run _goal_worker synchronously (in the calling thread) against a fake
session, so the real bridge code path executes without pywebview. This is the
layer the unit-level command tests don't reach; it caught a missing import once.
"""
from __future__ import annotations

from typing import Any

from klimt.goal import Goal
from klimt.tab_api import _SingleTabApi


class FakeSession:
    def __init__(self, verdicts: list[tuple[bool, str]]) -> None:
        self.goal: Goal | None = None
        self.history: list[dict[str, Any]] = []
        self._verdicts = list(verdicts)
        self.streamed: list[str] = []
        self.persisted = 0

    def stream(self, text: str, emit, attachments=None) -> None:
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


def test_goal_worker_uses_continuation_directive_after_first_turn() -> None:
    session = FakeSession(verdicts=[(False, "still failing"), (True, "ok")])
    session.goal = Goal(condition="tests green", max_turns=10)
    tab, _ = _tab(session)

    tab._goal_worker(session, generation=1)

    assert session.streamed[0] == "tests green"  # initial directive
    assert "still failing" in session.streamed[1]  # continuation carries reason
    assert "tests green" in session.streamed[1]
