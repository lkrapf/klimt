"""ChatSession + system-prompt assembly helpers.

Lives outside app.py so it can be reused by the bridge (tab_api.py) and
slash-command handlers (command_handlers.py) without circular imports.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import agents, prompt, skills, tools
from .api import ChatSession
from .model_config import default_model_name, resolve_model_config


def _vision_for(model: str) -> bool:
    """Return True when the active model is vision-capable."""
    try:
        return resolve_model_config(model).vision
    except (KeyError, RuntimeError):
        return False


_NO_VISION_NOTE = (
    "\n\n> **Note:** the active model is not vision-capable. "
    "The `visual` tool is unavailable in this session. "
    "Switch to a vision-capable model to use it."
)


def build_system_prompt(cwd: str | None = None, model: str | None = None) -> str:
    catalog = agents.build_catalog_manifest(agents.list_agents(cwd))
    has_vision = _vision_for(model or "")
    # For the system-prompt manifest, omit `visual` from the listed tools when
    # the model can't use it — no point advertising a dead tool. The note below
    # makes the reason explicit so the model can tell the user clearly.
    manifest_schemas = (
        list(tools.SCHEMAS)
        if has_vision
        else [s for s in tools.SCHEMAS if s.get("function", {}).get("name") != "visual"]
    )
    base = prompt.build_system_prompt(
        manifest_schemas,
        skills.list_skills(),
        cwd=cwd,
        agent_manifest=catalog,
    )
    if not has_vision:
        base = base.rstrip("\n") + _NO_VISION_NOTE + "\n"
    return base


def new_session(cwd: str | None = None, model: str | None = None) -> ChatSession:
    cwd = str(Path(cwd or os.getcwd()).expanduser().resolve())
    chosen = (model or "").strip() or default_model_name()
    # If the caller passes a model that no longer resolves (e.g. removed from
    # models.json between sessions), fall back rather than crash.
    try:
        resolve_model_config(chosen)
    except KeyError:
        chosen = default_model_name()
    return ChatSession(
        model=chosen,
        system=build_system_prompt(cwd, chosen),
        cwd=cwd,
    )
