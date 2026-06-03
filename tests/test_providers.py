from klimt.providers import _anthropic_messages, _chat_completions_sanitize_messages


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
