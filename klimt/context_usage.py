"""Cheap context-window accounting.

We use a chars/4 heuristic for messages that have no usage metadata, and the
provider's reported totals where available. Result shape matches what the
status bar shows; precision is not critical.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .tool_impl import visual as _visual

# Each image block is roughly this many tokens in the chars/4 heuristic.
_IMAGE_TOKEN_ESTIMATE = 4800


def estimate_tokens(msg: Dict[str, Any]) -> int:
    """Estimate the token cost of one history message.

    chars/4 heuristic matching Pi's fallback strategy. Counts text content,
    tool-call name+arguments, and a flat per-image allowance.

    Image envelopes (pasted images or visual tool results stored as JSON
    strings) are detected and counted as a flat image allowance instead of
    their raw base64 size, which would massively over-estimate token cost.
    """
    chars = 0
    content = msg.get("content")
    if isinstance(content, str):
        envelope = _visual.parse_envelope(content)
        if envelope is not None:
            chars += _IMAGE_TOKEN_ESTIMATE * 4  # restore chars from flat estimate
        else:
            chars += len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    chars += len(block.get("text") or "")
                elif block.get("type") == "image":
                    chars += _IMAGE_TOKEN_ESTIMATE
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        chars += len(fn.get("name") or "") + len(fn.get("arguments") or "")
    return max(0, (chars + 3) // 4)


def context_tokens_from_usage(usage: Dict[str, Any]) -> int:
    """Sum of provider-reported usage fields, falling back to totalTokens."""
    return int(usage.get("totalTokens") or (
        int(usage.get("input") or 0)
        + int(usage.get("output") or 0)
        + int(usage.get("cacheRead") or 0)
        + int(usage.get("cacheWrite") or 0)
    ))


def estimate_context_tokens(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Estimate total context tokens.

    Walks history backwards looking for the most recent assistant turn that
    carries provider usage metadata. The reported usage covers everything up
    to and including that turn; messages after it are added by the chars/4
    heuristic. With no usage anywhere, every message is estimated.
    """
    last_usage_index = None
    last_usage = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("usage"):
            last_usage_index = i
            last_usage = msg["usage"]
            break

    if last_usage_index is None:
        estimated = sum(estimate_tokens(m) for m in messages)
        return {
            "tokens": estimated,
            "usageTokens": 0,
            "trailingTokens": estimated,
            "lastUsageIndex": None,
        }

    usage_tokens = context_tokens_from_usage(last_usage)
    trailing = sum(estimate_tokens(m) for m in messages[last_usage_index + 1:])
    return {
        "tokens": usage_tokens + trailing,
        "usageTokens": usage_tokens,
        "trailingTokens": trailing,
        "lastUsageIndex": last_usage_index,
    }


def context_usage(
    system: str,
    history: List[Dict[str, Any]],
    context_window: int,
) -> Dict[str, Any]:
    """Return the status-bar payload {tokens, contextWindow, percent}."""
    messages: list[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(history)
    estimate = estimate_context_tokens(messages)

    if context_window <= 0:
        return {
            "tokens": estimate["tokens"],
            "contextWindow": 0,
            "percent": None,
        }

    percent = (estimate["tokens"] / context_window) * 100
    return {
        "tokens": estimate["tokens"],
        "contextWindow": context_window,
        "percent": percent,
    }
