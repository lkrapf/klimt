"""Provider/client adapters for model APIs."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Dict, Iterator

from openai import AzureOpenAI, OpenAI

from . import anthropic_oauth, copilot_oauth
from .api_types import Emit
from .model_config import ModelConfig, resolve_model_config
from .tool_impl import visual as _visual


ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_OAUTH_BETA = "claude-code-20250219,oauth-2025-04-20"
KLIMT_USER_AGENT = "Klimt/0.1"
COPILOT_API_BASE = "https://api.githubcopilot.com"
COPILOT_HEADERS = {
    "Editor-Version": "Klimt/0.1",
    "Editor-Plugin-Version": "klimt/0.1",
    "Copilot-Integration-Id": "vscode-chat",
}
PROVIDER_DEBUG = bool(os.environ.get("KLIMT_PROVIDER_DEBUG"))


class ChatProvider:
    """Thin adapter around the configured chat-completions provider."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._anthropic_oauth = config.provider == "anthropic" and not config.api_key_env
        self._copilot_oauth = config.provider == "copilot" and not config.api_key_env
        self._bedrock = config.provider == "bedrock"
        oauth = self._anthropic_oauth or self._copilot_oauth
        self._api_key = "" if oauth or self._bedrock else config.resolved_api_key()
        self.client = None if oauth else self._make_client(config, self._api_key)

    @classmethod
    def resolve(cls, name: str) -> "ChatProvider":
        return cls(resolve_model_config(name))

    @staticmethod
    def _make_client(config: ModelConfig, api_key: str) -> Any:
        if config.provider == "azure":
            return AzureOpenAI(
                azure_endpoint=config.base_url,
                api_key=api_key,
                api_version=config.api_version or "2024-10-21",
            )
        if config.provider in {"openai", "ollama"}:
            kwargs: Dict[str, Any] = {"api_key": api_key}
            if config.provider == "ollama":
                kwargs["base_url"] = config.base_url or "http://127.0.0.1:11434/v1"
            elif config.base_url:
                kwargs["base_url"] = config.base_url
            return OpenAI(**kwargs)
        if config.provider == "anthropic":
            return OpenAI(
                api_key=api_key,
                base_url=config.base_url or "https://api.anthropic.com/v1",
            )
        if config.provider == "copilot":
            return OpenAI(
                api_key=api_key,
                base_url=config.base_url or COPILOT_API_BASE,
                default_headers=COPILOT_HEADERS,
            )
        if config.provider == "bedrock":
            import boto3
            return boto3.client("bedrock-runtime", region_name=config.region or None)
        raise ValueError(f"unsupported model provider: {config.provider}")

    def provider_model(self) -> str:
        return self.config.provider_model()

    def preserves_reasoning_blocks(self) -> bool:
        return self._anthropic_oauth or self._bedrock

    def complete(
        self,
        messages: list[dict[str, Any]],
        max_completion_tokens: int,
        emit: Emit | None = None,
    ) -> Any:
        if self._anthropic_oauth:
            return _anthropic_oauth_complete(
                self.config,
                anthropic_oauth.access_token(),
                messages,
                max_completion_tokens,
            )
        if self._copilot_oauth:
            client = self._make_client(self.config, copilot_oauth.access_token(_on_copilot_device_code(emit)))
            if self.config.responses_api:
                return _copilot_responses_complete(client, self.config, messages, max_completion_tokens)
            return client.chat.completions.create(
                model=self.provider_model(),
                messages=_chat_completions_sanitize_messages(self.config.provider, messages),
                max_completion_tokens=max_completion_tokens,
            )
        if self._bedrock:
            return _bedrock_complete(self.config, self.client, messages, max_completion_tokens)
        if self.config.provider == "copilot" and self.config.responses_api:
            return _copilot_responses_complete(self.client, self.config, messages, max_completion_tokens)
        return self.client.chat.completions.create(
            model=self.provider_model(),
            messages=_chat_completions_sanitize_messages(self.config.provider, messages),
            max_completion_tokens=max_completion_tokens,
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        max_completion_tokens: int,
        emit: Emit | None = None,
    ) -> Any:
        if self._anthropic_oauth:
            return _AnthropicOAuthStream(
                self.config,
                anthropic_oauth.access_token(),
                messages,
                tool_schemas,
                max_completion_tokens,
            )
        if self._copilot_oauth:
            client = self._make_client(self.config, copilot_oauth.access_token(_on_copilot_device_code(emit)))
            if self.config.responses_api:
                return _CopilotResponsesStream(client, self.config, messages, tool_schemas, max_completion_tokens)
            return client.chat.completions.create(
                model=self.provider_model(),
                messages=_chat_completions_sanitize_messages(self.config.provider, messages),
                tools=tool_schemas,
                max_completion_tokens=max_completion_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
        if self._bedrock:
            return _BedrockStream(self.config, self.client, messages, tool_schemas, max_completion_tokens)
        if self.config.provider == "copilot" and self.config.responses_api:
            return _CopilotResponsesStream(self.client, self.config, messages, tool_schemas, max_completion_tokens)
        return self.client.chat.completions.create(
            model=self.provider_model(),
            messages=_chat_completions_sanitize_messages(self.config.provider, messages),
            tools=tool_schemas,
            max_completion_tokens=max_completion_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )


def _on_copilot_device_code(emit: Emit | None) -> copilot_oauth.OnDeviceCode | None:
    """Build a device-code callback that surfaces the login prompt in chat.

    Runs on a background worker thread; `emit` (backed by `evaluate_js`) is
    safe to call from there.
    """
    if emit is None:
        return None

    def _on_device_code(verification_uri: str, user_code: str) -> None:
        emit({
            "type": "text",
            "content": (
                "**GitHub Copilot sign-in required.**\n\n"
                f"Open [{verification_uri}]({verification_uri}) and enter code `{user_code}`."
            ),
        })

    return _on_device_code


def _copilot_responses_tools(tool_schemas: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Flatten chat-completions tool schemas into Responses API shape."""
    out: list[dict[str, Any]] = []
    for schema in tool_schemas or []:
        fn = schema.get("function") or {}
        out.append({
            "type": "function",
            "name": fn.get("name") or "",
            "description": fn.get("description") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _copilot_responses_image_content(placeholder: str, envelope: dict[str, Any]) -> list[dict[str, Any]]:
    media_type = envelope.get("media_type") or "image/png"
    data = envelope.get("data") or ""
    return [
        {"type": "input_text", "text": placeholder},
        {"type": "input_image", "image_url": f"data:{media_type};base64,{data}"},
    ]


def _copilot_responses_items(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate one canonical Klimt message into Responses API input items."""
    role = msg.get("role")
    if role == "tool":
        call_id = str(msg.get("tool_call_id") or "")
        envelope = _visual.parse_envelope(msg.get("content"))
        if envelope is not None:
            placeholder = _visual.envelope_summary(envelope)
            return [
                {"type": "function_call_output", "call_id": call_id, "output": placeholder},
                {"role": "user", "content": _copilot_responses_image_content(placeholder, envelope)},
            ]
        return [{
            "type": "function_call_output",
            "call_id": call_id,
            "output": str(msg.get("content") or ""),
        }]
    if role == "assistant":
        items: list[dict[str, Any]] = []
        content = msg.get("content")
        if content:
            items.append({"role": "assistant", "content": str(content)})
        for tool_call in msg.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            items.append({
                "type": "function_call",
                "call_id": str(tool_call.get("id") or ""),
                "name": str(fn.get("name") or ""),
                "arguments": str(fn.get("arguments") or "{}"),
            })
        return items
    # user (and any other) roles: plain text or a visual-tool image envelope.
    envelope = _visual.parse_envelope(msg.get("content"))
    if envelope is not None:
        placeholder = _visual.envelope_summary(envelope)
        return [{"role": "user", "content": _copilot_responses_image_content(placeholder, envelope)}]
    return [{"role": role or "user", "content": str(msg.get("content") or "")}]


def _copilot_responses_kwargs(
    config: ModelConfig,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None,
    max_completion_tokens: int,
) -> dict[str, Any]:
    """Build `client.responses.create(...)` kwargs from canonical Klimt history.

    The system message becomes the top-level `instructions` string; everything
    else is flattened into `input` items (see `_copilot_responses_items`).
    """
    instructions = ""
    input_items: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            if not instructions:
                instructions = str(msg.get("content") or "")
            continue
        input_items.extend(_copilot_responses_items(msg))

    kwargs: dict[str, Any] = {
        "model": config.provider_model(),
        "input": input_items,
        "max_output_tokens": max_completion_tokens,
    }
    if instructions:
        kwargs["instructions"] = instructions
    tools = _copilot_responses_tools(tool_schemas)
    if tools:
        kwargs["tools"] = tools
    return kwargs


def _copilot_responses_usage(usage: Any) -> Any:
    if usage is None:
        return None
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    input_details = getattr(usage, "input_tokens_details", None)
    cached_tokens = int(getattr(input_details, "cached_tokens", 0) or 0) if input_details else 0
    cache_write_tokens = int(getattr(input_details, "cache_write_tokens", 0) or 0) if input_details else 0
    total_tokens = int(getattr(usage, "total_tokens", 0) or (input_tokens + output_tokens))
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
        ),
    )


def _copilot_responses_complete(
    client: Any,
    config: ModelConfig,
    messages: list[dict[str, Any]],
    max_completion_tokens: int,
) -> Any:
    """Non-streaming Responses API call, used only by compaction."""
    kwargs = _copilot_responses_kwargs(config, messages, None, max_completion_tokens)
    response = client.responses.create(**kwargs)
    if response.status != "completed":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        error = getattr(response, "error", None)
        message = getattr(error, "message", None)
        raise RuntimeError(
            f"Copilot Responses API returned {response.status}: "
            f"{message or reason or 'no details provided'}"
        )
    text = getattr(response, "output_text", "") or ""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=_copilot_responses_usage(getattr(response, "usage", None)),
    )


def _copilot_responses_error_message(event: Any) -> str:
    response = getattr(event, "response", None)
    error = getattr(response, "error", None) if response is not None else getattr(event, "error", None)
    if error is not None:
        message = getattr(error, "message", None)
        if message:
            return str(message)
    return str(getattr(event, "message", None) or event)


class _CopilotResponsesStream:
    """Adapts `client.responses.create(..., stream=True)` to Klimt's chunk protocol."""

    def __init__(
        self,
        client: Any,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        max_completion_tokens: int,
    ) -> None:
        kwargs = _copilot_responses_kwargs(config, messages, tool_schemas, max_completion_tokens)
        self._stream = client.responses.create(**kwargs, stream=True)
        self._items: Dict[int, dict[str, Any]] = {}
        self._next_index = 0
        self._saw_tool_call = False

    def close(self) -> None:
        self._stream.close()

    def __iter__(self) -> Iterator[Any]:
        for event in self._stream:
            _debug_provider_event("copilot", {"type": event.type})
            yield from self._on_event(event)

    def _on_event(self, event: Any) -> Iterator[Any]:
        etype = event.type
        if etype == "response.output_text.delta":
            yield _chunk(content=event.delta or "")
        elif etype == "response.reasoning_summary_text.delta":
            yield _chunk(reasoning=event.delta or "")
        elif etype == "response.output_item.added":
            item = event.item
            if getattr(item, "type", None) == "function_call":
                call_id = str(getattr(item, "call_id", "") or "")
                name = str(getattr(item, "name", "") or "")
                if not call_id or not name:
                    raise RuntimeError("Copilot Responses API sent a malformed function_call item")
                output_index = event.output_index
                index = self._next_index
                self._next_index += 1
                self._items[output_index] = {"index": index}
                self._saw_tool_call = True
                yield _chunk(tool_calls=[_tool_delta(index=index, tool_id=call_id, name=name)])
        elif etype == "response.function_call_arguments.delta":
            slot = self._items.get(event.output_index)
            if slot is None:
                raise RuntimeError(
                    f"Copilot Responses API sent function-call arguments for unknown item {event.output_index}"
                )
            yield _chunk(tool_calls=[_tool_delta(index=slot["index"], arguments=event.delta or "")])
        elif etype in {"response.incomplete", "response.completed"}:
            usage = _copilot_responses_usage(getattr(event.response, "usage", None))
            if etype == "response.incomplete":
                details = getattr(event.response, "incomplete_details", None)
                reason = getattr(details, "reason", None)
                if reason != "max_output_tokens":
                    raise RuntimeError(f"Copilot Responses API incomplete: {reason or 'no reason provided'}")
                finish_reason = "length"
            else:
                finish_reason = "tool_calls" if self._saw_tool_call else "stop"
            yield SimpleNamespace(choices=[], usage=usage, finish_reason=finish_reason)
        elif etype in {"response.failed", "error"}:
            raise RuntimeError(f"Copilot Responses API error: {_copilot_responses_error_message(event)}")


def _chat_completions_sanitize_messages(
    provider: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize canonical history for OpenAI chat-completions APIs.

    Klimt keeps provider-neutral history. Some OpenAI-compatible endpoints reject
    assistant messages with JSON null content, especially tool-call turns. Avoid
    persisting provider-specific rewrites; adapt the outbound payload instead.

    Also unpacks `visual` tool envelopes: chat-completions does not accept image
    content parts on a `role: tool` message, so the tool result is kept as a
    short text placeholder and an extra synthetic `user` message carrying the
    image is inserted immediately after.

    User-turn image envelopes (pasted images) are expanded into a multi-part
    user message with text + image_url parts.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            envelope = _visual.parse_envelope(msg.get("content"))
            if envelope is not None:
                placeholder = _visual.envelope_summary(envelope)
                out.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id") or "",
                    "content": placeholder,
                })
                out.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": placeholder},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{envelope.get('media_type', 'image/png')};"
                                    f"base64,{envelope.get('data', '')}"
                                ),
                            },
                        },
                    ],
                })
                continue
            out.append(msg)
            continue
        if role == "user":
            envelope = _visual.parse_envelope(msg.get("content"))
            if envelope is not None:
                placeholder = _visual.envelope_summary(envelope)
                out.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": placeholder},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{envelope.get('media_type', 'image/png')};"
                                    f"base64,{envelope.get('data', '')}"
                                ),
                            },
                        },
                    ],
                })
                continue
            out.append(msg)
            continue
        if role != "assistant" or msg.get("content") is not None:
            out.append(msg)
            continue
        msg = dict(msg)
        if provider == "ollama" and msg.get("tool_calls"):
            msg.pop("content", None)
        else:
            msg["content"] = ""
        out.append(msg)
    return out


def _debug_provider_event(provider: str, event: dict[str, Any]) -> None:
    if not PROVIDER_DEBUG:
        return
    print(f"[klimt:{provider}] {json.dumps(event, ensure_ascii=False, default=str)}", flush=True)


def _bedrock_request(
    config: ModelConfig,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None,
    max_completion_tokens: int,
) -> dict[str, Any]:
    system, rest = _split_system(messages)
    bedrock_messages = _bedrock_messages(rest, vision=config.vision)
    request: dict[str, Any] = {
        "modelId": config.provider_model(),
        "messages": bedrock_messages,
        "inferenceConfig": {"maxTokens": max_completion_tokens},
    }
    if system:
        request["system"] = [{"text": system}]
    if config.adaptive_thinking:
        request["additionalModelRequestFields"] = {
            "thinking": {"type": "adaptive"}
        }
    elif config.thinking_budget_tokens:
        if config.thinking_budget_tokens >= max_completion_tokens:
            raise ValueError("thinking_budget_tokens must be lower than max_completion_tokens")
        request["additionalModelRequestFields"] = {
            "thinking": {
                "type": "enabled",
                "budget_tokens": config.thinking_budget_tokens,
            }
        }
    tools = _bedrock_tools(tool_schemas or [])
    if tools:
        request["toolConfig"] = {"tools": tools}
    if config.cache_prompts:
        _bedrock_apply_cache_points(request)
    return request


def _bedrock_apply_cache_points(request: dict[str, Any]) -> None:
    """Stamp cachePoint blocks on the most stable prefixes.

    Bedrock prompt caching: append `{cachePoint: {type: "default"}}` after the
    chunks we want cached. Three breakpoints today (Anthropic models accept 4):
      1. end of `system` (kernel + tool manifest + AGENTS.md)
      2. end of `toolConfig.tools` (tool schemas)
      3. end of the last message's content (history prefix)

    Cache hits return as `cacheReadInputTokens`; creations are billed at +25%
    of input for 5-minute caches. Net win starts at the second turn.
    """
    system = request.get("system")
    if system:
        system.append({"cachePoint": {"type": "default"}})

    tool_config = request.get("toolConfig")
    if tool_config and tool_config.get("tools"):
        tool_config["tools"].append({"cachePoint": {"type": "default"}})

    messages = request.get("messages") or []
    if messages:
        last = messages[-1]
        content = last.get("content")
        if isinstance(content, list):
            content.append({"cachePoint": {"type": "default"}})


def _bedrock_messages(messages: list[dict[str, Any]], *, vision: bool = True) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            _append_bedrock_message(out, "user", [{
                "toolResult": _bedrock_tool_result(
                    str(msg.get("tool_call_id") or ""),
                    msg.get("content"),
                    vision=vision,
                ),
            }])
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            reasoning = msg.get("reasoning")
            reasoning_signature = msg.get("reasoning_signature")
            if reasoning and reasoning_signature:
                blocks.append({
                    "reasoningContent": {
                        "reasoningText": {
                            "text": str(reasoning),
                            "signature": str(reasoning_signature),
                        }
                    }
                })
            content = msg.get("content")
            if content:
                blocks.append({"text": str(content)})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    tool_input = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_input = {}
                if not isinstance(tool_input, dict):
                    tool_input = {}
                blocks.append({
                    "toolUse": {
                        "toolUseId": str(tc.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "input": tool_input,
                    },
                })
            _append_bedrock_message(out, "assistant", blocks or [{"text": ""}])
        elif role == "user":
            envelope = _visual.parse_envelope(msg.get("content"))
            if envelope is not None:
                blocks: list[dict[str, Any]] = [{"text": _visual.envelope_summary(envelope)}]
                image = _bedrock_image_block(envelope) if vision else None
                if image is not None:
                    blocks.append({"image": image})
                _append_bedrock_message(out, "user", blocks)
            else:
                _append_bedrock_message(out, "user", _bedrock_user_blocks(msg.get("content")))
    return out


def _append_bedrock_message(out: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if out and out[-1]["role"] == role:
        out[-1]["content"].extend(blocks)
        return
    out.append({"role": role, "content": blocks})


def _bedrock_user_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                blocks.append({"text": str(part.get("text") or "")})
            else:
                blocks.append({"text": str(part)})
        return blocks or [{"text": ""}]
    return [{"text": str(content or "")}]


def _bedrock_tool_result(tool_use_id: str, content: Any, *, vision: bool) -> dict[str, Any]:
    envelope = _visual.parse_envelope(content)
    if envelope is None:
        return {"toolUseId": tool_use_id, "content": [{"text": str(content or "")}]}
    result_blocks: list[dict[str, Any]] = [{"text": _visual.envelope_summary(envelope)}]
    image = _bedrock_image_block(envelope) if vision else None
    if image is not None:
        result_blocks.append({"image": image})
    return {"toolUseId": tool_use_id, "content": result_blocks}


def _bedrock_image_block(envelope: dict[str, Any]) -> dict[str, Any] | None:
    media_type = str(envelope.get("media_type") or "")
    formats = {
        "image/png": "png",
        "image/jpeg": "jpeg",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    image_format = formats.get(media_type)
    if not image_format:
        return None
    data = envelope.get("data")
    if not isinstance(data, str):
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        return None
    return {"format": image_format, "source": {"bytes": raw}}


def _bedrock_tools(tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for schema in tool_schemas:
        fn = schema.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        out.append({
            "toolSpec": {
                "name": name,
                "description": fn.get("description") or "",
                "inputSchema": {"json": fn.get("parameters") or {"type": "object"}},
            },
        })
    return out


def _bedrock_usage(usage: dict[str, Any]) -> Any:
    input_tokens = int(usage.get("inputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    cache_read = int(usage.get("cacheReadInputTokens") or 0)
    cache_write = int(usage.get("cacheWriteInputTokens") or 0)
    # Bedrock's `totalTokens` already accounts for cache reads/writes when the
    # API populates it; only fall back to a manual sum when missing.
    total_tokens = int(
        usage.get("totalTokens")
        or (input_tokens + output_tokens + cache_read + cache_write)
    )
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
    )


def _bedrock_error(config: ModelConfig, exc: Exception, client: Any | None = None) -> str:
    code = type(exc).__name__
    message = str(exc)
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        code = str(error.get("Code") or code)
        message = str(error.get("Message") or message)
    region = config.region or str(getattr(getattr(client, "meta", None), "region_name", "") or "")
    region_text = f", region={region}" if region else ""
    return f"Bedrock API error {code} (model={config.provider_model()}{region_text}): {message}"


def _bedrock_complete(
    config: ModelConfig,
    client: Any,
    messages: list[dict[str, Any]],
    max_completion_tokens: int,
) -> Any:
    request = _bedrock_request(config, messages, None, max_completion_tokens)
    try:
        data = client.converse(**request)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(_bedrock_error(config, exc, client)) from exc
    content = "".join(
        block.get("text", "")
        for block in ((data.get("output") or {}).get("message") or {}).get("content", [])
        if "text" in block
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=_bedrock_usage(data.get("usage") or {}),
    )


class _BedrockStream:
    def __init__(
        self,
        config: ModelConfig,
        client: Any,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        max_completion_tokens: int,
    ) -> None:
        self._config = config
        self._client = client
        self._request = _bedrock_request(config, messages, tool_schemas, max_completion_tokens)
        self._stream: Any = None
        self._tool_blocks: dict[int, dict[str, str]] = {}
        self._reasoning_blocks: dict[int, dict[str, str]] = {}

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close:
            close()

    def __iter__(self) -> Iterator[Any]:
        try:
            response = self._client.converse_stream(**self._request)
            self._stream = response.get("stream")
            if self._stream:
                for event in self._stream:
                    _debug_provider_event("bedrock", event)
                    yield from self._event(event)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(_bedrock_error(self._config, exc, self._client)) from exc
        finally:
            self._stream = None

    def _event(self, event: dict[str, Any]) -> Iterator[Any]:
        if "contentBlockStart" in event:
            start_event = event["contentBlockStart"]
            start = start_event.get("start") or {}
            tool = start.get("toolUse") or {}
            if tool:
                index = int(start_event.get("contentBlockIndex") or 0)
                tool_id = str(tool.get("toolUseId") or "")
                name = str(tool.get("name") or "")
                if not tool_id or not name:
                    raise RuntimeError(f"Bedrock sent malformed toolUse block at index {index}")
                self._tool_blocks[index] = {"id": tool_id, "name": name, "args": ""}
                yield _chunk(tool_calls=[_tool_delta(index=index, tool_id=tool_id, name=name)])
        elif "contentBlockDelta" in event:
            delta_event = event["contentBlockDelta"]
            delta = delta_event.get("delta") or {}
            if "text" in delta:
                yield _chunk(content=delta.get("text") or "")
            elif "toolUse" in delta:
                index = int(delta_event.get("contentBlockIndex") or 0)
                partial = str((delta.get("toolUse") or {}).get("input") or "")
                block = self._tool_blocks.get(index)
                if block is None:
                    raise RuntimeError(f"Bedrock sent tool input JSON for unknown content block {index}")
                block["args"] += partial
                yield _chunk(tool_calls=[_tool_delta(index=index, arguments=partial)])
            elif "reasoningContent" in delta:
                rc = delta.get("reasoningContent") or {}
                thinking_delta = rc.get("text")
                signature_delta = rc.get("signature")
                index = int(delta_event.get("contentBlockIndex") or 0)
                if thinking_delta is not None:
                    block = self._reasoning_blocks.setdefault(index, {"text": "", "signature": ""})
                    block["text"] += thinking_delta
                    yield _chunk(reasoning=thinking_delta)
                if signature_delta is not None:
                    block = self._reasoning_blocks.setdefault(index, {"text": "", "signature": ""})
                    block["signature"] += signature_delta
                    yield _chunk(reasoning_signature=signature_delta)
        elif "contentBlockStop" in event:
            index = int(event["contentBlockStop"].get("contentBlockIndex") or 0)
            if index in self._tool_blocks:
                self._validate_tool_block(index)
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
            if stop_reason:
                yield SimpleNamespace(choices=[], usage=None, finish_reason=str(stop_reason))
        elif "metadata" in event:
            usage = (event["metadata"] or {}).get("usage") or {}
            if usage:
                yield SimpleNamespace(choices=[], usage=_bedrock_usage(usage))

    def _validate_tool_block(self, index: int) -> None:
        block = self._tool_blocks[index]
        try:
            parsed = json.loads(block["args"] or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Bedrock tool input JSON was incomplete for tool {block['name']}: {exc.msg}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Bedrock tool input for tool {block['name']} was not a JSON object")


def _anthropic_base_url(config: ModelConfig) -> str:
    base = (config.base_url or "https://api.anthropic.com").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _anthropic_headers(api_key: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "accept": "application/json",
        "authorization": f"Bearer {api_key}",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": ANTHROPIC_OAUTH_BETA,
        "user-agent": KLIMT_USER_AGENT,
    }


def _anthropic_payload(
    config: ModelConfig,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None,
    max_completion_tokens: int,
    *,
    stream: bool,
) -> dict[str, Any]:
    system, rest = _split_system(messages)
    payload: dict[str, Any] = {
        "model": config.provider_model(),
        "max_tokens": max_completion_tokens,
        "system": _anthropic_system(system),
        "messages": _anthropic_messages(rest),
        "stream": stream,
    }
    if config.thinking_budget_tokens:
        if config.thinking_budget_tokens >= max_completion_tokens:
            raise ValueError("thinking_budget_tokens must be lower than max_completion_tokens")
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": config.thinking_budget_tokens,
        }
    tools = _anthropic_tools(tool_schemas or [])
    if tools:
        payload["tools"] = tools
    if config.cache_prompts:
        _anthropic_apply_cache_breakpoints(payload)
    return payload


def _anthropic_apply_cache_breakpoints(payload: dict[str, Any]) -> None:
    """Mark the most stable prefixes with ephemeral cache breakpoints.

    Anthropic prompt caching allows up to four `cache_control` breakpoints per
    request. We claim three:
      1. last `system` block (kernel + tool manifest + AGENTS.md)
      2. last entry in `tools` (tool schemas)
      3. last block of the last message (history prefix)

    Each cache write costs +25% of input tokens; each read costs 10% of input.
    The win shows up from turn two onward when the prefix repeats verbatim.
    Header `prompt-caching-2024-07-31` is already implied for current models;
    the OAuth beta header carries any required flag for the native endpoint.
    """
    system = payload.get("system")
    if isinstance(system, list) and system:
        last = system[-1]
        if isinstance(last, dict):
            last["cache_control"] = {"type": "ephemeral"}

    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        last = tools[-1]
        if isinstance(last, dict):
            last["cache_control"] = {"type": "ephemeral"}

    messages = payload.get("messages") or []
    if messages:
        last_msg = messages[-1]
        content = last_msg.get("content")
        if isinstance(content, list) and content:
            last_block = content[-1]
            if isinstance(last_block, dict):
                last_block["cache_control"] = {"type": "ephemeral"}


def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content")
            if content:
                system_parts.append(str(content))
        else:
            rest.append(msg)
    return "\n".join(system_parts), rest


def _anthropic_system(system: str) -> list[dict[str, str]]:
    out = [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."}]
    if system:
        out.append({"type": "text", "text": system})
    return out


def _anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            envelope = _visual.parse_envelope(msg.get("content"))
            if envelope is not None:
                placeholder = _visual.envelope_summary(envelope)
                _append_anthropic_message(out, "user", [{
                    "type": "tool_result",
                    "tool_use_id": str(msg.get("tool_call_id") or ""),
                    "content": [
                        {"type": "text", "text": placeholder},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": envelope.get("media_type", "image/png"),
                                "data": envelope.get("data", ""),
                            },
                        },
                    ],
                }])
                continue
            _append_anthropic_message(out, "user", [{
                "type": "tool_result",
                "tool_use_id": str(msg.get("tool_call_id") or ""),
                "content": str(msg.get("content") or ""),
            }])
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            reasoning = msg.get("reasoning")
            signature = msg.get("reasoning_signature")
            if reasoning and signature:
                blocks.append({
                    "type": "thinking",
                    "thinking": str(reasoning),
                    "signature": str(signature),
                })
            content = msg.get("content")
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    tool_input = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_input = {"_raw": fn.get("arguments") or ""}
                blocks.append({
                    "type": "tool_use",
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "input": tool_input,
                })
            _append_anthropic_message(out, "assistant", blocks or [{"type": "text", "text": ""}])
        elif role == "user":
            envelope = _visual.parse_envelope(msg.get("content"))
            if envelope is not None:
                placeholder = _visual.envelope_summary(envelope)
                _append_anthropic_message(out, "user", [
                    {"type": "text", "text": placeholder},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": envelope.get("media_type", "image/png"),
                            "data": envelope.get("data", ""),
                        },
                    },
                ])
            else:
                _append_anthropic_message(out, "user", [{"type": "text", "text": str(msg.get("content") or "")}])
    return out


def _append_anthropic_message(out: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if out and out[-1]["role"] == role:
        out[-1]["content"].extend(blocks)
        return
    out.append({"role": role, "content": blocks})


def _anthropic_tools(tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for schema in tool_schemas:
        fn = schema.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        out.append({
            "name": name,
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object"},
        })
    return out


def _anthropic_initial_tool_args(value: Any) -> str:
    if value in (None, "", {}):
        return ""
    return json.dumps(value, ensure_ascii=False)


def _anthropic_request(config: ModelConfig, api_key: str, payload: dict[str, Any]) -> urllib.request.Request:
    req = urllib.request.Request(
        f"{_anthropic_base_url(config)}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    for key, value in _anthropic_headers(api_key).items():
        req.add_header(key, value)
    return req


def _anthropic_oauth_complete(
    config: ModelConfig,
    api_key: str,
    messages: list[dict[str, Any]],
    max_completion_tokens: int,
) -> Any:
    payload = _anthropic_payload(config, messages, None, max_completion_tokens, stream=False)
    try:
        with urllib.request.urlopen(_anthropic_request(config, api_key, payload), timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_anthropic_error(exc)) from exc
    content = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    usage = data.get("usage") or {}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=_anthropic_usage(usage),
    )


def _anthropic_error(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace")
    return f"Anthropic API error {exc.code}: {body}"


def _anthropic_usage(usage: dict[str, Any]) -> Any:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens + cache_read + cache_write,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
    )


class _AnthropicOAuthStream:
    def __init__(
        self,
        config: ModelConfig,
        api_key: str,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        max_completion_tokens: int,
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._payload = _anthropic_payload(config, messages, tool_schemas, max_completion_tokens, stream=True)
        self._response: Any = None
        self._usage: dict[str, Any] = {}
        self._tool_blocks: dict[int, dict[str, str]] = {}

    def close(self) -> None:
        if self._response is not None:
            self._response.close()

    def __iter__(self) -> Iterator[Any]:
        try:
            with urllib.request.urlopen(_anthropic_request(self._config, self._api_key, self._payload), timeout=120) as response:
                self._response = response
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        _debug_provider_event("anthropic", event)
                        yield from self._event(event)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_anthropic_error(exc)) from exc
        finally:
            self._response = None

    def _event(self, event: dict[str, Any]) -> Iterator[Any]:
        event_type = event.get("type")
        if event_type == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                yield _chunk(content=delta.get("text") or "")
            elif delta.get("type") == "thinking_delta":
                yield _chunk(reasoning=delta.get("thinking") or "")
            elif delta.get("type") == "signature_delta":
                yield _chunk(reasoning_signature=delta.get("signature") or "")
            elif delta.get("type") == "input_json_delta":
                index = int(event.get("index") or 0)
                block = self._tool_blocks.get(index)
                if block is None:
                    raise RuntimeError(f"Anthropic sent tool input JSON for unknown content block {index}")
                partial = delta.get("partial_json") or ""
                block["args"] += partial
                yield _chunk(tool_calls=[_tool_delta(
                    index=index,
                    arguments=partial,
                )])
        elif event_type == "content_block_start":
            block = event.get("content_block") or {}
            index = int(event.get("index") or 0)
            if block.get("type") == "tool_use":
                tool_id = str(block.get("id") or "")
                name = str(block.get("name") or "")
                if not tool_id or not name:
                    raise RuntimeError(f"Anthropic sent malformed tool_use block at index {index}")
                initial_args = _anthropic_initial_tool_args(block.get("input"))
                self._tool_blocks[index] = {"id": tool_id, "name": name, "args": initial_args}
                yield _chunk(tool_calls=[_tool_delta(
                    index=index,
                    tool_id=tool_id,
                    name=name,
                )])
                if initial_args:
                    yield _chunk(tool_calls=[_tool_delta(index=index, arguments=initial_args)])
            elif block.get("type") == "text" and block.get("text"):
                yield _chunk(content=block.get("text") or "")
            elif block.get("type") == "thinking":
                if block.get("thinking"):
                    yield _chunk(reasoning=block.get("thinking") or "")
                if block.get("signature"):
                    yield _chunk(reasoning_signature=block.get("signature") or "")
        elif event_type == "content_block_stop":
            index = int(event.get("index") or 0)
            if index in self._tool_blocks:
                self._validate_tool_block(index)
        elif event_type == "message_delta":
            usage = event.get("usage") or {}
            if usage:
                self._usage.update(usage)
            stop_reason = (event.get("delta") or {}).get("stop_reason")
            if stop_reason:
                yield SimpleNamespace(choices=[], usage=None, finish_reason=str(stop_reason))
        elif event_type == "message_start":
            usage = (event.get("message") or {}).get("usage") or {}
            if usage:
                self._usage.update(usage)
        elif event_type == "message_stop":
            yield SimpleNamespace(choices=[], usage=_anthropic_usage(self._usage))

    def _validate_tool_block(self, index: int) -> None:
        block = self._tool_blocks[index]
        if not block["id"] or not block["name"]:
            raise RuntimeError(f"Anthropic sent malformed tool_use block at index {index}")
        try:
            parsed = json.loads(block["args"] or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Anthropic tool input JSON was incomplete for tool {block['name']}: {exc.msg}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Anthropic tool input for tool {block['name']} was not a JSON object")


def _chunk(
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    reasoning: str | None = None,
    reasoning_signature: str | None = None,
) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(
            content=content,
            tool_calls=tool_calls or [],
            reasoning=reasoning,
            reasoning_signature=reasoning_signature,
        ))],
        usage=None,
    )


def _tool_delta(
    *,
    index: int = 0,
    tool_id: str = "",
    name: str = "",
    arguments: str = "",
) -> Any:
    return SimpleNamespace(
        index=index,
        id=tool_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )
