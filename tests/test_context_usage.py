"""Context-usage estimation tests."""
from __future__ import annotations

from klimt import context_usage


def test_estimate_tokens_text_content() -> None:
    # 8 chars / 4 = 2 tokens.
    assert context_usage.estimate_tokens({"content": "abcdefgh"}) == 2


def test_estimate_tokens_list_content_text_and_image() -> None:
    msg = {
        "content": [
            {"type": "text", "text": "abcd"},  # 1 token
            {"type": "image"},                  # 4800 chars → 1200 tokens
        ]
    }
    # (4 + 4800 + 3) // 4 = 1201
    assert context_usage.estimate_tokens(msg) == 1201


def test_estimate_tokens_includes_tool_calls() -> None:
    msg = {
        "tool_calls": [
            {"function": {"name": "read", "arguments": '{"path": "x"}'}},
        ]
    }
    # len("read") + len('{"path": "x"}') = 4 + 13 = 17 chars → 5 tokens
    assert context_usage.estimate_tokens(msg) == 5


def test_context_tokens_from_usage_prefers_total() -> None:
    assert context_usage.context_tokens_from_usage({"totalTokens": 999}) == 999


def test_context_tokens_from_usage_falls_back_to_sum() -> None:
    usage = {"input": 100, "output": 50, "cacheRead": 5, "cacheWrite": 2}
    assert context_usage.context_tokens_from_usage(usage) == 157


def test_estimate_context_tokens_no_usage_anywhere() -> None:
    messages = [
        {"role": "user", "content": "abcdefgh"},   # 2
        {"role": "assistant", "content": "ijklmnop"},  # 2
    ]
    result = context_usage.estimate_context_tokens(messages)
    assert result == {
        "tokens": 4,
        "usageTokens": 0,
        "trailingTokens": 4,
        "lastUsageIndex": None,
    }


def test_estimate_context_tokens_with_trailing() -> None:
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old reply", "usage": {"totalTokens": 1000}},
        {"role": "user", "content": "abcdefgh"},  # 2 tokens trailing
    ]
    result = context_usage.estimate_context_tokens(messages)
    assert result["lastUsageIndex"] == 1
    assert result["usageTokens"] == 1000
    assert result["trailingTokens"] == 2
    assert result["tokens"] == 1002


def test_context_usage_no_window_returns_none_percent() -> None:
    payload = context_usage.context_usage("sys", [], context_window=0)
    assert payload["contextWindow"] == 0
    assert payload["percent"] is None


def test_context_usage_computes_percent() -> None:
    # system="abcd" (1 token), no history, window=100.
    payload = context_usage.context_usage("abcd", [], context_window=100)
    assert payload["contextWindow"] == 100
    assert payload["percent"] == 1.0
