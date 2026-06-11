"""Lossy compaction of older chat history.

ChatSession.compact() delegates to this module so that the policy (cutoff,
chunking, two-pass merge, stamped replacement message) is testable on its
own, and api.py stays focused on session orchestration.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from .context_usage import estimate_tokens
from .tool_impl import visual as _visual

# Marker prefix on the replacement user message. ChatSession's replay code
# checks for this prefix to render the entry as a system note rather than a
# real user turn.
COMPACTED_NOTE_PREFIX = "[Klimt compacted prior context"


# The compactor LLM gets this as its system prompt. It is large by design;
# the goal is preserving structure and provenance, not brevity.
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

CompactCall = Callable[[str], str]


@dataclass(frozen=True)
class CompactionResult:
    history: list[Dict[str, Any]]
    summary: str  # Markdown human summary of what changed.


def compact_history(
    history: List[Dict[str, Any]],
    compact_text: CompactCall,
    keep_recent: int = 8,
    chunk_budget: int | None = None,
) -> CompactionResult:
    """Replace the oldest portion of `history` with a structured summary.

    `keep_recent` raw messages stay at the tail. The cutoff is moved left to
    avoid splitting an assistant turn from its tool-result follow-ups.

    `compact_text` is called once per chunk and once more to merge multiple
    chunk summaries. The caller decides which model runs it.

    Returns a CompactionResult with the new history list and a short status
    line ("compacted N messages..." or "nothing to compact").
    """
    keep_recent = max(0, min(int(keep_recent), len(history)))
    cutoff = len(history) - keep_recent
    while 0 < cutoff < len(history) and history[cutoff].get("role") == "tool":
        cutoff -= 1

    old = history[:cutoff]
    # Usage metadata on retained assistant messages describes the pre-compaction
    # request context. Once old history is replaced by a summary, those totals
    # are stale; drop them and let context_usage() estimate the new history.
    recent = [
        {k: v for k, v in msg.items() if k != "usage"}
        for msg in history[cutoff:]
    ]
    if not old:
        return CompactionResult(history=list(history), summary="nothing to compact")

    if chunk_budget is None:
        chunk_budget = int(os.environ.get("KLIMT_COMPACTION_CHUNK_TOKENS", "24000"))

    chunks = chunk_messages(old, chunk_budget)
    summaries: list[str] = []
    offset = 0
    for n, chunk in enumerate(chunks, start=1):
        transcript = "\n\n".join(
            _message_for_compaction(msg, offset + i)
            for i, msg in enumerate(chunk)
        )
        offset += len(chunk)
        summaries.append(compact_text(
            f"Compact transcript chunk {n}/{len(chunks)}.\n\n{transcript}"
        ))

    if len(summaries) == 1:
        compacted = summaries[0]
    else:
        joined = "\n\n---\n\n".join(
            f"# Chunk summary {i}\n\n{summary}"
            for i, summary in enumerate(summaries, start=1)
        )
        compacted = compact_text(
            "Merge these chunk summaries into one coherent compacted state. "
            "Remove duplication, preserve uncertainty and provenance.\n\n"
            + joined
        )

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    note = (
        f"{COMPACTED_NOTE_PREFIX} at {stamp}.]\n\n"
        "The raw transcript before this message was replaced by the "
        "structured state below. Treat it as context, not as a new user task.\n\n"
        f"{compacted.strip()}"
    )
    new_history = [{"role": "user", "content": note}, *recent]
    summary = (
        f"compacted {len(old)} messages into {len(note)} chars; "
        f"kept {len(recent)} recent messages"
    )
    return CompactionResult(history=new_history, summary=summary)


def chunk_messages(messages: List[Dict[str, Any]], max_tokens: int) -> List[List[Dict[str, Any]]]:
    """Pack messages into chunks under `max_tokens` (chars/4) each."""
    chunks: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_tokens = 0
    for msg in messages:
        tokens = max(1, estimate_tokens(msg))
        if current and current_tokens + tokens > max_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(msg)
        current_tokens += tokens
    if current:
        chunks.append(current)
    return chunks


def _message_for_compaction(msg: Dict[str, Any], index: int) -> str:
    """Stable, readable transcript entry for compaction.

    Image envelopes (pasted images or visual tool results) are replaced by
    their short text summary so the compaction model never sees raw base64.
    """
    clean = {k: v for k, v in msg.items() if k != "usage"}
    content = clean.get("content")
    envelope = _visual.parse_envelope(content)
    if envelope is not None:
        clean = dict(clean)
        clean["content"] = _visual.envelope_summary(envelope)
    return f"<message index={index} role={clean.get('role', '')}>\n" + json.dumps(
        clean,
        ensure_ascii=False,
        indent=2,
    ) + "\n</message>"
