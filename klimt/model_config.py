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
DEFAULT_MAX_COMPLETION_TOKENS = 4096


@dataclass(frozen=True)
class ModelConfig:
    """Resolved model endpoint config.

    `name` is the user-facing name accepted by `/model`. `model` is the provider-side model
    or Azure deployment sent to the API.
    """

    name: str
    provider: str = "azure"
    model: str = ""
    base_url: str = ""
    api_version: str = ""
    api_key_env: str = ""
    context_window: int = 0
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS
    thinking_budget_tokens: int = 0
    vision: bool = False
    classes: tuple[str, ...] = ()

    def provider_model(self) -> str:
        return self.model or self.name

    def resolved_api_key(self) -> str:
        if self.api_key_env:
            return os.environ[self.api_key_env]
        if self.provider == "ollama":
            return "ollama"
        raise ValueError(f"model {self.name!r} requires api_key_env")


def _item_to_config(item: Any) -> ModelConfig | None:
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

    try:
        context_window = int(item.get("context_window") or 0)
    except (TypeError, ValueError):
        context_window = 0

    try:
        max_completion_tokens = int(item.get("max_completion_tokens") or item.get("max_tokens") or DEFAULT_MAX_COMPLETION_TOKENS)
    except (TypeError, ValueError):
        max_completion_tokens = DEFAULT_MAX_COMPLETION_TOKENS

    try:
        thinking_budget_tokens = int(item.get("thinking_budget_tokens") or item.get("thinking_budget") or 0)
    except (TypeError, ValueError):
        thinking_budget_tokens = 0

    classes = _parse_classes(item.get("classes") or item.get("class"))
    vision = bool(item.get("vision"))

    return ModelConfig(
        name=name,
        provider=provider,
        model=model or name,
        base_url=str(item.get("base_url") or item.get("endpoint") or "").strip(),
        api_version=str(item.get("api_version") or "").strip(),
        api_key_env=str(item.get("api_key_env") or "").strip(),
        context_window=max(0, context_window),
        max_completion_tokens=max(1, max_completion_tokens),
        thinking_budget_tokens=max(0, thinking_budget_tokens),
        vision=vision,
        classes=classes,
    )


def _parse_classes(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def _load_models_doc() -> Any:
    try:
        return json.loads(MODELS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return None


def list_model_configs() -> list[ModelConfig]:
    data = _load_models_doc()
    if data is None:
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


def _configured_default() -> str:
    """Top-level `default` field in models.json, if any."""
    data = _load_models_doc()
    if not isinstance(data, dict):
        return ""
    return str(data.get("default") or "").strip()


def list_model_names() -> list[str]:
    return [m.name for m in list_model_configs()]


def list_model_classes() -> list[str]:
    """Return all class names declared by any configured model, deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for cfg in list_model_configs():
        for cls in cfg.classes:
            if cls in seen or cls in {cfg.name, cfg.model}:
                continue
            seen.add(cls)
            out.append(cls)
    return out


def default_model_name() -> str:
    configured = list_model_configs()
    if not configured:
        raise RuntimeError(f"no models configured; create {MODELS_PATH}")

    env = os.environ.get("KLIMT_MODEL", "").strip()
    if env:
        resolved = _match_name(env, configured)
        if resolved:
            return resolved
        raise KeyError(f"KLIMT_MODEL={env!r} is not configured in {MODELS_PATH}")

    configured_default = _configured_default()
    if configured_default:
        resolved = _match_name(configured_default, configured)
        if resolved:
            return resolved
        # Bad config: fall through to the first entry rather than crash the app.

    return configured[0].name


def _match_name(requested: str, configs: list[ModelConfig]) -> str:
    for cfg in configs:
        if requested in {cfg.name, cfg.model}:
            return cfg.name
    for cfg in configs:
        if requested in cfg.classes:
            return cfg.name
    return ""


def resolve_model_config(name: str) -> ModelConfig:
    """Resolve a model by exact name, provider model string, or declared class.

    Class resolution returns the first configured model that lists the class,
    in config-file order. Exact name/model matches always win over class matches.
    """
    requested = (name or "").strip() or default_model_name()
    configs = list_model_configs()
    for cfg in configs:
        if requested in {cfg.name, cfg.model}:
            return cfg
    for cfg in configs:
        if requested in cfg.classes:
            return cfg
    raise KeyError(requested)
