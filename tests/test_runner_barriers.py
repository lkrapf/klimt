"""Barrier grouping and parallel execution of tool calls.

These were the parent-runner tests; the underlying logic now lives in
klimt.tool_runner and is shared with the subagent loop.
"""
from __future__ import annotations

import threading
import time

from klimt import tool_runner
from klimt.tool_runner import ToolCall


def _calls(*names: str):
    return [({}, ToolCall(id=f"call-{i}", name=n, raw_args="{}")) for i, n in enumerate(names)]


def test_barrier_groups_consecutive_read_only():
    groups = tool_runner.barrier_groups(_calls("read", "read", "websearch"))
    assert len(groups) == 1
    assert [c.name for _, c in groups[0]] == ["read", "read", "websearch"]


def test_barrier_groups_split_on_mutating():
    groups = tool_runner.barrier_groups(_calls("read", "edit", "read", "read"))
    assert [[c.name for _, c in g] for g in groups] == [
        ["read"],
        ["edit"],
        ["read", "read"],
    ]


def test_barrier_groups_solo_mutating():
    groups = tool_runner.barrier_groups(_calls("bash", "write", "edit"))
    assert [[c.name for _, c in g] for g in groups] == [["bash"], ["write"], ["edit"]]


def test_barrier_groups_empty():
    assert tool_runner.barrier_groups([]) == []


def test_barrier_groups_custom_predicate_groups_agents():
    """A predicate can mark certain agent calls as read-only so they parallelize."""
    parsed = [
        ({"name": "researcher"}, ToolCall(id="1", name="agent")),
        ({"name": "researcher"}, ToolCall(id="2", name="agent")),
        ({"name": "refactorer"}, ToolCall(id="3", name="agent")),
        ({"name": "researcher"}, ToolCall(id="4", name="agent")),
    ]

    def predicate(name, args):
        return name == "agent" and args.get("name") == "researcher"

    groups = tool_runner.barrier_groups(parsed, predicate)
    assert [[c.id for _, c in g] for g in groups] == [
        ["1", "2"],
        ["3"],
        ["4"],
    ]


def test_parse_args_valid():
    assert tool_runner.parse_args('{"a": 1}') == {"a": 1}


def test_parse_args_invalid_keeps_raw():
    assert tool_runner.parse_args("not json") == {"_raw": "not json"}


def test_parse_args_empty_string():
    assert tool_runner.parse_args("") == {}


def _record_complete(events: list[dict]):
    def on_complete(call, args, result):
        events.append({"type": "tool", "id": call.id, "name": call.name, "args": args, "result": result})
    return on_complete


def test_execute_preserves_results_when_reads_finish_out_of_order():
    """Results map by id even when reads finish out of order."""
    started = threading.Event()

    def dispatch(name, args):
        if args.get("slow"):
            started.set()
            time.sleep(0.05)
        return f"{name}:{args.get('tag', '')}"

    parsed = [
        ({"slow": True, "tag": "a"}, ToolCall(id="1", name="read")),
        ({"tag": "b"}, ToolCall(id="2", name="read")),
        ({"tag": "c"}, ToolCall(id="3", name="read")),
    ]
    events: list[dict] = []
    results, completed = tool_runner.execute(
        parsed,
        dispatch=dispatch,
        cancel=threading.Event(),
        on_complete=_record_complete(events),
    )
    assert completed
    assert results == {"1": "read:a", "2": "read:b", "3": "read:c"}
    # Completion events may arrive in any order; the id correlates them.
    assert sorted(e["id"] for e in events) == ["1", "2", "3"]


def test_execute_sequential_for_mutating():
    order: list[str] = []

    def dispatch(name, args):
        order.append(name)
        return name

    parsed = [
        ({}, ToolCall(id="1", name="read")),
        ({}, ToolCall(id="2", name="edit")),
        ({}, ToolCall(id="3", name="read")),
    ]
    tool_runner.execute(
        parsed,
        dispatch=dispatch,
        cancel=threading.Event(),
    )
    # edit must complete after the first read and before the trailing read.
    assert order == ["read", "edit", "read"]


def test_execute_marks_pending_calls_interrupted_when_cancelled_mid_run():
    cancel = threading.Event()
    order: list[str] = []

    def dispatch(name, args):
        order.append(name)
        if name == "edit":
            cancel.set()
        return name

    parsed = [
        ({}, ToolCall(id="1", name="read")),
        ({}, ToolCall(id="2", name="edit")),
        ({}, ToolCall(id="3", name="read")),
    ]
    results, completed = tool_runner.execute(
        parsed,
        dispatch=dispatch,
        cancel=cancel,
    )
    assert not completed
    assert results["1"] == "read"
    assert results["2"] == "edit"
    assert results["3"] == "[interrupted]"
