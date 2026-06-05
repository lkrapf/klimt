"""Streaming model/tool turn runner."""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from . import tool_runner, tools
from .api_types import Emit
from .providers import ChatProvider
from .tool_runner import ToolCall, parse_args

AgentDispatch = Callable[[str, Dict[str, Any]], str]
ReadOnlyPredicate = Callable[[str, Dict[str, Any]], bool]


_NORMAL_FINISH_REASONS = {"stop", "tool_calls", "end_turn", "tool_use", "stop_sequence"}

# Re-exported for callers (tests, api.py) that classify tool calls.
READ_ONLY_TOOLS = tools.READ_ONLY_TOOLS


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
    is_read_only: ReadOnlyPredicate | None = None,
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
        parsed: list[tuple[Dict[str, Any], ToolCall]] = [
            (parse_args(v["args"]), ToolCall.from_dict(v)) for v in ordered
        ]

        # Show every pending tool box in declaration order before doing any work.
        for args, call in parsed:
            emit({
                "type": "tool_start",
                "id": call.id,
                "name": call.name,
                "args": args,
            })

        def dispatch(name: str, args: Dict[str, Any]) -> str:
            return _dispatch_one(name, args, cancel, cwd, agent_dispatch)

        def on_complete(call: ToolCall, args: Dict[str, Any], result: str) -> None:
            emit({
                "type": "tool",
                "id": call.id,
                "name": call.name,
                "args": args,
                "result": result,
            })

        results, completed = tool_runner.execute(
            parsed,
            dispatch=dispatch,
            cancel=cancel,
            is_read_only=is_read_only,
            on_complete=on_complete,
        )
        if not completed:
            emit({"type": "error", "message": "interrupted"})
            return False

        for v in ordered:
            history.append({
                "role": "tool",
                "tool_call_id": v["id"],
                "content": results.get(v["id"], ""),
            })


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
