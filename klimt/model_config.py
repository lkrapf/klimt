"""Model endpoint configuration for Klimt.

Reads ~/.klimt/models.json. A model entry is a selectable endpoint, not just a
model string: provider, base URL, deployment/model name, and auth can differ.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODELS_PATH = Path.home() / ".klimt" / "models.json"


@dataclass(frozen=True)
class ModelConfig:
    """Resolved model endpoint config.

    `name` is the selector shown in `/model`. `model` is the provider-side model
    or Azure deployment sent to the API.
    """

    name: str
    provider: str = "azure"
    model: str = ""
    base_url: str = ""
    api_version: str = ""
    api_key: str = ""
    api_key_env: str = ""

    def provider_model(self) -> str:
        return self.model or self.name

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ[self.api_key_env]
        if self.provider == "anthropic":
            return os.environ["ANTHROPIC_API_KEY"]
        return os.environ.get("OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY") or "ollama"


def _item_to_config(item: Any) -> ModelConfig | None:
    if isinstance(item, str):
        name = item.strip()
        return ModelConfig(name=name, model=name) if name else None
    if not isinstance(item, dict):
        return None

    provider = str(item.get("provider") or item.get("type") or "azure").strip().lower()
    if provider in {"azure-openai", "azure_oai", "azure-oai"}:
        provider = "azure"
    if provider in {"openai-compatible", "openai_compatible"}:
        provider = "openai"

    model = str(item.get("model") or item.get("deployment") or "").strip()
    name = str(item.get("name") or model or item.get("deployment") or "").strip()
    if not name:
        return None

    return ModelConfig(
        name=name,
        provider=provider,
        model=model or name,
        base_url=str(item.get("base_url") or item.get("endpoint") or "").strip(),
        api_version=str(item.get("api_version") or "").strip(),
        api_key=str(item.get("api_key") or "").strip(),
        api_key_env=str(item.get("api_key_env") or "").strip(),
    )


def list_model_configs() -> list[ModelConfig]:
    try:
        data = json.loads(MODELS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = []
    except json.JSONDecodeError:
        return []

    raw_items = data.get("models", []) if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        return []

    seen: set[str] = set()
    out: list[ModelConfig] = []
    for item in raw_items:
        cfg = _item_to_config(item)
        if cfg and cfg.name not in seen:
            seen.add(cfg.name)
            out.append(cfg)
    return out


def list_model_names() -> list[str]:
    return [m.name for m in list_model_configs()]


def default_model_name() -> str:
    configured = list_model_configs()
    return os.environ.get("KLIMT_MODEL") or (configured[0].name if configured else os.environ["AZURE_OPENAI_DEPLOYMENT"])


def resolve_model_config(name: str) -> ModelConfig:
    requested = (name or "").strip() or default_model_name()
    for cfg in list_model_configs():
        if requested in {cfg.name, cfg.model}:
            return cfg

    # Backward-compatible env-only Azure config.
    deployment = os.environ.get("KLIMT_MODEL") or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if requested == deployment:
        return ModelConfig(
            name=requested,
            provider="azure",
            model=requested,
            base_url=os.environ.get("AZURE_OPENAI_BASE_URL", ""),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            api_key_env="AZURE_OPENAI_API_KEY",
        )

    raise KeyError(requested)
