from klimt.model_config import ModelConfig
from klimt.providers import (
    _anthropic_messages,
    _anthropic_payload,
    _anthropic_usage,
    _chat_completions_sanitize_messages,
)


def test_chat_completions_sanitizer_removes_ollama_tool_call_null_content_without_mutation():
    original = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
    }

    sanitized = _chat_completions_sanitize_messages("ollama", [original])

    assert "content" not in sanitized[0]
    assert original["content"] is None


def test_chat_completions_sanitizer_replaces_other_null_assistant_content_without_mutation():
    original = {"role": "assistant", "content": None}

    sanitized = _chat_completions_sanitize_messages("openai", [original])

    assert sanitized == [{"role": "assistant", "content": ""}]
    assert original["content"] is None


def test_anthropic_messages_omits_unsigned_reasoning_when_switching_from_non_anthropic_model():
    messages = [{"role": "assistant", "content": "answer", "reasoning": "private chain"}]

    converted = _anthropic_messages(messages)

    assert converted == [{"role": "assistant", "content": [{"type": "text", "text": "answer"}]}]


def test_anthropic_messages_keeps_signed_reasoning():
    messages = [{
        "role": "assistant",
        "content": "answer",
        "reasoning": "private chain",
        "reasoning_signature": "sig",
    }]

    converted = _anthropic_messages(messages)

    assert converted == [{
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "private chain", "signature": "sig"},
            {"type": "text", "text": "answer"},
        ],
    }]


def test_anthropic_payload_stamps_cache_breakpoints_by_default():
    cfg = ModelConfig(name="sonnet", provider="anthropic", model="claude-sonnet-4")
    payload = _anthropic_payload(
        cfg,
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        [{
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read",
                "parameters": {"type": "object"},
            },
        }],
        100,
        stream=False,
    )

    # Last system block tagged for caching (system has a Claude-Code preamble).
    assert payload["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # Last tool entry tagged.
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    # Last block of the last message tagged.
    last_block = payload["messages"][-1]["content"][-1]
    assert last_block["cache_control"] == {"type": "ephemeral"}


def test_anthropic_payload_omits_cache_breakpoints_when_disabled():
    cfg = ModelConfig(
        name="sonnet",
        provider="anthropic",
        model="claude-sonnet-4",
        cache_prompts=False,
    )
    payload = _anthropic_payload(
        cfg,
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        [],
        100,
        stream=False,
    )

    for block in payload["system"]:
        assert "cache_control" not in block
    for msg in payload["messages"]:
        for block in msg["content"]:
            assert "cache_control" not in block


def test_anthropic_usage_splits_cache_read_and_write():
    usage = _anthropic_usage({
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 50,
    })

    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 20
    assert usage.prompt_tokens_details.cached_tokens == 100
    assert usage.prompt_tokens_details.cache_write_tokens == 50
    # totalTokens needs to include cache so the status bar reflects real cost.
    assert usage.total_tokens == 180
