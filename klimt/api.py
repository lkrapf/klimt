"""Streaming wrapper around Azure OpenAI Chat Completions, with tool calling."""
from __future__ import annotations

import contextlib
import json
import os
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from . import tools
from .model_config import ModelConfig, resolve_model_config
from .providers import ChatProvider
from .session_store import DEFAULT_SESSION, UNTITLED_PREFIX, SessionStore, random_session_name, title_from_prompt

Event = Dict[str, Any]
Emit = Callable[[Event], None]


COMPACTION_PROMPT = """You compact old chat history into durable working state.

This is lossy compression. Preserve only information likely to matter later, but
be specific where specificity matters. Do not invent facts. Distinguish user
facts, assistant assumptions, decisions, constraints, and unresolved questions.

Return Markdown with this shape:

# Compacted context

## Current objective
- ...

## Active constraints and preferences
- ...

## Known facts
- ...

## Decisions / rejected options
- ...

## Open questions / risks
- ...

## References and provenance
- Mention important files, URLs, commands, tool outputs, or message ranges when relevant.

## Continuation notes
- What the assistant should keep in mind for the next turn.

If a section has nothing useful, write `- none`.
"""


def _message_for_compaction(msg: Dict[str, Any], index: int) -> str:
    """Stable, readable transcript entry for compaction."""
    clean = {k: v for k, v in msg.items() if k != "usage"}
    return f"<message index={index} role={clean.get('role', '')}>\n" + json.dumps(
        clean,
        ensure_ascii=False,
        indent=2,
    ) + "\n</message>"


def _chunk_messages(messages: List[Dict[str, Any]], max_tokens: int) -> List[List[Dict[str, Any]]]:
    chunks: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_tokens = 0
    for msg in messages:
        tokens = max(1, _estimate_tokens(msg))
        if current and current_tokens + tokens > max_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(msg)
        current_tokens += tokens
    if current:
        chunks.append(current)
    return chunks


def _usage_dict(usage: Any) -> Dict[str, int]:
    """Normalize OpenAI usage into the small shape we persist."""
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details else 0
    return {
        "input": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output": int(getattr(usage, "completion_tokens", 0) or 0),
        "cacheRead": int(cached or 0),
        "cacheWrite": 0,
        "totalTokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _context_tokens_from_usage(usage: Dict[str, Any]) -> int:
    return int(usage.get("totalTokens") or (
        int(usage.get("input") or 0)
        + int(usage.get("output") or 0)
        + int(usage.get("cacheRead") or 0)
        + int(usage.get("cacheWrite") or 0)
    ))


def _estimate_tokens(msg: Dict[str, Any]) -> int:
    """Cheap chars/4 heuristic, matching Pi's fallback strategy."""
    chars = 0
    content = msg.get("content")
    if isinstance(content, str):
        chars += len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    chars += len(block.get("text") or "")
                elif block.get("type") == "image":
                    chars += 4800
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        chars += len(fn.get("name") or "") + len(fn.get("arguments") or "")
    return max(0, (chars + 3) // 4)


def _estimate_context_tokens(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    last_usage_index = None
    last_usage = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("usage"):
            last_usage_index = i
            last_usage = msg["usage"]
            break

    if last_usage_index is None:
        estimated = sum(_estimate_tokens(m) for m in messages)
        return {
            "tokens": estimated,
            "usageTokens": 0,
            "trailingTokens": estimated,
            "lastUsageIndex": None,
        }

    usage_tokens = _context_tokens_from_usage(last_usage)
    trailing = sum(_estimate_tokens(m) for m in messages[last_usage_index + 1:])
    return {
        "tokens": usage_tokens + trailing,
        "usageTokens": usage_tokens,
        "trailingTokens": trailing,
        "lastUsageIndex": last_usage_index,
    }


@dataclass
class ChatSession:
    # For Azure, "model" is the deployment name. For other providers, it is the
    # selector name from ~/.klimt/models.json.
    model: str
    system: str
    max_tokens: int = 4096
    context_window: int = 0
    history: List[Dict] = field(default_factory=list)
    session_name: str = field(default_factory=random_session_name)
    input_history: List[str] = field(default_factory=list)
    store: SessionStore = field(default_factory=SessionStore, repr=False)
    _provider: ChatProvider = field(default=None, init=False, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _active_stream: Any = field(default=None, init=False, repr=False)
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
            stream = self._active_stream
        close = getattr(stream, "close", None)
        if close:
            with contextlib.suppress(Exception):
                close()

    def abandon(self) -> None:
        """Prevent an obsolete worker from writing this session to disk."""
        self._abandoned = True
        self.interrupt()

    def reset(self) -> None:
        self.history.clear()
        self.input_history.clear()
        self.persist()

    def persist(self) -> None:
        if self._abandoned:
            return
        self.store.save(self.session_name, self.history, self.input_history, self.model)

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
        self.history = data.get("history") or []
        self.input_history = data.get("input_history") or []
        saved_model = (data.get("model") or "").strip()
        if saved_model:
            self.model = saved_model
            self.reload_client()
        return True

    def rename_session(self, name: str) -> None:
        old = self.session_name
        wanted = name.strip() or random_session_name()
        self.session_name = self.store.unique_name(wanted) if wanted != old else old
        self.persist()
        if old != self.session_name:
            self.store.delete(old)

    def list_sessions(self) -> List[Dict[str, Any]]:
        return self.store.list()

    def context_usage(self) -> Dict[str, Any]:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.extend(self.history)
        estimate = _estimate_context_tokens(messages)

        if self.context_window <= 0:
            return {
                "tokens": estimate["tokens"],
                "contextWindow": 0,
                "percent": None,
            }

        percent = (estimate["tokens"] / self.context_window) * 100
        return {
            "tokens": estimate["tokens"],
            "contextWindow": self.context_window,
            "percent": percent,
        }

    def compact(self, keep_recent: int = 8) -> str:
        """Compact older history into a structured state note.

        Keeps the most recent `keep_recent` messages raw because the active turn
        boundary is where lossy summaries hurt most. Tool-call adjacency is also
        preserved by moving the cutoff left if needed.
        """
        keep_recent = max(0, min(int(keep_recent), len(self.history)))
        cutoff = len(self.history) - keep_recent
        while 0 < cutoff < len(self.history) and self.history[cutoff].get("role") == "tool":
            cutoff -= 1

        old = self.history[:cutoff]
        # Usage metadata on retained assistant messages describes the pre-compaction
        # request context. Once old history is replaced by a summary, those totals
        # are stale, so drop them and let context_usage() estimate the new history.
        recent = [
            {k: v for k, v in msg.items() if k != "usage"}
            for msg in self.history[cutoff:]
        ]
        if not old:
            return "nothing to compact"

        chunk_budget = int(os.environ.get("KLIMT_COMPACTION_CHUNK_TOKENS", "24000"))
        summaries: List[str] = []
        chunks = _chunk_messages(old, chunk_budget)
        offset = 0
        for n, chunk in enumerate(chunks, start=1):
            transcript = "\n\n".join(
                _message_for_compaction(msg, offset + i)
                for i, msg in enumerate(chunk)
            )
            offset += len(chunk)
            summaries.append(self._compact_text(
                f"Compact transcript chunk {n}/{len(chunks)}.\n\n{transcript}"
            ))

        if len(summaries) == 1:
            compacted = summaries[0]
        else:
            joined = "\n\n---\n\n".join(
                f"# Chunk summary {i}\n\n{summary}"
                for i, summary in enumerate(summaries, start=1)
            )
            compacted = self._compact_text(
                "Merge these chunk summaries into one coherent compacted state. "
                "Remove duplication, preserve uncertainty and provenance.\n\n"
                + joined
            )

        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        note = (
            f"[Klimt compacted prior context at {stamp}.]\n\n"
            "The raw transcript before this message was replaced by the "
            "structured state below. Treat it as context, not as a new user task.\n\n"
            f"{compacted.strip()}"
        )
        self.history = [{"role": "user", "content": note}, *recent]
        self.persist()
        return f"compacted {len(old)} messages into {len(note)} chars; kept {len(recent)} recent messages"

    def _compact_text(self, text: str) -> str:
        response = self._provider.complete(
            messages=[
                {"role": "system", "content": COMPACTION_PROMPT},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=self.max_tokens,
        )
        content = response.choices[0].message.content if response.choices else None
        return (content or "").strip() or "# Compacted context\n\n- compaction returned no content"

    def reload_client(self) -> None:
        self._provider = ChatProvider(self.model_config())

    def stream(self, user_text: str, emit: Emit) -> None:
        """Push events for one user turn.

        Event shapes:
          {type: 'text_start'}
          {type: 'text_delta', content: str}
          {type: 'text_end'}
          {type: 'text', content: str}       atomic markdown message
          {type: 'tool', name, args, result}
          {type: 'error', message: str}
        """
        self._cancel.clear()
        self.history.append({"role": "user", "content": user_text})

        while True:
            if self._cancel.is_set():
                emit({"type": "error", "message": "interrupted"})
                return

            stream = self._provider.stream(
                messages=[
                    {"role": "system", "content": self.system},
                    *[{k: v for k, v in m.items() if k != "usage"} for m in self.history],
                ],
                tool_schemas=tools.SCHEMAS,
                max_completion_tokens=self.max_tokens,
            )
            with self._active_lock:
                self._active_stream = stream

            content_buf: List[str] = []
            tool_calls: Dict[int, Dict[str, str]] = {}
            text_open = False
            usage = None

            try:
                for chunk in stream:
                    if self._cancel.is_set():
                        break
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta is None:
                        continue

                    if delta.content:
                        if not text_open:
                            emit({"type": "text_start"})
                            text_open = True
                        emit({"type": "text_delta", "content": delta.content})
                        content_buf.append(delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            slot = tool_calls.setdefault(
                                tc.index, {"id": "", "name": "", "args": ""}
                            )
                            if tc.id:
                                slot["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    slot["name"] = tc.function.name
                                if tc.function.arguments:
                                    slot["args"] += tc.function.arguments
            except Exception:
                if not self._cancel.is_set():
                    raise
            finally:
                with self._active_lock:
                    if self._active_stream is stream:
                        self._active_stream = None

            if self._cancel.is_set():
                if text_open:
                    emit({"type": "text_end"})
                emit({"type": "error", "message": "interrupted"})
                return

            if text_open:
                emit({"type": "text_end"})

            full_text = "".join(content_buf)
            assistant_entry: Dict[str, Any] = {
                "role": "assistant",
                "content": full_text or None,
            }
            if usage:
                assistant_entry["usage"] = _usage_dict(usage)
            if tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": v["id"],
                        "type": "function",
                        "function": {"name": v["name"], "arguments": v["args"]},
                    }
                    for _, v in sorted(tool_calls.items())
                ]
            self.history.append(assistant_entry)

            if not tool_calls:
                self.persist()
                return

            for _, v in sorted(tool_calls.items()):
                if self._cancel.is_set():
                    emit({"type": "error", "message": "interrupted"})
                    return
                try:
                    args = json.loads(v["args"] or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": v["args"]}
                result = tools.run(v["name"], args, self._cancel)
                emit({
                    "type": "tool",
                    "name": v["name"],
                    "args": args,
                    "result": result,
                })
                if self._cancel.is_set():
                    emit({"type": "error", "message": "interrupted"})
                    return
                self.history.append({
                    "role": "tool",
                    "tool_call_id": v["id"],
                    "content": result,
                })
                self.persist()
