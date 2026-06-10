import base64
import json
from types import SimpleNamespace

import pytest

from klimt.model_config import ModelConfig
from klimt.providers import (
    _BedrockStream,
    _bedrock_image_block,
    _bedrock_messages,
    _bedrock_request,
    _bedrock_tools,
)


def test_bedrock_request_splits_system_and_maps_tools():
    cfg = ModelConfig(name="br", provider="bedrock", model="anthropic.claude", region="us-east-1")
    request = _bedrock_request(
        cfg,
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        [{
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }],
        123,
    )

    assert request == {
        "modelId": "anthropic.claude",
        "messages": [{"role": "user", "content": [{"text": "hi"}]}],
        "system": [{"text": "sys"}],
        "inferenceConfig": {"maxTokens": 123},
        "toolConfig": {
            "tools": [{
                "toolSpec": {
                    "name": "read",
                    "description": "Read file",
                    "inputSchema": {"json": {"type": "object", "properties": {"path": {"type": "string"}}}},
                },
            }],
        },
    }


def test_bedrock_request_rejects_thinking_budget():
    cfg = ModelConfig(name="br", provider="bedrock", model="m", thinking_budget_tokens=1)

    with pytest.raises(ValueError, match="thinking_budget_tokens"):
        _bedrock_request(cfg, [], [], 100)


def test_bedrock_messages_maps_assistant_tool_calls_and_tool_results():
    messages = _bedrock_messages([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "read", "arguments": '{"path":"providers.py"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "toolu_1", "content": "result text"},
    ])

    assert messages == [
        {
            "role": "assistant",
            "content": [{
                "toolUse": {
                    "toolUseId": "toolu_1",
                    "name": "read",
                    "input": {"path": "providers.py"},
                },
            }],
        },
        {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": "toolu_1",
                    "content": [{"text": "result text"}],
                },
            }],
        },
    ]


def test_bedrock_tools_keeps_schema_unchanged():
    schema = {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Edit",
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer", "minimum": 1}},
                "required": ["n"],
            },
        },
    }

    assert _bedrock_tools([schema]) == [{
        "toolSpec": {
            "name": "edit",
            "description": "Edit",
            "inputSchema": {"json": schema["function"]["parameters"]},
        },
    }]


def test_bedrock_image_block_decodes_visual_envelope():
    raw = b"\x89PNG\r\n\x1a\nimage-bytes"
    envelope = {
        "_klimt_image": True,
        "media_type": "image/png",
        "data": base64.b64encode(raw).decode("ascii"),
    }

    assert _bedrock_image_block(envelope) == {
        "format": "png",
        "source": {"bytes": raw},
    }


class FakeBedrockClient:
    def converse_stream(self, **request):
        self.request = request
        return {
            "stream": iter([
                {"messageStart": {"role": "assistant"}},
                {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "t1", "name": "read"}}}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"path"'}}}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": ':"x"}'}}}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "tool_use"}},
                {"metadata": {"usage": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5}}},
            ]),
        }


def test_bedrock_stream_yields_openai_like_tool_deltas_and_usage():
    cfg = ModelConfig(name="br", provider="bedrock", model="m")
    stream = _BedrockStream(cfg, FakeBedrockClient(), [{"role": "user", "content": "hi"}], [], 100)

    chunks = list(stream)

    assert chunks[0].choices[0].delta.tool_calls[0].id == "t1"
    assert chunks[0].choices[0].delta.tool_calls[0].function.name == "read"
    assert chunks[1].choices[0].delta.tool_calls[0].function.arguments == '{"path"'
    assert chunks[2].choices[0].delta.tool_calls[0].function.arguments == ':"x"}'
    assert chunks[3].finish_reason == "tool_use"
    assert chunks[4].usage.prompt_tokens == 2
    assert chunks[4].usage.completion_tokens == 3
