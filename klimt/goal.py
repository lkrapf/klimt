"""Goal-directed autonomy: keep working until a condition is met.

A `Goal` is a session-scoped completion condition. After each assistant turn a
cheap evaluator model reads the condition plus the recent transcript and decides
whether the condition holds. If not, the driver (in tab_api) starts another turn.

The driver enforces a hard turn/time budget independently of the evaluator so a
mis-worded or unverifiable condition cannot loop forever. In Klimt the `bash`
tool is unsandboxed, so this budget is the safety mechanism, not polish.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# Hard ceilings enforced by the driver regardless of the evaluator's verdict.
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_SECONDS = 600

# A transient error (dropped connection, 5xx, timeout) on a single turn should
# not kill an unattended loop. Retry the turn up to this many consecutive times,
# backing off between attempts. The counter resets after any turn that streams
# without raising, so only a sustained outage stops the goal.
MAX_CONSECUTIVE_ERRORS = 4
RETRY_BACKOFF_SECONDS = (2, 5, 15, 30)

# Model classes tried, in order, to find a cheap evaluator. Falls back to the
# session model when none are configured.
EVALUATOR_CLASSES = ("fast", "cheap", "haiku")

CONDITION_MAX_CHARS = 4000

# How much recent transcript the evaluator sees.
_TRANSCRIPT_MESSAGES = 12
_MESSAGE_MAX_CHARS = 2000

_EVAL_SYSTEM = (
    "You are a goal-completion evaluator. You are given a COMPLETION CONDITION "
    "and the recent transcript of an AI assistant working toward it. Decide "
    "whether the condition is satisfied based ONLY on what the transcript "
    "demonstrates. You cannot run commands or read files yourself. If the "
    "transcript does not clearly show the condition is met, answer NO.\n\n"
    "Respond with exactly two lines and nothing else:\n"
    "VERDICT: YES or NO\n"
    "REASON: one short sentence"
)

# clear aliases accepted by the /goal command
CLEAR_ALIASES = frozenset({"clear", "stop", "off", "reset", "none", "cancel"})


@dataclass
class Goal:
    condition: str
    max_turns: int = DEFAULT_MAX_TURNS
    max_seconds: int = DEFAULT_MAX_SECONDS
    turns: int = 0
    started_at: float = field(default_factory=time.time)
    last_reason: str = ""
    achieved: bool = False

    def elapsed(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def budget_exhausted(self) -> tuple[bool, str]:
        """Return (exhausted, reason). The hard stop, independent of the model."""
        if self.turns >= self.max_turns:
            return True, f"turn budget reached ({self.max_turns} turns)"
        if self.elapsed() >= self.max_seconds:
            return True, f"time budget reached ({self.max_seconds}s)"
        return False, ""

    def initial_directive(self) -> str:
        return self.condition

    def continuation_directive(self) -> str:
        reason = self.last_reason.strip() or "not yet satisfied"
        return (
            f"The goal is not met yet: {reason}\n\n"
            f"Keep working toward this goal: {self.condition}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "max_turns": self.max_turns,
            "max_seconds": self.max_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Goal | None":
        condition = (data.get("condition") or "").strip()
        if not condition:
            return None
        return cls(
            condition=condition,
            max_turns=int(data.get("max_turns") or DEFAULT_MAX_TURNS),
            max_seconds=int(data.get("max_seconds") or DEFAULT_MAX_SECONDS),
        )


def parse_max_turns(arg: str) -> tuple[str, int]:
    """Split an optional leading `turns=N` clause off a /goal argument.

    Returns (condition, max_turns). `turns=N` may appear at the start of the
    argument; anything after it is the condition.
    """
    stripped = arg.strip()
    if stripped.lower().startswith("turns="):
        head, _, rest = stripped.partition(" ")
        _, _, n = head.partition("=")
        try:
            return rest.strip(), max(1, int(n))
        except ValueError:
            return rest.strip(), DEFAULT_MAX_TURNS
    return stripped, DEFAULT_MAX_TURNS


def recent_transcript(history: list[dict[str, Any]]) -> str:
    """Render the tail of history into plain text for the evaluator.

    Skips reasoning and provider bookkeeping; keeps role + content and tool
    results, each truncated, so the evaluator judges what Claude surfaced.
    """
    lines: list[str] = []
    for msg in history[-_TRANSCRIPT_MESSAGES:]:
        role = msg.get("role") or "?"
        content = (msg.get("content") or "").strip()
        if role == "tool":
            label = "tool_result"
        else:
            label = role
        if not content and msg.get("tool_calls"):
            names = ", ".join(
                (tc.get("function") or {}).get("name") or "tool"
                for tc in msg.get("tool_calls") or []
            )
            content = f"[called tools: {names}]"
        if not content:
            continue
        if len(content) > _MESSAGE_MAX_CHARS:
            content = content[:_MESSAGE_MAX_CHARS] + " …[truncated]"
        lines.append(f"{label}: {content}")
    return "\n\n".join(lines) or "(no transcript yet)"


def evaluate(provider: Any, condition: str, transcript: str, max_tokens: int) -> tuple[bool, str]:
    """Ask the evaluator whether `condition` holds. Returns (met, reason).

    On any error the goal is treated as not-met with the error as the reason, so
    the driver keeps control (and its budget still bounds the loop).
    """
    user = (
        f"COMPLETION CONDITION:\n{condition}\n\n"
        f"RECENT TRANSCRIPT:\n{transcript}"
    )
    try:
        response = provider.complete(
            messages=[
                {"role": "system", "content": _EVAL_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_completion_tokens=min(max_tokens, 256),
        )
        text = response.choices[0].message.content if response.choices else ""
    except Exception as e:  # noqa: BLE001
        return False, f"evaluator error: {type(e).__name__}: {e}"
    return _parse_verdict(text or "")


def _parse_verdict(text: str) -> tuple[bool, str]:
    verdict = None
    reason = ""
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("verdict:"):
            value = line.split(":", 1)[1].strip().lower()
            verdict = value.startswith("y")
        elif low.startswith("reason:"):
            reason = line.split(":", 1)[1].strip()
    if verdict is None:
        # Model ignored the format; be conservative and keep working.
        low = text.strip().lower()
        verdict = low.startswith("yes")
        reason = reason or text.strip()[:200]
    return bool(verdict), reason or "(no reason given)"
