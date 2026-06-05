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
    dispatch: "tool_runner.Dispatch | None" = None,
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

        # Per-iteration state. Kept outside the try so the cancel/finalize
        # branches below can flush partial assistant content into history.
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

        interrupted = cancel.is_set()

        if reasoning_open:
            emit({"type": "reasoning_end"})
        if text_open:
            emit({"type": "text_end"})

        if not interrupted and finish_reason and finish_reason not in _NORMAL_FINISH_REASONS:
            emit({"type": "error", "message": _finish_reason_message(finish_reason, max_tokens)})

        assistant_entry = _build_assistant_entry(
            content_buf=content_buf,
            reasoning_buf=reasoning_buf,
            reasoning_signature=reasoning_signature,
            usage=usage,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            interrupted=interrupted,
        )
        # Always append, even on interrupt, so the partial work is visible in
        # the transcript and the next turn has context for "do X instead".
        history.append(assistant_entry)

        if not tool_calls:
            if interrupted:
                emit({"type": "error", "message": "interrupted"})
                return False
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

        if interrupted:
            # Cancel landed before any tool ran. Still emit and persist matching
            # `tool` messages for every tool_call id so history stays balanced.
            for args, call in parsed:
                emit({
                    "type": "tool",
                    "id": call.id,
                    "name": call.name,
                    "args": args,
                    "result": "[interrupted]",
                })
            for v in ordered:
                history.append({
                    "role": "tool",
                    "tool_call_id": v["id"],
                    "content": "[interrupted]",
                })
            emit({"type": "error", "message": "interrupted"})
            return False

        def _default_dispatch(name: str, args: Dict[str, Any]) -> str:
            return _dispatch_one(name, args, cancel, cwd, agent_dispatch)

        active_dispatch = dispatch if dispatch is not None else _default_dispatch

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
            dispatch=active_dispatch,
            cancel=cancel,
            is_read_only=is_read_only,
            on_complete=on_complete,
        )
        # Always flush tool messages, even on interrupt. tool_runner fills in
        # `[interrupted]` markers for any pending ids, so every assistant
        # tool_call has a matching `tool` message and the next turn won't blow
        # up with a dangling-tool_calls API error.
        for v in ordered:
            history.append({
                "role": "tool",
                "tool_call_id": v["id"],
                "content": results.get(v["id"], "[interrupted]"),
            })
        if not completed:
            emit({"type": "error", "message": "interrupted"})
            return False


def _build_assistant_entry(
    *,
    content_buf: list[str],
    reasoning_buf: list[str],
    reasoning_signature: str | None,
    usage: Any,
    finish_reason: str | None,
    tool_calls: Dict[int, Dict[str, str]],
    interrupted: bool,
) -> Dict[str, Any]:
    """Assemble an assistant history entry from streamed buffers.

    On interrupt, partial text gets an `_[interrupted by user]_` marker so the
    user can see where work stopped and the next-turn context is unambiguous.
    Partial tool_calls are kept as-is; parse_args has a `_raw` fallback for
    truncated JSON, and the matching tool messages will carry `[interrupted]`.
    """
    full_text = "".join(content_buf)
    if interrupted:
        marker = "_[interrupted by user]_"
        full_text = f"{full_text}\n\n{marker}" if full_text else marker
    full_reasoning = "".join(reasoning_buf)
    entry: Dict[str, Any] = {
        "role": "assistant",
        "content": full_text or None,
    }
    if full_reasoning:
        entry["reasoning"] = full_reasoning
    if reasoning_signature:
        entry["reasoning_signature"] = reasoning_signature
    if usage:
        entry["usage"] = _usage_dict(usage)
    if finish_reason:
        entry["finish_reason"] = finish_reason
    if interrupted:
        entry["interrupted"] = True
    if tool_calls:
        entry["tool_calls"] = [
            {
                "id": v["id"],
                "type": "function",
                "function": {
                    "name": v["name"],
                    # Repair truncated JSON args from an interrupted stream.
                    # Providers reject malformed JSON in tool_calls on follow-up
                    # turns even when the matching tool result is present.
                    "arguments": _sanitize_tool_args(v["args"]),
                },
            }
            for _, v in sorted(tool_calls.items())
        ]
    return entry


def _sanitize_tool_args(raw: str) -> str:
    """Return `raw` if it's valid JSON, otherwise `{}`.

    Used only when reconstructing assistant entries on interrupt. Losing the
    partial args is acceptable because the matching tool result is recorded as
    `[interrupted]` regardless — the model gets enough signal from the result.
    """
    s = (raw or "").strip() or "{}"
    try:
        import json as _json
        _json.loads(s)
        return s
    except Exception:  # noqa: BLE001
        return "{}"


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


def dispatch_one(
    name: str,
    args: Dict[str, Any],
    cancel: threading.Event,
    cwd: str | None,
    agent_dispatch: AgentDispatch | None,
) -> str:
    """Public alias for _dispatch_one; used by ChatSession._make_dispatch."""
    return _dispatch_one(name, args, cancel, cwd, agent_dispatch)


def _message_for_provider(msg: dict[str, Any], provider: ChatProvider) -> dict[str, Any]:
    # `interrupted` is a Klimt-local marker; never leak to the provider.
    out = {
        k: v for k, v in msg.items()
        if k not in {"usage", "reasoning", "reasoning_signature", "interrupted"}
    }
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
