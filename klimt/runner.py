"""Streaming model/tool turn runner."""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List

from . import tools
from .api_types import Emit
from .providers import ChatProvider


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
                    {k: v for k, v in m.items() if k not in {"usage", "reasoning"}}
                    for m in history
                ],
            ],
            tool_schemas=tools.SCHEMAS,
            max_completion_tokens=max_tokens,
        )
        with active_lock:
            active_stream_ref["stream"] = stream

        content_buf: List[str] = []
        reasoning_buf: List[str] = []
        tool_calls: Dict[int, Dict[str, str]] = {}
        text_open = False
        reasoning_open = False
        usage = None

        try:
            for chunk in stream:
                if cancel.is_set():
                    break
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue

                reasoning_delta = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
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

        full_text = "".join(content_buf)
        full_reasoning = "".join(reasoning_buf)
        assistant_entry: Dict[str, Any] = {
            "role": "assistant",
            "content": full_text or None,
        }
        if full_reasoning:
            assistant_entry["reasoning"] = full_reasoning
        if usage:
            assistant_entry["usage"] = _usage_dict(usage)
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

        for _, v in sorted(tool_calls.items()):
            if cancel.is_set():
                emit({"type": "error", "message": "interrupted"})
                return False
            try:
                args = json.loads(v["args"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": v["args"]}
            result = tools.run(v["name"], args, cancel, cwd)
            emit({
                "type": "tool",
                "name": v["name"],
                "args": args,
                "result": result,
            })
            if cancel.is_set():
                emit({"type": "error", "message": "interrupted"})
                return False
            history.append({
                "role": "tool",
                "tool_call_id": v["id"],
                "content": result,
            })


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
