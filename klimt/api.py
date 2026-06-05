"""ChatSession: turn loop, persistence, model lifecycle.

Kept deliberately thin. Context-window math lives in klimt.context_usage;
lossy compaction lives in klimt.compaction; tool dispatch and turn streaming
live in klimt.runner / klimt.tool_runner.
"""
from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import agent_runner, agents as agents_mod, tools as tools_mod
from . import compaction as compaction_mod
from . import context_usage as context_usage_mod
from .api_types import Emit
from .model_config import ModelConfig, list_model_classes, list_model_names, resolve_model_config
from .providers import ChatProvider
from .runner import run_turn
from .session_store import DEFAULT_SESSION, UNTITLED_PREFIX, SessionStore, random_session_name, title_from_prompt


@dataclass
class ChatSession:
    # For Azure, "model" is the deployment name. For other providers, it is the
    # selector name from ~/.klimt/models.json.
    model: str
    system: str
    max_tokens: int = 4096
    history: List[Dict] = field(default_factory=list)
    session_name: str = field(default_factory=random_session_name)
    input_history: List[str] = field(default_factory=list)
    cwd: str = field(default_factory=lambda: str(os.getcwd()))
    # A session is "kept" once the user deliberately saves it via /save, or
    # resumes one from disk. Until then it lives only in memory: persist() is a
    # no-op so casual scratch tabs never litter the session store.
    kept: bool = False
    store: SessionStore = field(default_factory=SessionStore, repr=False)
    _provider: ChatProvider = field(default=None, init=False, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _active_stream_ref: Dict[str, Any] = field(default_factory=lambda: {"stream": None}, init=False, repr=False)
    _active_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _abandoned: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.reload_client()

    def model_config(self) -> ModelConfig:
        return resolve_model_config(self.model)

    def provider_model(self) -> str:
        return self._provider.provider_model()

    def interrupt(self) -> None:
        """Ask the current request/tool to stop as soon as possible."""
        self._cancel.set()
        with self._active_lock:
            stream = self._active_stream_ref.get("stream")
        close = getattr(stream, "close", None)
        if close:
            with contextlib.suppress(Exception):
                close()

    def abandon(self) -> None:
        """Prevent an obsolete worker from writing this session to disk."""
        self._abandoned = True
        self.interrupt()

    def persist(self) -> None:
        if self._abandoned or not self.kept:
            return
        self.store.save(self.session_name, self.history, self.input_history, self.model, self.cwd)

    def keep(self, name: str | None = None) -> None:
        """Promote an in-memory session to the on-disk store.

        With no name, snapshots under the current (possibly auto-titled) name.
        """
        wanted = (name or self.session_name).strip() or random_session_name()
        if wanted != self.session_name or not self.kept:
            self.session_name = self.store.unique_name(wanted)
        self.kept = True
        self.persist()

    def remember_input(self, text: str) -> None:
        text = text.strip()
        if text and (not self.input_history or self.input_history[-1] != text):
            self.input_history.append(text)

    def maybe_title_from_first_input(self, text: str) -> bool:
        """Rename the temporary session after the first meaningful prompt."""
        text = text.strip()
        if not text or text.startswith(("/", "!")):
            return False
        if not self.session_name.startswith(UNTITLED_PREFIX):
            return False
        wanted = title_from_prompt(text)
        self.session_name = self.store.unique_name(wanted)
        return True

    def load_session(self, name: str = DEFAULT_SESSION) -> bool:
        data = self.store.load(name)
        if not data:
            return False
        self.session_name = data.get("name") or name or DEFAULT_SESSION
        self.kept = True
        self.history = data.get("history") or []
        self.input_history = data.get("input_history") or []
        saved_model = (data.get("model") or "").strip()
        saved_cwd = (data.get("cwd") or "").strip()
        if saved_cwd:
            self.cwd = str(Path(saved_cwd).expanduser().resolve())
            self.store = self.store.for_folder(self.cwd)
        if saved_model:
            self.model = saved_model
            self.reload_client()
        return True

    def rename_session(self, name: str) -> None:
        old = self.session_name
        was_kept = self.kept
        wanted = name.strip() or random_session_name()
        self.session_name = self.store.unique_name(wanted) if wanted != old else old
        self.kept = True
        self.persist()
        if was_kept and old != self.session_name:
            self.store.delete(old)

    def list_sessions(self) -> List[Dict[str, Any]]:
        return self.store.list()

    def context_usage(self) -> Dict[str, Any]:
        return context_usage_mod.context_usage(
            self.system,
            self.history,
            self.model_config().context_window,
        )

    def compact(self, keep_recent: int = 8) -> str:
        """Compact older history into a structured state note.

        Keeps the most recent `keep_recent` messages raw because the active turn
        boundary is where lossy summaries hurt most. Tool-call adjacency is also
        preserved by moving the cutoff left if needed.
        """
        result = compaction_mod.compact_history(
            history=self.history,
            compact_text=self._compact_text,
            keep_recent=keep_recent,
        )
        if result.history is self.history or result.summary == "nothing to compact":
            return result.summary
        self.history = result.history
        self.persist()
        return result.summary

    def _compact_text(self, text: str) -> str:
        response = self._provider.complete(
            messages=[
                {"role": "system", "content": compaction_mod.COMPACTION_PROMPT},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=self.max_tokens,
        )
        content = response.choices[0].message.content if response.choices else None
        return (content or "").strip() or "# Compacted context\n\n- compaction returned no content"

    def reload_client(self) -> None:
        config = self.model_config()
        self.max_tokens = config.max_completion_tokens or 4096
        self._provider = ChatProvider(config)

    def _tool_schemas(self) -> list[dict[str, Any]]:
        schemas = list(tools_mod.SCHEMAS)
        available = agents_mod.list_agents(self.cwd)
        if available:
            schemas.append(_agent_tool_schema(available))
        return schemas

    def _is_read_only(self, name: str, args: Dict[str, Any]) -> bool:
        if name in tools_mod.READ_ONLY_TOOLS:
            return True
        if name == "agent":
            target = (args.get("name") or "").strip() or "read-only"
            agent = agents_mod.find_agent(target, self.cwd)
            return bool(agent and agent.mode != "full")
        return False

    def _agent_dispatch(self, name: str, args: Dict[str, Any]) -> str:
        if name != "agent":
            return f"error: unknown agent dispatch {name!r}"
        target = (args.get("name") or "").strip() or "read-only"
        task = (args.get("prompt") or args.get("task") or "").strip()
        if not task:
            return "error: agent invocation requires a non-empty `prompt`"
        agent = agents_mod.find_agent(target, self.cwd)
        if not agent:
            return f"error: unknown agent {target!r}; use /agents to list available subagents"
        transcripts_dir = self.store.root / f"{self.session_name}.agents"
        model_override = (args.get("model") or "").strip() or None
        inv = agent_runner.AgentInvocation(
            agent=agent,
            task=task,
            parent_model=self.model,
            cwd=self.cwd,
            cancel=self._cancel,
            transcripts_dir=transcripts_dir,
            model_override=model_override,
        )
        return agent_runner.run_agent(inv)

    def stream(self, user_text: str, emit: Emit) -> None:
        """Push events for one user turn.

        Event shapes:
          {type: 'text_start'}
          {type: 'text_delta', content: str}
          {type: 'text_end'}
          {type: 'text', content: str}       atomic markdown message
          {type: 'tool_start', id, name, args}
          {type: 'tool', id, name, args, result}
          {type: 'error', message: str}
        """
        self._cancel.clear()
        self.history.append({"role": "user", "content": user_text})
        completed = run_turn(
            provider=self._provider,
            system=self.system,
            history=self.history,
            max_tokens=self.max_tokens,
            cancel=self._cancel,
            active_lock=self._active_lock,
            active_stream_ref=self._active_stream_ref,
            emit=emit,
            cwd=self.cwd,
            tool_schemas=self._tool_schemas(),
            agent_dispatch=self._agent_dispatch,
            is_read_only=self._is_read_only,
        )
        if completed:
            self.persist()


def _agent_tool_schema(available: list[agents_mod.Agent]) -> dict[str, Any]:
    names = [a.name for a in available]
    model_choices = list_model_names() + list_model_classes()
    model_property: Dict[str, Any] = {
        "type": "string",
        "description": (
            "Optional model override for this invocation. Accepts a configured "
            "model name or a model class declared in ~/.klimt/models.json. "
            "Class names resolve to the first model that declares them. Omit "
            "to use the agent's configured model, or the parent model if the "
            "agent has none."
        ),
    }
    if model_choices:
        model_property["enum"] = model_choices
    return {
        "type": "function",
        "function": {
            "name": "agent",
            "description": (
                "Delegate a focused task to a subagent. The subagent runs with "
                "its own scoped tools and a turn budget, then returns a Markdown "
                "report. Use `/agents` to inspect available agents. Subagents "
                "do not see the parent conversation history, so include all "
                "context the agent needs in `prompt`. Use `model` to route "
                "heavy reasoning to a stronger model and grunt work to a cheaper one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": f"Subagent to invoke. One of: {', '.join(names)}.",
                        "enum": names,
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Task description and any context the subagent needs. Be specific.",
                    },
                    "model": model_property,
                },
                "required": ["name", "prompt"],
            },
        },
    }
