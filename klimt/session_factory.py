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


def build_system_prompt(cwd: str | None = None) -> str:
    catalog = agents.build_catalog_manifest(agents.list_agents(cwd))
    return prompt.build_system_prompt(
        tools.SCHEMAS,
        skills.list_skills(),
        cwd=cwd,
        agent_manifest=catalog,
    )


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
        system=build_system_prompt(cwd),
        cwd=cwd,
    )
