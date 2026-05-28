"""Barrier grouping and parallel execution of tool calls."""
from __future__ import annotations

import threading
import time

from klimt import runner


def _calls(*names: str):
    return [({}, {"id": f"call-{i}", "name": n, "args": "{}"}) for i, n in enumerate(names)]


def test_barrier_groups_consecutive_read_only():
    groups = runner._barrier_groups(_calls("read", "read", "websearch"))
    assert len(groups) == 1
    assert [v["name"] for _, v in groups[0]] == ["read", "read", "websearch"]


def test_barrier_groups_split_on_mutating():
    groups = runner._barrier_groups(_calls("read", "edit", "read", "read"))
    assert [[v["name"] for _, v in g] for g in groups] == [
        ["read"],
        ["edit"],
        ["read", "read"],
    ]


def test_barrier_groups_solo_mutating():
    groups = runner._barrier_groups(_calls("bash", "write", "edit"))
    assert [[v["name"] for _, v in g] for g in groups] == [["bash"], ["write"], ["edit"]]


def test_barrier_groups_empty():
    assert runner._barrier_groups([]) == []


def test_barrier_groups_custom_predicate_groups_agents():
    """A predicate can mark certain agent calls as read-only so they parallelize."""
    parsed = [
        ({"name": "researcher"}, {"id": "1", "name": "agent", "args": ""}),
        ({"name": "researcher"}, {"id": "2", "name": "agent", "args": ""}),
        ({"name": "refactorer"}, {"id": "3", "name": "agent", "args": ""}),
        ({"name": "researcher"}, {"id": "4", "name": "agent", "args": ""}),
    ]

    def predicate(name, args):
        return name == "agent" and args.get("name") == "researcher"

    groups = runner._barrier_groups(parsed, predicate)
    assert [[v["id"] for _, v in g] for g in groups] == [
        ["1", "2"],
        ["3"],
        ["4"],
    ]


def test_parse_args_valid():
    assert runner._parse_args('{"a": 1}') == {"a": 1}


def test_parse_args_invalid_keeps_raw():
    assert runner._parse_args("not json") == {"_raw": "not json"}


def test_parse_args_empty_string():
    assert runner._parse_args("") == {}


def test_execute_tool_calls_preserves_order(monkeypatch):
    """Results map by id even when reads finish out of order."""

    started = threading.Event()

    def fake_run(name, args, cancel, cwd):
        if args.get("slow"):
            started.set()
            time.sleep(0.05)
        return f"{name}:{args.get('tag', '')}"

    monkeypatch.setattr(runner.tools, "run", fake_run)

    parsed = [
        ({"slow": True, "tag": "a"}, {"id": "1", "name": "read", "args": ""}),
        ({"tag": "b"}, {"id": "2", "name": "read", "args": ""}),
        ({"tag": "c"}, {"id": "3", "name": "read", "args": ""}),
    ]
    results: dict[str, str] = {}
    events: list[dict] = []
    ok = runner._execute_tool_calls(
        parsed, results, events.append, threading.Event(), None, None
    )
    assert ok
    assert results == {"1": "read:a", "2": "read:b", "3": "read:c"}
    # Completion events may arrive in any order; the id correlates them.
    completed_ids = [e["id"] for e in events if e["type"] == "tool"]
    assert sorted(completed_ids) == ["1", "2", "3"]


def test_execute_tool_calls_sequential_for_mutating(monkeypatch):
    order: list[str] = []

    def fake_run(name, args, cancel, cwd):
        order.append(name)
        return name

    monkeypatch.setattr(runner.tools, "run", fake_run)

    parsed = [
        ({}, {"id": "1", "name": "read", "args": ""}),
        ({}, {"id": "2", "name": "edit", "args": ""}),
        ({}, {"id": "3", "name": "read", "args": ""}),
    ]
    results: dict[str, str] = {}
    events: list[dict] = []
    runner._execute_tool_calls(parsed, results, events.append, threading.Event(), None, None)
    # edit must complete after the first read and before the trailing read.
    assert order == ["read", "edit", "read"]
