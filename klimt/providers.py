"""Provider/client adapters for model APIs."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Dict, Iterator

from openai import AzureOpenAI, OpenAI

from . import anthropic_oauth
from .model_config import ModelConfig, resolve_model_config


ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_OAUTH_BETA = "claude-code-20250219,oauth-2025-04-20"
KLIMT_USER_AGENT = "Klimt/0.1"
PROVIDER_DEBUG = bool(os.environ.get("KLIMT_PROVIDER_DEBUG"))


class ChatProvider:
    """Thin adapter around the configured chat-completions provider."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._anthropic_oauth = config.provider == "anthropic" and not config.api_key_env
        self._api_key = "" if self._anthropic_oauth else config.resolved_api_key()
        self.client = None if self._anthropic_oauth else self._make_client(config, self._api_key)

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
        raise ValueError(f"unsupported model provider: {config.provider}")

    def provider_model(self) -> str:
        return self.config.provider_model()

    def preserves_reasoning_blocks(self) -> bool:
        return self._anthropic_oauth

    def complete(self, messages: list[dict[str, Any]], max_completion_tokens: int) -> Any:
        if self._anthropic_oauth:
            return _anthropic_oauth_complete(
                self.config,
                anthropic_oauth.access_token(),
                messages,
                max_completion_tokens,
            )
        return self.client.chat.completions.create(
            model=self.provider_model(),
            messages=messages,
            max_completion_tokens=max_completion_tokens,
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        max_completion_tokens: int,
    ) -> Any:
        if self._anthropic_oauth:
            return _AnthropicOAuthStream(
                self.config,
                anthropic_oauth.access_token(),
                messages,
                tool_schemas,
                max_completion_tokens,
            )
        return self.client.chat.completions.create(
            model=self.provider_model(),
            messages=messages,
            tools=tool_schemas,
            max_completion_tokens=max_completion_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )


def _debug_provider_event(provider: str, event: dict[str, Any]) -> None:
    if not PROVIDER_DEBUG:
        return
    print(f"[klimt:{provider}] {json.dumps(event, ensure_ascii=False)}", flush=True)


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
    return payload


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
            _append_anthropic_message(out, "user", [{
                "type": "tool_result",
                "tool_use_id": str(msg.get("tool_call_id") or ""),
                "content": str(msg.get("content") or ""),
            }])
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            reasoning = msg.get("reasoning")
            if reasoning:
                thinking_block = {"type": "thinking", "thinking": str(reasoning)}
                signature = msg.get("reasoning_signature")
                if signature:
                    thinking_block["signature"] = str(signature)
                blocks.append(thinking_block)
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
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cache_read),
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
