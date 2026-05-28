"""Model classes: parsing, listing, and class-based resolution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from klimt import model_config


def _write_models(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"models": items}), encoding="utf-8")


@pytest.fixture
def fake_models(tmp_path, monkeypatch):
    p = tmp_path / "models.json"
    monkeypatch.setattr(model_config, "MODELS_PATH", p)
    return p


def test_classes_parse_list(fake_models):
    _write_models(fake_models, [
        {"name": "opus-az", "provider": "azure", "api_key_env": "K", "classes": ["opus", "heavy"]},
    ])
    cfgs = model_config.list_model_configs()
    assert cfgs[0].classes == ("opus", "heavy")


def test_classes_parse_string(fake_models):
    _write_models(fake_models, [
        {"name": "sonnet-az", "provider": "azure", "api_key_env": "K", "classes": "sonnet, balanced"},
    ])
    cfgs = model_config.list_model_configs()
    assert cfgs[0].classes == ("sonnet", "balanced")


def test_classes_empty_when_missing(fake_models):
    _write_models(fake_models, [
        {"name": "plain", "provider": "azure", "api_key_env": "K"},
    ])
    assert model_config.list_model_configs()[0].classes == ()


def test_list_model_classes_dedupes(fake_models):
    _write_models(fake_models, [
        {"name": "a", "provider": "azure", "api_key_env": "K", "classes": ["heavy", "opus"]},
        {"name": "b", "provider": "azure", "api_key_env": "K", "classes": ["heavy", "balanced"]},
    ])
    classes = model_config.list_model_classes()
    assert classes == ["heavy", "opus", "balanced"]


def test_list_model_classes_excludes_name_collisions(fake_models):
    """A class named after a model name would be ambiguous; drop it from the class list."""
    _write_models(fake_models, [
        {"name": "opus", "provider": "azure", "api_key_env": "K", "classes": ["opus"]},
    ])
    assert model_config.list_model_classes() == []


def test_resolve_by_name_wins_over_class(fake_models):
    _write_models(fake_models, [
        {"name": "opus-a", "provider": "azure", "api_key_env": "K", "classes": ["opus"]},
        {"name": "opus", "provider": "azure", "api_key_env": "K"},  # bare name 'opus'
    ])
    cfg = model_config.resolve_model_config("opus")
    assert cfg.name == "opus"


def test_resolve_by_class_first_match(fake_models):
    _write_models(fake_models, [
        {"name": "opus-first", "provider": "azure", "api_key_env": "K", "classes": ["opus"]},
        {"name": "opus-second", "provider": "azure", "api_key_env": "K", "classes": ["opus"]},
    ])
    cfg = model_config.resolve_model_config("opus")
    assert cfg.name == "opus-first"


def test_resolve_unknown_class_raises(fake_models):
    _write_models(fake_models, [
        {"name": "x", "provider": "azure", "api_key_env": "K", "classes": ["heavy"]},
    ])
    with pytest.raises(KeyError):
        model_config.resolve_model_config("not-a-thing")
