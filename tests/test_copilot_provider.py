import base64
import json
from types import SimpleNamespace

import pytest

from klimt.api import ChatSession
from klimt.model_config import ModelConfig
from klimt.providers import (
    ChatProvider,
    _copilot_responses_complete,
    _copilot_responses_items,
    _copilot_responses_kwargs,
    _copilot_responses_tools,
    _copilot_responses_usage,
    _CopilotResponsesStream,
)


def _fake_openai_client():
    class _FakeCompletions:
        def create(self, **kwargs):
            return "ok"

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    return _FakeClient()


def test_copilot_oauth_provider_defers_client_creation_without_api_key_env():
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")

    provider = ChatProvider(cfg)

    assert provider._copilot_oauth is True
    assert provider.client is None


def test_copilot_static_api_key_builds_client_with_headers_and_base_url(monkeypatch):
    monkeypatch.setenv("COPILOT_TOKEN", "static-token")
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1", api_key_env="COPILOT_TOKEN")

    provider = ChatProvider(cfg)

    assert provider._copilot_oauth is False
    assert provider.client is not None
    assert str(provider.client.base_url).rstrip("/") == "https://api.githubcopilot.com"
    assert provider.client.default_headers["Copilot-Integration-Id"] == "vscode-chat"


def test_copilot_oauth_complete_fetches_fresh_token_per_call(monkeypatch):
    calls = []

    def fake_access_token(on_device_code=None):
        calls.append(1)
        return "fresh-token"

    monkeypatch.setattr("klimt.providers.copilot_oauth.access_token", fake_access_token)

    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")
    provider = ChatProvider(cfg)

    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return "ok"

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(ChatProvider, "_make_client", staticmethod(lambda config, api_key: _FakeClient()))

    result = provider.complete([{"role": "user", "content": "hi"}], 100)

    assert result == "ok"
    assert calls == [1]
    assert captured["model"] == "gpt-4.1"
    assert captured["max_completion_tokens"] == 100


def test_copilot_oauth_emits_device_code_prompt_into_chat(monkeypatch):
    captured_on_device_code = {}

    def fake_access_token(on_device_code=None):
        captured_on_device_code["cb"] = on_device_code
        if on_device_code is not None:
            on_device_code("https://github.com/login/device", "ABCD-1234")
        return "fresh-token"

    monkeypatch.setattr("klimt.providers.copilot_oauth.access_token", fake_access_token)
    monkeypatch.setattr(ChatProvider, "_make_client", staticmethod(lambda config, api_key: _fake_openai_client()))

    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")
    provider = ChatProvider(cfg)

    events = []
    provider.complete([{"role": "user", "content": "hi"}], 100, emit=events.append)

    assert captured_on_device_code["cb"] is not None
    assert len(events) == 1
    assert events[0]["type"] == "text"
    assert "ABCD-1234" in events[0]["content"]
    assert "https://github.com/login/device" in events[0]["content"]


def test_copilot_oauth_without_emit_passes_no_device_code_callback(monkeypatch):
    seen = {}

    def fake_access_token(on_device_code=None):
        seen["cb"] = on_device_code
        return "fresh-token"

    monkeypatch.setattr("klimt.providers.copilot_oauth.access_token", fake_access_token)
    monkeypatch.setattr(ChatProvider, "_make_client", staticmethod(lambda config, api_key: _fake_openai_client()))

    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")
    provider = ChatProvider(cfg)

    provider.complete([{"role": "user", "content": "hi"}], 100)

    assert seen["cb"] is None


def _envelope():
    return {
        "_klimt_image": True,
        "media_type": "image/png",
        "data": base64.b64encode(b"fake-bytes").decode("ascii"),
        "path": "/tmp/shot.png",
        "bytes": 10,
    }


def test_copilot_responses_tools_flattens_function_schema():
    schemas = [{
        "type": "function",
        "function": {
            "name": "multiply",
            "description": "Multiply two integers",
            "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}},
        },
    }]

    tools = _copilot_responses_tools(schemas)

    assert tools == [{
        "type": "function",
        "name": "multiply",
        "description": "Multiply two integers",
        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}},
    }]


def test_copilot_responses_items_user_text():
    assert _copilot_responses_items({"role": "user", "content": "hi"}) == [
        {"role": "user", "content": "hi"}
    ]


def test_copilot_responses_items_user_image_envelope():
    items = _copilot_responses_items({"role": "user", "content": json.dumps(_envelope())})

    assert len(items) == 1
    assert items[0]["role"] == "user"
    parts = items[0]["content"]
    assert parts[0]["type"] == "input_text"
    assert "shot.png" in parts[0]["text"]
    assert parts[1] == {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{base64.b64encode(b'fake-bytes').decode('ascii')}",
    }


def test_copilot_responses_items_assistant_text_and_tool_calls():
    msg = {
        "role": "assistant",
        "content": "checking",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "multiply", "arguments": '{"a":1,"b":2}'},
        }],
    }

    items = _copilot_responses_items(msg)

    assert items == [
        {"role": "assistant", "content": "checking"},
        {"type": "function_call", "call_id": "call_1", "name": "multiply", "arguments": '{"a":1,"b":2}'},
    ]


def test_copilot_responses_items_tool_result():
    items = _copilot_responses_items({"role": "tool", "tool_call_id": "call_1", "content": "42"})

    assert items == [{"type": "function_call_output", "call_id": "call_1", "output": "42"}]


def test_copilot_responses_items_tool_result_with_image_envelope():
    items = _copilot_responses_items({
        "role": "tool",
        "tool_call_id": "call_1",
        "content": json.dumps(_envelope()),
    })

    assert len(items) == 2
    assert items[0]["type"] == "function_call_output"
    assert items[0]["call_id"] == "call_1"
    assert items[1]["role"] == "user"


def test_copilot_responses_kwargs_maps_system_to_instructions_and_tokens():
    messages = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    kwargs = _copilot_responses_kwargs(cfg, messages, None, 123)

    assert kwargs["model"] == "gpt-5.6-sol"
    assert kwargs["instructions"] == "be terse"
    assert kwargs["input"] == [{"role": "user", "content": "hi"}]
    assert kwargs["max_output_tokens"] == 123
    assert "tools" not in kwargs


def test_copilot_responses_usage_maps_fields():
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=SimpleNamespace(cached_tokens=2, cache_write_tokens=1),
    )

    mapped = _copilot_responses_usage(usage)

    assert mapped.prompt_tokens == 10
    assert mapped.completion_tokens == 5
    assert mapped.total_tokens == 15
    assert mapped.prompt_tokens_details.cached_tokens == 2
    assert mapped.prompt_tokens_details.cache_write_tokens == 1


class _FakeResponsesClient:
    def __init__(self, events=None, response=None):
        self._events = events or []
        self._response = response
        self.captured_kwargs = None

    class _Responses:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.captured_kwargs = kwargs
            if kwargs.get("stream"):
                return iter(self._outer._events)
            return self._outer._response

    @property
    def responses(self):
        return self._Responses(self)


def test_copilot_responses_complete_returns_text_and_usage():
    response = SimpleNamespace(
        status="completed",
        output_text="the answer is 42",
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=2, total_tokens=3,
            input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
        ),
    )
    client = _FakeResponsesClient(response=response)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    result = _copilot_responses_complete(client, cfg, [{"role": "user", "content": "hi"}], 50)

    assert result.choices[0].message.content == "the answer is 42"
    assert result.usage.total_tokens == 3


@pytest.mark.parametrize("status,reason", [
    ("incomplete", "max_output_tokens"),
    ("failed", "model unavailable"),
])
def test_copilot_responses_complete_rejects_noncompleted_result(status, reason):
    response = SimpleNamespace(
        status=status,
        output_text="partial summary",
        incomplete_details=SimpleNamespace(reason=reason) if status == "incomplete" else None,
        error=SimpleNamespace(message=reason) if status == "failed" else None,
    )
    client = _FakeResponsesClient(response=response)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    with pytest.raises(RuntimeError, match=reason):
        _copilot_responses_complete(client, cfg, [{"role": "user", "content": "hi"}], 50)


def test_incomplete_copilot_compaction_preserves_history(monkeypatch):
    monkeypatch.setenv("COPILOT_TOKEN", "static-token")
    monkeypatch.setattr(ChatSession, "reload_client", lambda self: None)
    cfg = ModelConfig(
        name="copilot-gpt", provider="copilot", model="gpt-5.6-sol",
        api_key_env="COPILOT_TOKEN", responses_api=True,
    )
    provider = ChatProvider(cfg)
    provider.client = _FakeResponsesClient(response=SimpleNamespace(
        status="incomplete",
        output_text="partial summary",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    ))
    history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "recent"},
    ]
    session = ChatSession(model="copilot-gpt", system="sys", history=history)
    session._provider = provider

    with pytest.raises(RuntimeError, match="max_output_tokens"):
        session.compact(keep_recent=1)

    assert session.history == history


def test_copilot_responses_stream_text_and_usage():
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta="Hel"),
        SimpleNamespace(type="response.output_text.delta", delta="lo"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=SimpleNamespace(
                input_tokens=5, output_tokens=2, total_tokens=7,
                input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
            )),
        ),
    ]
    client = _FakeResponsesClient(events=events)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    stream = _CopilotResponsesStream(client, cfg, [{"role": "user", "content": "hi"}], [], 50)
    text = ""
    finish_reason = None
    usage = None
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text += chunk.choices[0].delta.content
        if getattr(chunk, "finish_reason", None):
            finish_reason = chunk.finish_reason
            usage = chunk.usage

    assert text == "Hello"
    assert finish_reason == "stop"
    assert usage.total_tokens == 7


def test_copilot_responses_stream_tool_call_round_trip():
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(type="function_call", call_id="call_1", name="multiply"),
        ),
        SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta='{"a":1'),
        SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta=',"b":2}'),
        SimpleNamespace(type="response.function_call_arguments.done", output_index=0, arguments='{"a":1,"b":2}'),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, total_tokens=2,
                input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
            )),
        ),
    ]
    client = _FakeResponsesClient(events=events)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)
    tool_schemas = [{"type": "function", "function": {"name": "multiply", "parameters": {}}}]

    stream = _CopilotResponsesStream(client, cfg, [{"role": "user", "content": "hi"}], tool_schemas, 50)
    tool_id = None
    name = None
    arguments = ""
    finish_reason = None
    for chunk in stream:
        for tc in (chunk.choices[0].delta.tool_calls if chunk.choices else []):
            if tc.id:
                tool_id = tc.id
            if tc.function.name:
                name = tc.function.name
            if tc.function.arguments:
                arguments += tc.function.arguments
        if getattr(chunk, "finish_reason", None):
            finish_reason = chunk.finish_reason

    assert tool_id == "call_1"
    assert name == "multiply"
    assert arguments == '{"a":1,"b":2}'
    assert finish_reason == "tool_calls"


def test_copilot_responses_stream_incomplete_maps_to_length():
    events = [
        SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=SimpleNamespace(
                    input_tokens=1, output_tokens=1, total_tokens=2,
                    input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                ),
            ),
        ),
    ]
    client = _FakeResponsesClient(events=events)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    finish_reasons = [
        chunk.finish_reason
        for chunk in _CopilotResponsesStream(client, cfg, [{"role": "user", "content": "hi"}], [], 50)
        if getattr(chunk, "finish_reason", None)
    ]

    assert finish_reasons == ["length"]


def test_copilot_responses_stream_incomplete_tool_call_does_not_finish_as_tool_calls():
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(type="function_call", call_id="call_1", name="bash"),
        ),
        SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta='{"command":"echo'),
        SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=None,
            ),
        ),
    ]
    client = _FakeResponsesClient(events=events)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    chunks = list(_CopilotResponsesStream(client, cfg, [{"role": "user", "content": "hi"}], [], 50))

    assert chunks[-1].finish_reason == "length"


def test_copilot_responses_stream_rejects_other_incomplete_reasons():
    events = [
        SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                incomplete_details=SimpleNamespace(reason="content_filter"),
                usage=None,
            ),
        ),
    ]
    client = _FakeResponsesClient(events=events)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    with pytest.raises(RuntimeError, match="content_filter"):
        list(_CopilotResponsesStream(client, cfg, [{"role": "user", "content": "hi"}], [], 50))


def test_copilot_responses_stream_failed_event_raises():
    events = [
        SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(error=SimpleNamespace(message="boom")),
        ),
    ]
    client = _FakeResponsesClient(events=events)
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-5.6-sol", responses_api=True)

    stream = _CopilotResponsesStream(client, cfg, [{"role": "user", "content": "hi"}], [], 50)
    try:
        list(stream)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "boom" in str(exc)


def test_copilot_provider_routes_to_responses_api_with_static_key(monkeypatch):
    monkeypatch.setenv("COPILOT_TOKEN", "static-token")
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="hi"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, total_tokens=2,
                input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
            )),
        ),
    ]
    fake_client = _FakeResponsesClient(events=events)
    cfg = ModelConfig(
        name="copilot-gpt",
        provider="copilot",
        model="gpt-5.6-sol",
        api_key_env="COPILOT_TOKEN",
        responses_api=True,
    )
    provider = ChatProvider(cfg)
    provider.client = fake_client

    stream = provider.stream([{"role": "user", "content": "hi"}], [], 50)

    assert isinstance(stream, _CopilotResponsesStream)
    text = "".join(
        chunk.choices[0].delta.content or ""
        for chunk in stream
        if chunk.choices
    )
    assert text == "hi"
