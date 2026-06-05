"""Presenter formatting tests.

These cover the cheap escaping and table-shape guarantees the UI relies on.
Not snapshot tests; just enough to catch regressions when the formatters change.
"""
from __future__ import annotations

from klimt import presenters
from klimt.agents import Agent
from klimt.model_config import ModelConfig


def test_md_escape_handles_pipes_backticks_and_backslashes() -> None:
    assert presenters.md_escape("a|b`c\\d") == "a\\|b\\`c\\\\d"


def test_md_escape_none_returns_empty() -> None:
    assert presenters.md_escape(None) == ""


def test_table_cell_escapes_pipes_only() -> None:
    assert presenters.table_cell("a|b`c") == "a\\|b`c"


def test_format_session_time_handles_garbage() -> None:
    assert presenters.format_session_time(None) == "unknown"
    assert presenters.format_session_time("not-a-number") == "unknown"
    assert presenters.format_session_time(0) == "unknown"


def test_format_session_time_renders_epoch() -> None:
    out = presenters.format_session_time(1_700_000_000)
    assert len(out) == len("YYYY-MM-DD HH:MM")
    assert out.count("-") == 2 and out.count(":") == 1


def test_skills_markdown_empty() -> None:
    assert presenters.skills_markdown([]).startswith("_no skills")


def test_skills_markdown_escapes_pipe_in_description() -> None:
    md = presenters.skills_markdown([{"name": "k", "description": "a|b"}])
    assert "| `/k` | a\\|b |" in md


def test_sessions_markdown_empty() -> None:
    assert presenters.sessions_markdown([]).startswith("_no saved sessions")


def test_sessions_markdown_renders_row() -> None:
    md = presenters.sessions_markdown([{
        "name": "demo",
        "model": "azure-4.1",
        "updated": 1_700_000_000,
        "messages": 3,
        "inputs": 2,
    }])
    assert "## Sessions" in md
    assert "| 1 | demo | azure-4.1 |" in md
    assert "/sessions resume" in md


def test_models_markdown_empty() -> None:
    assert presenters.models_markdown([], current="").startswith("_no models configured")


def test_models_markdown_marks_current() -> None:
    cfg = ModelConfig(name="m1", provider="openai", model="gpt-4.1")
    md = presenters.models_markdown([cfg], current="m1")
    assert "## Models" in md
    assert "| m1 | openai | gpt-4.1 | yes |" in md


def test_themes_markdown_empty() -> None:
    assert presenters.themes_markdown([], current="").startswith("_no themes")


def test_themes_markdown_marks_current() -> None:
    md = presenters.themes_markdown(["a", "b"], current="b")
    assert "| `a` |  |" in md
    assert "| `b` | yes |" in md


def test_agents_markdown_no_classes() -> None:
    agent = Agent(
        name="ro",
        description="reads files",
        tools=("read",),
        body="",
        source="builtin",
    )
    md = presenters.agents_markdown([agent], model_classes=[])
    assert "## Available agents" in md
    assert "| `ro` | read | read | (inherits parent) | reads files | builtin |" in md
    assert "Model classes" not in md


def test_agents_markdown_lists_classes() -> None:
    agent = Agent(
        name="ro",
        description="reads files",
        tools=("read",),
        body="",
        source="builtin",
    )
    md = presenters.agents_markdown([agent], model_classes=["opus", "haiku"])
    assert "Model classes" in md
    assert "`opus`" in md and "`haiku`" in md


def test_unknown_choice_markdown_with_choices() -> None:
    md = presenters.unknown_choice_markdown("model", "ghost", ["a", "b"])
    assert "_unknown model: `ghost`_" in md
    assert "Configured choices: `a`, `b`" in md


def test_unknown_choice_markdown_empty_hint() -> None:
    md = presenters.unknown_choice_markdown("model", "ghost", [], empty_hint="_none configured_")
    assert "Configured choices: _none configured_" in md


def test_unknown_choice_markdown_theme_uses_available_choices() -> None:
    md = presenters.unknown_choice_markdown("theme", "ghost", ["dark"])
    assert "Available choices: `dark`" in md
