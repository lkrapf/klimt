"""Provider/client adapters for model APIs."""
from __future__ import annotations

import os
from typing import Any, Dict

from openai import AzureOpenAI, OpenAI

from .model_config import ModelConfig, resolve_model_config


class ChatProvider:
    """Thin adapter around the configured chat-completions provider.

    This keeps provider/client construction and request-option quirks out of
    ChatSession. It is intentionally small; provider-specific compatibility can
    grow here without turning session state into a switchboard.
    """

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.client = self._make_client(config)

    @classmethod
    def resolve(cls, name: str) -> "ChatProvider":
        return cls(resolve_model_config(name))

    @staticmethod
    def _make_client(config: ModelConfig) -> Any:
        if config.provider == "azure":
            return AzureOpenAI(
                azure_endpoint=config.base_url or os.environ["AZURE_OPENAI_BASE_URL"],
                api_key=config.resolved_api_key(),
                api_version=config.api_version or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        if config.provider in {"openai", "ollama"}:
            kwargs: Dict[str, Any] = {"api_key": config.resolved_api_key()}
            if config.provider == "ollama":
                kwargs["base_url"] = config.base_url or os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
            elif config.base_url:
                kwargs["base_url"] = config.base_url
            return OpenAI(**kwargs)
        if config.provider == "anthropic":
            # Anthropic support uses its OpenAI-compatible endpoint. Tool calling
            # through this compatibility layer may not support every native feature.
            return OpenAI(
                api_key=config.resolved_api_key(),
                base_url=config.base_url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
            )
        raise ValueError(f"unsupported model provider: {config.provider}")

    def provider_model(self) -> str:
        return self.config.provider_model()

    def complete(self, messages: list[dict[str, Any]], max_completion_tokens: int) -> Any:
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
        return self.client.chat.completions.create(
            model=self.provider_model(),
            messages=messages,
            tools=tool_schemas,
            max_completion_tokens=max_completion_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
