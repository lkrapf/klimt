"""Tool-call parsing, barrier grouping, and execution.

Shared by the parent loop (klimt/runner.py) and the subagent loop
(klimt/agent_runner.py). Behavior contract:

- Tool calls execute in the order they were issued, except read-only
  groups, which may complete out of order. Results are still keyed by
  tool-call id so the caller can append them to history in declaration
  order.
- A barrier group is a maximal run of consecutive read-only calls.
  Mutating calls each form a solo group. Groups execute sequentially.
- Read-only groups run on a small thread pool (cap: _MAX_PARALLEL_TOOLS).
- The caller supplies `dispatch(name, args)` to actually run a tool.
  This lets the parent inject special handling for the `agent` tool
  while the subagent uses a plain wrapper around tools.run.
- Optional `on_complete(call, args, result)` is invoked synchronously
  as each call finishes, so a UI bridge can emit incremental "tool"
  events. Subagents pass None.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from . import tools

# Cap parallel read-only tool execution. The bottleneck is usually the network
# (webfetch/websearch); the threads themselves are cheap.
MAX_PARALLEL_TOOLS = 8


Dispatch = Callable[[str, Dict[str, Any]], str]
ReadOnlyPredicate = Callable[[str, Dict[str, Any]], bool]
OnComplete = Callable[["ToolCall", Dict[str, Any], str], None]


@dataclass(frozen=True)
class ToolCall:
    """One tool call as issued by the model."""
    id: str
    name: str
    raw_args: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=str(d.get("id") or ""),
            name=str(d.get("name") or ""),
            raw_args=str(d.get("args") or ""),
        )


def parse_args(raw: str) -> Dict[str, Any]:
    """Parse a tool-call arguments string, preserving the raw payload on failure."""
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}


def default_is_read_only(name: str, args: Dict[str, Any]) -> bool:  # noqa: ARG001
    return name in tools.READ_ONLY_TOOLS


def barrier_groups(
    calls: list[tuple[Dict[str, Any], ToolCall]],
    is_read_only: ReadOnlyPredicate = default_is_read_only,
) -> list[list[tuple[Dict[str, Any], ToolCall]]]:
    """Group consecutive read-only tool calls; mutating calls form solo barriers.

    Input is a list of (parsed_args, ToolCall) pairs because the predicate may
    inspect args (e.g. an `agent` call routed to a read-only subagent).
    """
    groups: list[list[tuple[Dict[str, Any], ToolCall]]] = []
    current: list[tuple[Dict[str, Any], ToolCall]] = []
    for args, call in calls:
        if is_read_only(call.name, args):
            current.append((args, call))
            continue
        if current:
            groups.append(current)
            current = []
        groups.append([(args, call)])
    if current:
        groups.append(current)
    return groups


def execute(
    calls: list[tuple[Dict[str, Any], ToolCall]],
    *,
    dispatch: Dispatch,
    cancel: threading.Event,
    is_read_only: ReadOnlyPredicate | None = None,
    on_complete: OnComplete | None = None,
    interrupted_marker: str = "[interrupted]",
) -> tuple[dict[str, str], bool]:
    """Execute calls in barrier groups. Returns (results_by_id, completed).

    `completed` is False if the cancel event tripped before/during execution.
    Already-completed results are returned regardless, and any pending calls
    in an interrupted group are recorded with `interrupted_marker`.
    """
    predicate = is_read_only or default_is_read_only
    results: dict[str, str] = {}
    completed = True

    for group in barrier_groups(calls, predicate):
        if cancel.is_set():
            for _, call in group:
                results.setdefault(call.id, interrupted_marker)
            completed = False
            continue

        if len(group) == 1:
            args, call = group[0]
            result = _safe_dispatch(dispatch, call.name, args)
            results[call.id] = result
            if on_complete:
                on_complete(call, args, result)
        else:
            _run_parallel(group, dispatch, results, on_complete)

        if cancel.is_set():
            completed = False
            # Don't break; the loop's next iteration will mark remaining
            # groups as interrupted via the cancel check at the top.

    return results, completed


def _safe_dispatch(dispatch: Dispatch, name: str, args: Dict[str, Any]) -> str:
    try:
        return dispatch(name, args)
    except Exception as e:  # noqa: BLE001 - defensive; tools.run already returns errors as strings
        return f"error: {type(e).__name__}: {e}"


def _run_parallel(
    group: list[tuple[Dict[str, Any], ToolCall]],
    dispatch: Dispatch,
    results: dict[str, str],
    on_complete: OnComplete | None,
) -> None:
    workers = min(MAX_PARALLEL_TOOLS, len(group))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_safe_dispatch, dispatch, call.name, args): (args, call)
            for args, call in group
        }
        for fut in as_completed(future_map):
            args, call = future_map[fut]
            try:
                result = fut.result()
            except Exception as e:  # noqa: BLE001
                result = f"error: {type(e).__name__}: {e}"
            results[call.id] = result
            if on_complete:
                on_complete(call, args, result)
