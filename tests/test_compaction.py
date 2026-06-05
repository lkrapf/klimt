"""Compaction policy tests.

`compact_history` is provider-agnostic; we inject a fake compact_text callback
so the tests don't need a real model.
"""
from __future__ import annotations

import pytest

from klimt import compaction


def _fake_compactor(label: str):
    calls: list[str] = []

    def compact_text(text: str) -> str:
        calls.append(text)
        return f"[{label} summary #{len(calls)}]"

    return compact_text, calls


def test_compact_history_nothing_to_compact_when_keep_recent_covers_all() -> None:
    history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    compact_text, calls = _fake_compactor("x")
    result = compaction.compact_history(history, compact_text, keep_recent=10)
    assert result.summary == "nothing to compact"
    assert calls == []
    # History returned unchanged (or an equivalent copy).
    assert result.history == history


def test_compact_history_replaces_old_with_note_and_keeps_recent() -> None:
    history = [
        {"role": "user", "content": "old-1"},
        {"role": "assistant", "content": "old-2"},
        {"role": "user", "content": "recent-1"},
        {"role": "assistant", "content": "recent-2"},
    ]
    compact_text, _calls = _fake_compactor("x")
    result = compaction.compact_history(history, compact_text, keep_recent=2)
    assert "compacted 2 messages" in result.summary
    assert len(result.history) == 3  # note + 2 recent
    note = result.history[0]
    assert note["role"] == "user"
    assert note["content"].startswith(compaction.COMPACTED_NOTE_PREFIX)
    assert result.history[1:] == history[2:]


def test_compact_history_moves_cutoff_left_to_preserve_tool_pair() -> None:
    """Cutoff must not split an assistant tool-call from its tool-result followups."""
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "result"},
        {"role": "assistant", "content": "answer"},
    ]
    compact_text, _calls = _fake_compactor("x")
    # keep_recent=2 would naively split the (assistant tool_calls, tool result)
    # pair. The cutoff should slide left so the entire tool-call pair becomes
    # "recent" together.
    result = compaction.compact_history(history, compact_text, keep_recent=2)
    # Expect note + (assistant tool_calls, tool, assistant) preserved as recent.
    recent_roles = [m["role"] for m in result.history[1:]]
    assert recent_roles == ["assistant", "tool", "assistant"]


def test_compact_history_drops_usage_metadata_from_recent() -> None:
    history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "recent", "usage": {"totalTokens": 999}},
    ]
    compact_text, _ = _fake_compactor("x")
    result = compaction.compact_history(history, compact_text, keep_recent=1)
    recent = result.history[1]
    assert "usage" not in recent


def test_compact_history_chunked_then_merged() -> None:
    # Set a small chunk budget so each old message ends up in its own chunk.
    history = [
        {"role": "user", "content": "x" * 200},
        {"role": "assistant", "content": "y" * 200},
        {"role": "user", "content": "recent"},
    ]
    compact_text, calls = _fake_compactor("c")
    result = compaction.compact_history(
        history, compact_text, keep_recent=1, chunk_budget=64,
    )
    # 2 chunk summaries + 1 merge call = 3 total.
    assert len(calls) == 3
    assert "compacted 2 messages" in result.summary


def test_chunk_messages_packs_under_budget() -> None:
    msgs = [
        {"role": "user", "content": "abcd"},   # 1 token
        {"role": "user", "content": "efgh"},   # 1 token
        {"role": "user", "content": "ijkl"},   # 1 token
    ]
    # Budget 2 → first two go together, third spills.
    chunks = compaction.chunk_messages(msgs, max_tokens=2)
    assert [[m["content"] for m in c] for c in chunks] == [
        ["abcd", "efgh"],
        ["ijkl"],
    ]


def test_chunk_messages_always_includes_oversized_singleton() -> None:
    # A single message larger than the budget must still get its own chunk.
    msgs = [{"role": "user", "content": "x" * 4000}]
    chunks = compaction.chunk_messages(msgs, max_tokens=10)
    assert len(chunks) == 1
    assert chunks[0][0]["content"].startswith("x")


def test_compact_history_respects_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no chunk_budget is passed, KLIMT_COMPACTION_CHUNK_TOKENS controls it."""
    monkeypatch.setenv("KLIMT_COMPACTION_CHUNK_TOKENS", "1")
    history = [
        {"role": "user", "content": "abcd"},
        {"role": "assistant", "content": "efgh"},
        {"role": "user", "content": "recent"},
    ]
    compact_text, calls = _fake_compactor("c")
    compaction.compact_history(history, compact_text, keep_recent=1)
    # With chunk budget = 1 token, the two old messages become two chunks
    # plus a merge call.
    assert len(calls) == 3
