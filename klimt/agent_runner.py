"""Subagent execution: scoped prompt, scoped tool allowlist, sidecar transcript.

A subagent is a fresh model loop run on behalf of the parent. It has:

- Its own system prompt assembled from kernel + filtered tool manifest +
  project AGENTS.md + filtered skill manifest + the agent's own body.
  No parent chat history. No global ~/.klimt/AGENTS.md.
- Its own tool allowlist (the agent's `tools` field). Mutating tools run
  sequentially; read-only tools currently run sequentially within the
  subagent too (parallelism inside subagents can come later).
- A turn cap (`max_turns`, default 3). One turn = one assistant message
  plus any tool calls it issues plus their results.
- A sidecar Markdown transcript written under the parent session's
  `<session>.agents/` directory.

The subagent shares the parent's cancel event and cwd. There is no nested
delegation: `agent` is never present in a subagent's tool allowlist.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from . import agents as agents_mod
from . import prompt as prompt_mod
from . import skills as skills_mod
from . import tools as tools_mod
from .model_config import resolve_model_config
from .providers import ChatProvider


# Result statuses surfaced in the tool result metadata.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_INTERRUPTED = "interrupted"
STATUS_MAX_TURNS = "max_turns"

# Prompt used for the forced synthesis turn after the turn budget is hit.
_SYNTHESIS_NUDGE = (
    "Your turn budget is exhausted. Do not call any more tools. Using only what "
    "you already know from prior tool results, write the final Markdown report "
    "now. If you could not complete the task, say so plainly and state what is "
    "missing."
)

# Same parallel ceiling as the parent runner for read-only barrier groups.
_MAX_PARALLEL_TOOLS = 8

_READ_ONLY_TOOLS = frozenset({"read", "glob", "grep", "webfetch", "websearch"})


@dataclass
class AgentInvocation:
    """One subagent invocation. Owned by the parent runner."""

    agent: agents_mod.Agent
    task: str
    parent_model: str
    cwd: str
    cancel: threading.Event
    transcripts_dir: Path


def filtered_tool_schemas(allowed: tuple[str, ...]) -> list[dict[str, Any]]:
    if not allowed:
        return []
    allow = set(allowed)
    return [s for s in tools_mod.SCHEMAS if s.get("function", {}).get("name") in allow]


def filtered_skills(allowed: tuple[str, ...]) -> list[dict[str, Any]]:
    items = skills_mod.list_skills()
    if not allowed:
        return items
    allow = set(allowed)
    return [s for s in items if s.get("name") in allow]


def _short_id() -> str:
    return secrets.token_hex(3)


def _transcript_path(transcripts_dir: Path, agent_name: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return transcripts_dir / f"{stamp}-{agent_name}-{_short_id()}.md"


def _resolve_model(agent: agents_mod.Agent, parent_model: str) -> str:
    if agent.model:
        # Validate it exists. Surface a clear error rather than silently
        # falling back to the parent model.
        try:
            resolve_model_config(agent.model)
        except Exception:
            raise ValueError(
                f"agent {agent.name!r} requested model {agent.model!r} which is "
                f"not configured in ~/.klimt/models.json"
            )
        return agent.model
    return parent_model


def build_subagent_system_prompt(
    agent: agents_mod.Agent,
    cwd: str,
) -> str:
    """Assemble the subagent system prompt. Excludes global ~/.klimt/AGENTS.md."""
    schemas = filtered_tool_schemas(agent.tools)
    skills_items = filtered_skills(agent.skills)

    # We reuse the prompt builder but bypass the global profile by directly
    # composing the parts. Keep the same physical order so the kernel
    # authority discussion still makes sense.
    parts: list[str] = []
    parts.append(prompt_mod._read_text_if_exists(prompt_mod.KERNEL_PROMPT_PATH))
    parts.append(prompt_mod.build_tools_manifest(schemas, cwd))
    parts.append(prompt_mod.build_skills_manifest(skills_items))
    parts.append(prompt_mod._section("Project instructions", prompt_mod.load_project_agents(Path(cwd))))
    parts.append(_section("Subagent role", _agent_role_block(agent)))
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def _section(title: str, body: str) -> str:
    body = (body or "").strip()
    if not body:
        return ""
    return f"# {title}\n\n{body}"


def _agent_role_block(agent: agents_mod.Agent) -> str:
    header = (
        f"You are the `{agent.name}` subagent, invoked by the parent assistant. "
        f"Your tool mode is `{agent.mode}` ({', '.join(agent.tools) or 'no tools'}). "
        f"You have at most {agent.max_turns} assistant turns to complete the task. "
        "Return a concise Markdown result. Do not ask the parent follow-up questions; "
        "decide based on the task as written."
    )
    if not agent.body:
        return header
    return f"{header}\n\n{agent.body.strip()}"


def run_agent(inv: AgentInvocation) -> str:
    """Run one subagent turn loop and return a metadata-wrapped Markdown string."""
    try:
        model_name = _resolve_model(inv.agent, inv.parent_model)
    except ValueError as e:
        return _format_result(
            agent=inv.agent,
            model="",
            status=STATUS_ERROR,
            transcript_rel="",
            body=f"error: {e}",
            notes="model resolution failed",
        )

    if inv.cancel.is_set():
        return _format_result(
            agent=inv.agent,
            model=model_name,
            status=STATUS_INTERRUPTED,
            transcript_rel="",
            body="interrupted before start",
            notes="",
        )

    config = resolve_model_config(model_name)
    provider = ChatProvider(config)
    system = build_subagent_system_prompt(inv.agent, inv.cwd)

    inv.transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = _transcript_path(inv.transcripts_dir, inv.agent.name)

    history: List[Dict[str, Any]] = [{"role": "user", "content": inv.task}]
    status = STATUS_OK
    final_text = ""
    turns_used = 0
    error_msg = ""

    schemas = filtered_tool_schemas(inv.agent.tools)
    max_completion_tokens = config.max_completion_tokens or 4096

    try:
        for turn in range(inv.agent.max_turns):
            if inv.cancel.is_set():
                status = STATUS_INTERRUPTED
                break

            turns_used = turn + 1
            assistant_entry, tool_calls = _run_subagent_turn(
                provider=provider,
                system=system,
                history=history,
                schemas=schemas,
                max_completion_tokens=max_completion_tokens,
                cancel=inv.cancel,
            )
            history.append(assistant_entry)

            if inv.cancel.is_set():
                status = STATUS_INTERRUPTED
                final_text = assistant_entry.get("content") or ""
                break

            if not tool_calls:
                final_text = assistant_entry.get("content") or ""
                break

            tool_results = _execute_subagent_tools(
                tool_calls=tool_calls,
                cancel=inv.cancel,
                cwd=inv.cwd,
            )
            for tc in tool_calls:
                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_results.get(tc["id"], ""),
                })

            if inv.cancel.is_set():
                status = STATUS_INTERRUPTED
                break
        else:
            # for/else: ran out of turns without breaking. Force a final
            # synthesis turn with no tools so the model produces prose instead
            # of returning an empty result.
            status = STATUS_MAX_TURNS
            final_text = _force_synthesis(
                provider=provider,
                system=system,
                history=history,
                max_completion_tokens=max_completion_tokens,
                cancel=inv.cancel,
            )
            if not final_text:
                final_text = (
                    _last_assistant_text(history)
                    or "(no final text; turn budget exhausted)"
                )
    except Exception as e:  # noqa: BLE001 - return as error metadata, don't crash the parent
        status = STATUS_ERROR
        error_msg = f"{type(e).__name__}: {e}"

    transcript_rel = _write_transcript(
        path=transcript_path,
        agent=inv.agent,
        model=model_name,
        task=inv.task,
        history=history,
        status=status,
        turns_used=turns_used,
        error_msg=error_msg,
    )

    body = final_text if status == STATUS_OK else (final_text or error_msg or status)
    notes = ""
    if status == STATUS_MAX_TURNS:
        notes = f"hit turn budget of {inv.agent.max_turns}"
    elif status == STATUS_ERROR and error_msg:
        notes = error_msg
    elif status == STATUS_INTERRUPTED:
        notes = "parent interrupted before completion"

    return _format_result(
        agent=inv.agent,
        model=model_name,
        status=status,
        transcript_rel=transcript_rel,
        body=body,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Subagent inner loop
# ---------------------------------------------------------------------------


def _run_subagent_turn(
    *,
    provider: ChatProvider,
    system: str,
    history: list[dict[str, Any]],
    schemas: list[dict[str, Any]],
    max_completion_tokens: int,
    cancel: threading.Event,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One assistant turn. Returns the assistant history entry and any tool calls."""
    stream = provider.stream(
        messages=[
            {"role": "system", "content": system},
            *[_msg_for_provider(m, provider) for m in history],
        ],
        tool_schemas=schemas,
        max_completion_tokens=max_completion_tokens,
    )

    content_buf: list[str] = []
    reasoning_buf: list[str] = []
    reasoning_signature: str | None = None
    tool_calls: Dict[int, Dict[str, str]] = {}
    finish_reason: str | None = None

    try:
        for chunk in stream:
            if cancel.is_set():
                break
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            choice_finish_reason = getattr(choice, "finish_reason", None)
            if choice_finish_reason:
                finish_reason = str(choice_finish_reason)
            delta = choice.delta
            if delta is None:
                continue

            reasoning_delta = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
            sig = getattr(delta, "reasoning_signature", None)
            if sig:
                reasoning_signature = str(sig)
            if reasoning_delta:
                reasoning_buf.append(reasoning_delta)
            if delta.content:
                content_buf.append(delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["args"] += tc.function.arguments
    finally:
        close = getattr(stream, "close", None)
        if close and cancel.is_set():
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    full_text = "".join(content_buf)
    full_reasoning = "".join(reasoning_buf)
    assistant_entry: Dict[str, Any] = {
        "role": "assistant",
        "content": full_text or None,
    }
    if full_reasoning:
        assistant_entry["reasoning"] = full_reasoning
    if reasoning_signature:
        assistant_entry["reasoning_signature"] = reasoning_signature
    if finish_reason:
        assistant_entry["finish_reason"] = finish_reason

    ordered = [v for _, v in sorted(tool_calls.items())]
    if ordered:
        assistant_entry["tool_calls"] = [
            {
                "id": v["id"],
                "type": "function",
                "function": {"name": v["name"], "arguments": v["args"]},
            }
            for v in ordered
        ]
    return assistant_entry, ordered


def _execute_subagent_tools(
    *,
    tool_calls: list[dict[str, Any]],
    cancel: threading.Event,
    cwd: str,
) -> dict[str, str]:
    """Execute tool calls in barrier groups, same policy as the parent runner."""
    results: dict[str, str] = {}
    groups = _barrier_groups(tool_calls)
    for group in groups:
        if cancel.is_set():
            for tc in group:
                results.setdefault(tc["id"], "[interrupted]")
            continue
        if len(group) == 1:
            tc = group[0]
            args = _parse_args(tc["args"])
            results[tc["id"]] = tools_mod.run(tc["name"], args, cancel, cwd)
        else:
            workers = min(_MAX_PARALLEL_TOOLS, len(group))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(tools_mod.run, tc["name"], _parse_args(tc["args"]), cancel, cwd): tc
                    for tc in group
                }
                for fut in as_completed(futures):
                    tc = futures[fut]
                    try:
                        results[tc["id"]] = fut.result()
                    except Exception as e:  # noqa: BLE001
                        results[tc["id"]] = f"error: {type(e).__name__}: {e}"
    return results


def _barrier_groups(tool_calls: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for tc in tool_calls:
        if tc["name"] in _READ_ONLY_TOOLS:
            current.append(tc)
            continue
        if current:
            groups.append(current)
            current = []
        groups.append([tc])
    if current:
        groups.append(current)
    return groups


def _parse_args(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}


def _msg_for_provider(msg: dict[str, Any], provider: ChatProvider) -> dict[str, Any]:
    out = {k: v for k, v in msg.items() if k not in {"usage", "reasoning", "reasoning_signature"}}
    if provider.preserves_reasoning_blocks() and msg.get("reasoning"):
        out["reasoning"] = msg["reasoning"]
        if msg.get("reasoning_signature"):
            out["reasoning_signature"] = msg["reasoning_signature"]
    return out


def _force_synthesis(
    *,
    provider: ChatProvider,
    system: str,
    history: list[dict[str, Any]],
    max_completion_tokens: int,
    cancel: threading.Event,
) -> str:
    """Run one extra turn with no tool schemas to force a final answer.

    Appends the synthesis nudge to history (and the model's response) so the
    transcript reflects what actually happened. Returns the prose content, or
    an empty string on cancel/empty response.
    """
    if cancel.is_set():
        return ""
    history.append({"role": "user", "content": _SYNTHESIS_NUDGE})
    try:
        assistant_entry, tool_calls = _run_subagent_turn(
            provider=provider,
            system=system,
            history=history,
            schemas=[],
            max_completion_tokens=max_completion_tokens,
            cancel=cancel,
        )
    except Exception as e:  # noqa: BLE001 - surfaced in transcript notes via outer handler
        history.append({
            "role": "assistant",
            "content": f"(synthesis turn failed: {type(e).__name__}: {e})",
        })
        return ""
    history.append(assistant_entry)
    if tool_calls:
        # The model ignored the no-tools instruction. Drop the orphan tool_calls
        # from the entry so the transcript doesn't claim work happened.
        assistant_entry.pop("tool_calls", None)
    return (assistant_entry.get("content") or "").strip()


def _last_assistant_text(history: list[dict[str, Any]]) -> str:
    for msg in reversed(history):
        if msg.get("role") == "assistant" and msg.get("content"):
            return str(msg["content"])
    return ""


# ---------------------------------------------------------------------------
# Result + transcript formatting
# ---------------------------------------------------------------------------


def _format_result(
    *,
    agent: agents_mod.Agent,
    model: str,
    status: str,
    transcript_rel: str,
    body: str,
    notes: str,
) -> str:
    header_lines = [
        f"agent: {agent.name}",
        f"status: {status}",
        f"model: {model or '(unresolved)'}",
        f"mode: {agent.mode}",
        f"tools: {', '.join(agent.tools) or 'none'}",
    ]
    if transcript_rel:
        header_lines.append(f"transcript: {transcript_rel}")
    if notes:
        header_lines.append(f"notes: {notes}")
    header = "\n".join(header_lines)
    body = (body or "").strip() or "(no result text)"
    return f"{header}\n\n--- result ---\n{body}"


def _write_transcript(
    *,
    path: Path,
    agent: agents_mod.Agent,
    model: str,
    task: str,
    history: list[dict[str, Any]],
    status: str,
    turns_used: int,
    error_msg: str,
) -> str:
    """Write a Markdown transcript and return a path string for the result header."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Subagent transcript: {agent.name}",
            "",
            f"- status: {status}",
            f"- model: {model}",
            f"- mode: {agent.mode}",
            f"- tools: {', '.join(agent.tools) or 'none'}",
            f"- turns used: {turns_used} / {agent.max_turns}",
        ]
        if error_msg:
            lines.append(f"- error: {error_msg}")
        lines += ["", "## Task", "", "```", task, "```", "", "## Messages", ""]
        for i, msg in enumerate(history):
            lines.append(f"### {i}. {msg.get('role', 'unknown')}")
            lines.append("")
            content = msg.get("content")
            if content:
                lines.append("```")
                lines.append(str(content))
                lines.append("```")
            reasoning = msg.get("reasoning")
            if reasoning:
                lines += ["", "_reasoning_:", "", "```", str(reasoning), "```"]
            tcs = msg.get("tool_calls") or []
            for tc in tcs:
                fn = tc.get("function") or {}
                lines += [
                    "",
                    f"_tool call_: `{fn.get('name', '')}`",
                    "",
                    "```json",
                    fn.get("arguments", ""),
                    "```",
                ]
            if msg.get("role") == "tool":
                lines += ["", f"_tool_call_id_: `{msg.get('tool_call_id', '')}`"]
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)
    except OSError as e:
        return f"(transcript write failed: {type(e).__name__}: {e})"
