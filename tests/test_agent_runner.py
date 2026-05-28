"""Subagent execution: tool filtering, prompt assembly, turn loop, result format."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from klimt import agent_runner, agents as agents_mod


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeDelta:
    content: str | None = None
    tool_calls: list[Any] = field(default_factory=list)
    reasoning: str | None = None
    reasoning_signature: str | None = None


@dataclass
class _FakeChoice:
    delta: _FakeDelta
    finish_reason: str | None = None


@dataclass
class _FakeChunk:
    choices: list[_FakeChoice]
    usage: Any = None
    finish_reason: str | None = None


@dataclass
class _FakeToolCall:
    index: int
    id: str
    function: Any


@dataclass
class _FakeFunction:
    name: str
    arguments: str


def _text_chunk(text: str, finish: str = "stop") -> _FakeChunk:
    return _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content=text), finish_reason=finish)])


def _tool_chunk(call_id: str, name: str, args: str) -> _FakeChunk:
    tc = _FakeToolCall(index=0, id=call_id, function=_FakeFunction(name=name, arguments=args))
    return _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(tool_calls=[tc]), finish_reason="tool_calls")])


class _FakeProvider:
    """Quacks like ChatProvider but returns a scripted set of streams."""

    def __init__(self, scripts: list[list[_FakeChunk]]):
        self._scripts = scripts
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    def preserves_reasoning_blocks(self) -> bool:
        return False

    def stream(self, *, messages, tool_schemas, max_completion_tokens):
        self.calls.append({"messages": messages, "tool_schemas": tool_schemas})
        chunks = self._scripts[self._idx]
        self._idx += 1
        return iter(chunks)


@dataclass
class _FakeModelConfig:
    max_completion_tokens: int = 1024


# ---------------------------------------------------------------------------
# Schema/skill filtering and prompt assembly
# ---------------------------------------------------------------------------


def test_filtered_tool_schemas_empty():
    assert agent_runner.filtered_tool_schemas(()) == []


def test_filtered_tool_schemas_subset():
    schemas = agent_runner.filtered_tool_schemas(("read", "grep"))
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read", "grep"}


def test_subagent_prompt_includes_role_and_tools(tmp_path):
    agent = agents_mod.builtin_general()
    text = agent_runner.build_subagent_system_prompt(agent, str(tmp_path))
    assert "Subagent role" in text
    assert "`general`" in text
    assert "Runtime tool manifest" in text
    # Only read-mode tools surfaced.
    assert "`read`" in text
    assert "`bash`" not in text


def test_subagent_prompt_excludes_global_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent_runner.prompt_mod,
        "GLOBAL_AGENTS_PATH",
        tmp_path / "should-not-be-read.md",
    )
    (tmp_path / "should-not-be-read.md").write_text("SECRET-USER-PROFILE")
    agent = agents_mod.builtin_general()
    text = agent_runner.build_subagent_system_prompt(agent, str(tmp_path))
    assert "SECRET-USER-PROFILE" not in text


# ---------------------------------------------------------------------------
# Turn loop
# ---------------------------------------------------------------------------


def _make_inv(tmp_path: Path, agent: agents_mod.Agent) -> agent_runner.AgentInvocation:
    return agent_runner.AgentInvocation(
        agent=agent,
        task="find all python files",
        parent_model="parent-model",
        cwd=str(tmp_path),
        cancel=threading.Event(),
        transcripts_dir=tmp_path / ".transcripts",
    )


def _patch_provider_and_config(monkeypatch, provider: _FakeProvider):
    monkeypatch.setattr(agent_runner, "ChatProvider", lambda config: provider)
    monkeypatch.setattr(
        agent_runner, "resolve_model_config", lambda _name: _FakeModelConfig()
    )


def test_run_agent_single_turn_text(tmp_path, monkeypatch):
    provider = _FakeProvider(scripts=[[_text_chunk("found nothing of note", finish="stop")]])
    _patch_provider_and_config(monkeypatch, provider)
    inv = _make_inv(tmp_path, agents_mod.builtin_general())

    out = agent_runner.run_agent(inv)
    assert "status: ok" in out
    assert "found nothing of note" in out
    assert "agent: general" in out
    assert "mode: read" in out
    # Sidecar transcript written.
    transcripts = list((tmp_path / ".transcripts").glob("*.md"))
    assert len(transcripts) == 1
    assert "Subagent transcript" in transcripts[0].read_text()


def test_run_agent_uses_tool_then_finishes(tmp_path, monkeypatch):
    provider = _FakeProvider(scripts=[
        [_tool_chunk("call-1", "glob", '{"pattern": "*.py"}')],
        [_text_chunk("done", finish="stop")],
    ])
    _patch_provider_and_config(monkeypatch, provider)
    called: list[tuple[str, dict]] = []

    def fake_tool_run(name, args, cancel, cwd):
        called.append((name, args))
        return "a.py\nb.py"

    monkeypatch.setattr(agent_runner.tools_mod, "run", fake_tool_run)

    inv = _make_inv(tmp_path, agents_mod.builtin_general())
    out = agent_runner.run_agent(inv)
    assert "status: ok" in out
    assert "done" in out
    assert called == [("glob", {"pattern": "*.py"})]


def test_run_agent_max_turns(tmp_path, monkeypatch):
    # Agent keeps calling tools forever; we cap turns at 2.
    agent = agents_mod.Agent(
        name="loopy",
        description="never stops",
        tools=("read",),
        body="",
        max_turns=2,
    )
    provider = _FakeProvider(scripts=[
        [_tool_chunk("c1", "read", '{"path": "a"}')],
        [_tool_chunk("c2", "read", '{"path": "b"}')],
    ])
    _patch_provider_and_config(monkeypatch, provider)
    monkeypatch.setattr(agent_runner.tools_mod, "run", lambda *a, **k: "ok")

    inv = _make_inv(tmp_path, agent)
    out = agent_runner.run_agent(inv)
    assert "status: max_turns" in out
    assert "hit turn budget" in out


def test_run_agent_unknown_model(tmp_path, monkeypatch):
    agent = agents_mod.Agent(
        name="picky",
        description="picks a missing model",
        tools=("read",),
        body="",
        model="not-configured",
    )

    def boom(name):
        raise RuntimeError("nope")

    monkeypatch.setattr(agent_runner, "resolve_model_config", boom)
    inv = _make_inv(tmp_path, agent)
    out = agent_runner.run_agent(inv)
    assert "status: error" in out
    assert "not configured" in out


def test_run_agent_cancelled_before_start(tmp_path, monkeypatch):
    _patch_provider_and_config(monkeypatch, _FakeProvider(scripts=[]))
    inv = _make_inv(tmp_path, agents_mod.builtin_general())
    inv.cancel.set()
    out = agent_runner.run_agent(inv)
    assert "status: interrupted" in out


# ---------------------------------------------------------------------------
# Barrier grouping inside subagents
# ---------------------------------------------------------------------------


def test_subagent_barrier_groups():
    calls = [
        {"id": "1", "name": "read", "args": "{}"},
        {"id": "2", "name": "write", "args": "{}"},
        {"id": "3", "name": "grep", "args": "{}"},
        {"id": "4", "name": "glob", "args": "{}"},
    ]
    groups = agent_runner._barrier_groups(calls)
    assert [[c["name"] for c in g] for g in groups] == [
        ["read"],
        ["write"],
        ["grep", "glob"],
    ]


def test_subagent_parses_bad_json():
    assert agent_runner._parse_args("not json") == {"_raw": "not json"}
    assert agent_runner._parse_args("") == {}
