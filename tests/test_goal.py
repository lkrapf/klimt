"""Unit tests for goal parsing, transcript rendering, and verdict parsing."""
from __future__ import annotations

from types import SimpleNamespace

from klimt import goal as goal_mod
from klimt.goal import Goal


def test_parse_max_turns_default() -> None:
    cond, turns = goal_mod.parse_max_turns("all tests pass")
    assert cond == "all tests pass"
    assert turns == goal_mod.DEFAULT_MAX_TURNS


def test_parse_max_turns_leading_clause() -> None:
    cond, turns = goal_mod.parse_max_turns("turns=5 lint is clean")
    assert cond == "lint is clean"
    assert turns == 5


def test_parse_max_turns_bad_value_falls_back() -> None:
    cond, turns = goal_mod.parse_max_turns("turns=abc do the thing")
    assert cond == "do the thing"
    assert turns == goal_mod.DEFAULT_MAX_TURNS


def test_budget_exhausted_on_turns() -> None:
    g = Goal(condition="x", max_turns=2)
    assert g.budget_exhausted()[0] is False
    g.turns = 2
    exhausted, why = g.budget_exhausted()
    assert exhausted is True
    assert "turn budget" in why


def test_budget_exhausted_on_time() -> None:
    g = Goal(condition="x", max_seconds=0)
    exhausted, why = g.budget_exhausted()
    assert exhausted is True
    assert "time budget" in why


def test_roundtrip_dict() -> None:
    g = Goal(condition="ship it", max_turns=7, max_seconds=100)
    restored = Goal.from_dict(g.to_dict())
    assert restored is not None
    assert restored.condition == "ship it"
    assert restored.max_turns == 7
    assert restored.max_seconds == 100
    # counters reset on restore
    assert restored.turns == 0


def test_from_dict_rejects_empty() -> None:
    assert Goal.from_dict({}) is None
    assert Goal.from_dict({"condition": "   "}) is None


def test_continuation_directive_includes_reason_and_condition() -> None:
    g = Goal(condition="tests pass")
    g.last_reason = "3 tests still failing"
    directive = g.continuation_directive()
    assert "3 tests still failing" in directive
    assert "tests pass" in directive


def test_recent_transcript_truncates_and_labels() -> None:
    history = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "x" * 5000},
        {"role": "assistant", "tool_calls": [{"function": {"name": "bash"}}]},
        {"role": "tool", "content": "exit=0"},
    ]
    out = goal_mod.recent_transcript(history)
    assert "user: do it" in out
    assert "truncated" in out
    assert "[called tools: bash]" in out
    assert "tool_result: exit=0" in out


def _fake_provider(text: str):
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])
    return SimpleNamespace(complete=lambda messages, max_completion_tokens: response)


def test_evaluate_yes() -> None:
    provider = _fake_provider("VERDICT: YES\nREASON: all done")
    met, reason = goal_mod.evaluate(provider, "cond", "transcript", 4096)
    assert met is True
    assert reason == "all done"


def test_evaluate_no() -> None:
    provider = _fake_provider("VERDICT: NO\nREASON: still failing")
    met, reason = goal_mod.evaluate(provider, "cond", "transcript", 4096)
    assert met is False
    assert reason == "still failing"


def test_evaluate_malformed_defaults_to_not_met() -> None:
    provider = _fake_provider("hmm not sure")
    met, _ = goal_mod.evaluate(provider, "cond", "transcript", 4096)
    assert met is False


def test_evaluate_provider_error_is_not_met() -> None:
    def boom(messages, max_completion_tokens):
        raise RuntimeError("network down")

    provider = SimpleNamespace(complete=boom)
    met, reason = goal_mod.evaluate(provider, "cond", "transcript", 4096)
    assert met is False
    assert "evaluator error" in reason
