"""Markdown presenters for UI text.

Pure functions: take data, return Markdown strings. No side effects, no emit.
Keep the UI bridge (`tab_api.py` / `app.py`) free of inline formatting.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from .agents import Agent
from .model_config import ModelConfig


def md_escape(text: object) -> str:
    """Escape values embedded in backtick code spans or inline code."""
    return str(text or "").replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|")


def table_cell(text: object) -> str:
    """Minimal escaping for a plain (non-code-span) Markdown table cell."""
    return str(text or "").replace("|", "\\|")


def format_session_time(ts: object) -> str:
    try:
        value = float(ts or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "unknown"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def skills_markdown(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_no skills found under `~/.klimt/skills`_"
    lines = [
        "## Available skills",
        "",
        "| skill | description |",
        "|---|---|",
    ]
    for s in items:
        name = md_escape(s.get("name") or "")
        desc = table_cell(s.get("description") or "(no description)")
        lines.append(f"| `/{name}` | {desc} |")
    return "\n".join(lines)


def agents_markdown(items: Iterable[Agent], model_classes: list[str]) -> str:
    lines = [
        "## Available agents",
        "",
        "| agent | mode | tools | model | description | source |",
        "|---|---|---|---|---|---|",
    ]
    for a in items:
        tools_label = ", ".join(a.tools) if a.tools else "none"
        desc = table_cell(a.description or "(no description)")
        model_label = table_cell(a.model or "(inherits parent)")
        lines.append(
            f"| `{md_escape(a.name)}` | {a.mode} | {table_cell(tools_label)} | {model_label} | {desc} | {a.source} |"
        )

    if model_classes:
        lines.extend([
            "",
            "Model classes from `~/.klimt/models.json`: "
            + ", ".join(f"`{md_escape(c)}`" for c in model_classes)
            + ". Use as the `model` argument to `agent` or as the `model:` field in an agent file.",
        ])
    return "\n".join(lines)


def sessions_markdown(sessions: list[dict[str, Any]]) -> str:
    if not sessions:
        return "_no saved sessions for this folder_"

    lines = [
        "## Sessions",
        "",
        "| # | name | model | updated | messages | inputs |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for i, s in enumerate(sessions, start=1):
        name = table_cell(s.get("name") or "")
        model = table_cell(s.get("model") or "")
        updated = format_session_time(s.get("updated"))
        messages = int(s.get("messages") or 0)
        inputs = int(s.get("inputs") or 0)
        lines.append(f"| {i} | {name} | {model} | {updated} | {messages} | {inputs} |")
    lines.extend([
        "",
        "Commands:",
        "",
        "- `/sessions resume <number|name>` — resume a session from this list, or by name.",
        "- `/sessions delete <number|name>` — delete a saved session. Deleting the active session starts a new one.",
        "- `/sessions clear confirm` — delete all saved sessions for this folder and start a new one.",
    ])
    return "\n".join(lines)


def models_markdown(configs: list[ModelConfig], current: str) -> str:
    if not configs:
        return "_no models configured; create `~/.klimt/models.json`_"

    lines = [
        "## Models",
        "",
        "| name | provider | model | current |",
        "|---|---|---|---|",
    ]
    for cfg in configs:
        marker = "yes" if cfg.name == current else ""
        lines.append(
            f"| {table_cell(cfg.name)} | {table_cell(cfg.provider)} | {table_cell(cfg.provider_model())} | {marker} |"
        )
    lines.extend(["", "_usage: `/model <name>`; use Tab to complete names._"])
    return "\n".join(lines)


def themes_markdown(themes: list[str], current: str) -> str:
    if not themes:
        return "_no themes found under `klimt/web/themes/` or `~/.klimt/themes/`_"

    lines = [
        "## Themes",
        "",
        "| name | current |",
        "|---|---|",
    ]
    for name in themes:
        marker = "yes" if name == current else ""
        lines.append(f"| `{table_cell(name)}` | {marker} |")
    lines.extend(["", "_usage: `/theme <name>`; use Tab to complete names._"])
    return "\n".join(lines)


def unknown_choice_markdown(kind: str, requested: str, choices: list[str], empty_hint: str = "_none_") -> str:
    """Render an '_unknown <kind>: `<requested>`_' message with a choice list."""
    if choices:
        formatted = ", ".join(f"`{md_escape(c)}`" for c in choices)
    else:
        formatted = empty_hint
    return (
        f"_unknown {kind}: `{md_escape(requested)}`_\n\n"
        f"{_choice_header(kind)}: {formatted}"
    )


def _choice_header(kind: str) -> str:
    if kind == "model":
        return "Configured choices"
    return "Available choices"
