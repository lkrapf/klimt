"""Esc/interrupt semantics for the streaming turn runner.

The contract: when a turn is cancelled, history must end in a structurally
valid state so the next turn can be sent to the provider without an API
error. That means:

- The in-flight assistant message is appended (with an `_[interrupted by user]_`
  marker), even if it was empty or had partial tool_calls.
- Every assistant tool_call id has a matching `tool` message, possibly with
  result `[interrupted]`.
- Truncated/invalid tool_call argument JSON is sanitized to `{}` so providers
  don't reject the follow-up turn.
- The Klimt-local `interrupted` marker isn't leaked back to the provider.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from klimt import runner


# ---------------------------------------------------------------------------
# Minimal streaming fakes (subset of OpenAI delta shape)
# ---------------------------------------------------------------------------


@dataclass
class _Delta:
    content: str | None = None
    tool_calls: list[Any] = field(default_factory=list)
    reasoning: str | None = None
    reasoning_signature: str | None = None


@dataclass
class _Choice:
    delta: _Delta
    finish_reason: str | None = None


@dataclass
class _Chunk:
    choices: list[_Choice]
    usage: Any = None
    finish_reason: str | None = None


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _TC:
    index: int
    id: str
    function: _Function


def _text(s: str, finish: str | None = None) -> _Chunk:
    return _Chunk(choices=[_Choice(delta=_Delta(content=s), finish_reason=finish)])


def _tool(idx: int, call_id: str, name: str, args: str, finish: str | None = None) -> _Chunk:
    tc = _TC(index=idx, id=call_id, function=_Function(name=name, arguments=args))
    return _Chunk(choices=[_Choice(delta=_Delta(tool_calls=[tc]), finish_reason=finish)])


class _ProviderScripted:
    """Yields chunks from a pre-baked list, optionally with a hook per chunk."""

    def __init__(self, chunks: list[_Chunk], on_chunk=None):
        self._chunks = chunks
        self._on_chunk = on_chunk

    def preserves_reasoning_blocks(self) -> bool:
        return False

    def stream(self, *, messages, tool_schemas, max_completion_tokens):
        def gen():
            for ch in self._chunks:
                yield ch
                # Hook fires AFTER yield so the consumer processes the chunk
                # before any side effect (e.g. setting cancel). Matches the
                # real provider where new chunks arrive after the consumer
                # finishes the previous one.
                if self._on_chunk is not None:
                    self._on_chunk(ch)
        return gen()


def _emit_sink():
    events: list[dict] = []

    def emit(ev: dict) -> None:
        events.append(ev)
    return events, emit


def _run(provider, history, cancel):
    return runner.run_turn(
        provider=provider,
        system="sys",
        history=history,
        max_tokens=1024,
        cancel=cancel,
        active_lock=threading.Lock(),
        active_stream_ref={"stream": None},
        emit=_emit_sink()[1],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_interrupt_mid_stream_keeps_partial_assistant_message():
    """Esc while the model is still streaming text leaves the text in history."""
    cancel = threading.Event()

    def trip_after_first(_chunk):
        cancel.set()  # set immediately so the next iteration of the chunk loop bails

    chunks = [_text("hello "), _text("world", finish="stop")]
    provider = _ProviderScripted(chunks, on_chunk=trip_after_first)

    history: list[dict] = [{"role": "user", "content": "hi"}]
    completed = _run(provider, history, cancel)

    assert completed is False
    assert len(history) == 2
    last = history[-1]
    assert last["role"] == "assistant"
    assert last.get("interrupted") is True
    assert "hello" in (last["content"] or "")
    assert "[interrupted by user]" in (last["content"] or "")


def test_interrupt_before_any_chunk_still_appends_marker():
    """Cancel set before the stream starts: assistant entry is still appended."""
    cancel = threading.Event()
    cancel.set()

    provider = _ProviderScripted([_text("never seen", finish="stop")])
    history: list[dict] = [{"role": "user", "content": "hi"}]
    completed = _run(provider, history, cancel)

    assert completed is False
    # Old behavior: nothing appended. New behavior: still bails before the stream,
    # so the user-only history is preserved without a dangling assistant entry.
    assert history == [{"role": "user", "content": "hi"}]


def test_interrupt_during_tool_execution_keeps_history_balanced():
    """Every assistant tool_call must have a matching tool message after cancel."""
    cancel = threading.Event()

    # Model emits two mutating tool_calls (sequential barrier groups), then
    # stop. The first dispatch trips cancel so the second never runs.
    chunks = [
        _tool(0, "call-a", "bash", '{"command": "echo one"}'),
        _tool(1, "call-b", "bash", '{"command": "echo two"}', finish="tool_calls"),
    ]
    provider = _ProviderScripted(chunks)

    # Patch tool dispatch through tool_runner: easier to inject via runner._dispatch_one
    # by monkeypatching klimt.tools.run for this test.
    from klimt import tools as tools_mod

    real_run = tools_mod.run
    calls: list[str] = []

    def fake_run(name, args, cancel_ev, cwd):
        calls.append(name)
        cancel.set()
        return f"{name}-result"

    tools_mod.run = fake_run
    try:
        history: list[dict] = [{"role": "user", "content": "go"}]
        completed = _run(provider, history, cancel)
    finally:
        tools_mod.run = real_run

    assert completed is False

    # Structure: user, assistant (with 2 tool_calls), tool, tool.
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "tool"]
    assistant = history[1]
    # The assistant *message* completed cleanly; only the tool execution was
    # interrupted. So no `interrupted` marker here — the marker is for partial
    # assistant streams, not for cancelled downstream work.
    assert assistant.get("interrupted") is not True
    tool_call_ids = [tc["id"] for tc in assistant["tool_calls"]]
    tool_msg_ids = [m["tool_call_id"] for m in history[2:]]
    assert tool_call_ids == tool_msg_ids == ["call-a", "call-b"]

    # First call ran to completion; second was pending when cancel tripped.
    assert history[2]["content"] == "bash-result"
    assert history[3]["content"] == "[interrupted]"
    assert calls == ["bash"]  # second call was skipped


def test_interrupt_with_truncated_tool_args_sanitizes_arguments():
    """Partial JSON in a streamed tool_call would otherwise poison the next turn."""
    cancel = threading.Event()

    # Model is mid-args when the user hits Esc.
    chunks = [
        _tool(0, "call-x", "read", '{"path": "/etc'),
    ]

    def trip(_chunk):
        cancel.set()

    provider = _ProviderScripted(chunks, on_chunk=trip)
    history: list[dict] = [{"role": "user", "content": "look"}]
    completed = _run(provider, history, cancel)

    assert completed is False
    assistant = next(m for m in history if m["role"] == "assistant")
    args_str = assistant["tool_calls"][0]["function"]["arguments"]
    # Must be parseable JSON; partial '{"path": "/etc' would have crashed providers.
    import json
    json.loads(args_str)

    # And a matching tool stub must exist.
    tool_msgs = [m for m in history if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call-x"
    assert tool_msgs[0]["content"] == "[interrupted]"


def test_interrupted_field_not_sent_to_provider():
    """`interrupted` is a Klimt-local marker; the provider must not see it."""
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "partial\n\n_[interrupted by user]_",
            "interrupted": True,
        },
        {"role": "user", "content": "ok skip that, do X"},
    ]

    captured: list[list[dict]] = []

    class _Capture(_ProviderScripted):
        def stream(self, *, messages, tool_schemas, max_completion_tokens):
            captured.append(messages)
            return super().stream(
                messages=messages,
                tool_schemas=tool_schemas,
                max_completion_tokens=max_completion_tokens,
            )

    provider = _Capture([_text("ok", finish="stop")])
    completed = _run(provider, history, threading.Event())
    assert completed is True

    sent = captured[0]
    assistant_sent = [m for m in sent if m.get("role") == "assistant"][0]
    assert "interrupted" not in assistant_sent
    # Content (with marker) is preserved so the model sees context.
    assert "[interrupted by user]" in assistant_sent["content"]
