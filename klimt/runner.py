"""Streaming model/tool turn runner."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List

from . import tools
from .api_types import Emit
from .providers import ChatProvider

AgentDispatch = Callable[[str, Dict[str, Any]], str]


_NORMAL_FINISH_REASONS = {"stop", "tool_calls", "end_turn", "tool_use", "stop_sequence"}

# Tools that have no side effects and can be safely parallelized within a
# barrier group. Anything not in this set forces a sequential barrier.
READ_ONLY_TOOLS = frozenset({"read", "glob", "grep", "webfetch", "websearch"})

# Cap parallel read-only tool execution. The bottleneck is usually the network
# (webfetch/websearch); the threads themselves are cheap.
_MAX_PARALLEL_TOOLS = 8


def run_turn(
    *,
    provider: ChatProvider,
    system: str,
    history: list[dict[str, Any]],
    max_tokens: int,
    cancel: threading.Event,
    active_lock: threading.Lock,
    active_stream_ref: dict[str, Any],
    emit: Emit,
    cwd: str | None = None,
    tool_schemas: list[dict[str, Any]] | None = None,
    agent_dispatch: AgentDispatch | None = None,
) -> bool:
    """Run one assistant turn, including any tool-call continuations.

    Mutates `history` with assistant/tool entries. Returns True when the turn
    completed normally and False when interrupted.
    """
    while True:
        if cancel.is_set():
            emit({"type": "error", "message": "interrupted"})
            return False

        stream = provider.stream(
            messages=[
                {"role": "system", "content": system},
                *[
                    _message_for_provider(m, provider)
                    for m in history
                ],
            ],
            tool_schemas=tool_schemas if tool_schemas is not None else tools.SCHEMAS,
            max_completion_tokens=max_tokens,
        )
        with active_lock:
            active_stream_ref["stream"] = stream

        content_buf: List[str] = []
        reasoning_buf: List[str] = []
        reasoning_signature = None
        tool_calls: Dict[int, Dict[str, str]] = {}
        text_open = False
        reasoning_open = False
        usage = None
        finish_reason = None

        try:
            for chunk in stream:
                if cancel.is_set():
                    break
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                chunk_finish_reason = getattr(chunk, "finish_reason", None)
                if chunk_finish_reason:
                    finish_reason = str(chunk_finish_reason)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                choice_finish_reason = getattr(choice, "finish_reason", None)
                if choice_finish_reason:
                    finish_reason = str(choice_finish_reason)
                delta = choice.delta
                if delta is None:
                    continue

                reasoning_delta = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                delta_reasoning_signature = getattr(delta, "reasoning_signature", None)
                if delta_reasoning_signature:
                    reasoning_signature = str(delta_reasoning_signature)
                if reasoning_delta:
                    if not reasoning_open:
                        emit({"type": "reasoning_start"})
                        reasoning_open = True
                    emit({"type": "reasoning_delta", "content": reasoning_delta})
                    reasoning_buf.append(reasoning_delta)

                if delta.content:
                    if reasoning_open:
                        emit({"type": "reasoning_end"})
                        reasoning_open = False
                    if not text_open:
                        emit({"type": "text_start"})
                        text_open = True
                    emit({"type": "text_delta", "content": delta.content})
                    content_buf.append(delta.content)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = tool_calls.setdefault(
                            tc.index, {"id": "", "name": "", "args": ""}
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["args"] += tc.function.arguments
        except Exception:
            if not cancel.is_set():
                raise
        finally:
            with active_lock:
                if active_stream_ref.get("stream") is stream:
                    active_stream_ref["stream"] = None

        if cancel.is_set():
            if reasoning_open:
                emit({"type": "reasoning_end"})
            if text_open:
                emit({"type": "text_end"})
            emit({"type": "error", "message": "interrupted"})
            return False

        if reasoning_open:
            emit({"type": "reasoning_end"})

        if text_open:
            emit({"type": "text_end"})

        if finish_reason and finish_reason not in _NORMAL_FINISH_REASONS:
            emit({"type": "error", "message": _finish_reason_message(finish_reason, max_tokens)})

        full_text = "".join(content_buf)
        full_reasoning = "".join(reasoning_buf)
        assistant_entry: Dict[str, Any] = {
            "role": "assistant",
            "content": full_text or None,
        }
        if full_reasoning:
            assistant_entry["reasoning"] = full_reasoning
        if reasoning_signature:
            assistant_entry["reasoning_signature"] = reasoning_signature
        if usage:
            assistant_entry["usage"] = _usage_dict(usage)
        if finish_reason:
            assistant_entry["finish_reason"] = finish_reason
        if tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": v["id"],
                    "type": "function",
                    "function": {"name": v["name"], "arguments": v["args"]},
                }
                for _, v in sorted(tool_calls.items())
            ]
        history.append(assistant_entry)

        if not tool_calls:
            return True

        ordered = [v for _, v in sorted(tool_calls.items())]
        parsed = [(_parse_args(v["args"]), v) for v in ordered]

        # Show every pending tool box in declaration order before doing any work.
        for args, v in parsed:
            emit({
                "type": "tool_start",
                "id": v["id"],
                "name": v["name"],
                "args": args,
            })

        results: Dict[str, str] = {}
        if not _execute_tool_calls(parsed, results, emit, cancel, cwd, agent_dispatch):
            emit({"type": "error", "message": "interrupted"})
            return False

        for v in ordered:
            history.append({
                "role": "tool",
                "tool_call_id": v["id"],
                "content": results.get(v["id"], ""),
            })


def _parse_args(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}


def _execute_tool_calls(
    parsed: list[tuple[Dict[str, Any], Dict[str, str]]],
    results: Dict[str, str],
    emit: Emit,
    cancel: threading.Event,
    cwd: str | None,
    agent_dispatch: AgentDispatch | None,
) -> bool:
    """Execute tool calls in barrier groups. Returns False if interrupted."""
    for group in _barrier_groups(parsed):
        if cancel.is_set():
            return False
        if len(group) == 1:
            args, v = group[0]
            results[v["id"]] = _dispatch_one(v["name"], args, cancel, cwd, agent_dispatch)
            emit({
                "type": "tool",
                "id": v["id"],
                "name": v["name"],
                "args": args,
                "result": results[v["id"]],
            })
        else:
            _run_parallel(group, results, emit, cancel, cwd, agent_dispatch)
        if cancel.is_set():
            return False
    return True


def _dispatch_one(
    name: str,
    args: Dict[str, Any],
    cancel: threading.Event,
    cwd: str | None,
    agent_dispatch: AgentDispatch | None,
) -> str:
    if name == "agent":
        if not agent_dispatch:
            return "error: no subagents configured"
        try:
            return agent_dispatch(name, args)
        except Exception as e:  # noqa: BLE001
            return f"error: {type(e).__name__}: {e}"
    return tools.run(name, args, cancel, cwd)


def _run_parallel(
    group: list[tuple[Dict[str, Any], Dict[str, str]]],
    results: Dict[str, str],
    emit: Emit,
    cancel: threading.Event,
    cwd: str | None,
    agent_dispatch: AgentDispatch | None,
) -> None:
    """Run a read-only group on a thread pool. Emits completions as they land."""
    workers = min(_MAX_PARALLEL_TOOLS, len(group))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_item = {
            pool.submit(_dispatch_one, v["name"], args, cancel, cwd, agent_dispatch): (args, v)
            for args, v in group
        }
        for future in as_completed(future_to_item):
            args, v = future_to_item[future]
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001 - defensive; tools.run already returns errors as strings
                result = f"error: {type(e).__name__}: {e}"
            results[v["id"]] = result
            emit({
                "type": "tool",
                "id": v["id"],
                "name": v["name"],
                "args": args,
                "result": result,
            })


def _barrier_groups(
    parsed: list[tuple[Dict[str, Any], Dict[str, str]]],
) -> list[list[tuple[Dict[str, Any], Dict[str, str]]]]:
    """Group consecutive read-only tool calls; mutating calls form solo barriers."""
    groups: list[list[tuple[Dict[str, Any], Dict[str, str]]]] = []
    current: list[tuple[Dict[str, Any], Dict[str, str]]] = []
    for args, v in parsed:
        if v["name"] in READ_ONLY_TOOLS:
            current.append((args, v))
            continue
        if current:
            groups.append(current)
            current = []
        groups.append([(args, v)])
    if current:
        groups.append(current)
    return groups


def _message_for_provider(msg: dict[str, Any], provider: ChatProvider) -> dict[str, Any]:
    out = {k: v for k, v in msg.items() if k not in {"usage", "reasoning", "reasoning_signature"}}
    if provider.preserves_reasoning_blocks() and msg.get("reasoning"):
        out["reasoning"] = msg["reasoning"]
        if msg.get("reasoning_signature"):
            out["reasoning_signature"] = msg["reasoning_signature"]
    return out


def _finish_reason_message(finish_reason: str, max_tokens: int) -> str:
    if finish_reason in {"length", "max_tokens"}:
        return (
            f"model stopped because the max completion token limit was reached "
            f"({max_tokens}); say continue or increase max_completion_tokens"
        )
    return f"model stopped with finish reason: {finish_reason}"


def _usage_dict(usage: Any) -> Dict[str, int]:
    """Normalize OpenAI usage into the small shape we persist."""
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details else 0
    return {
        "input": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output": int(getattr(usage, "completion_tokens", 0) or 0),
        "cacheRead": int(cached or 0),
        "cacheWrite": 0,
        "totalTokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
