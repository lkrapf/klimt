"""Subagent discovery, loading, and tool-allowlist normalization.

Agents are defined as Markdown files with YAML-ish frontmatter under one of:

- `~/.klimt/agents/**/*.md`  (user-wide)
- `<project>/.klimt/agents/**/*.md`  (project-local; overrides user)

Plus the built-in `read-only` and `read-write` agents, which are the
lowest-priority fallbacks. `read-only` is the default when no agent name is
specified by the caller.

Frontmatter keys: `name`, `description`, `tools`, `model`, `maxTurns` /
`max_turns`, `skills`. See PLAN.md "Subagent first-cut spec" for semantics.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import tools as _tools_mod

# Tools the parent may grant to a subagent. The `agent` tool is intentionally
# absent: no nested delegation in the first cut.
#
# Derived from tools.SPECS so adding a new tool automatically participates in
# subagent allowlists.
ALL_TOOLS: tuple[str, ...] = _tools_mod.ALL_TOOL_NAMES
READ_TOOLS: tuple[str, ...] = tuple(s.name for s in _tools_mod.SPECS if s.read_only)
FULL_TOOLS: tuple[str, ...] = ALL_TOOLS

MUTATING_TOOLS: frozenset[str] = _tools_mod.MUTATING_TOOLS

DEFAULT_MAX_TURNS = 6

USER_AGENTS_DIR = Path.home() / ".klimt" / "agents"
PROJECT_AGENTS_REL = Path(".klimt") / "agents"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*:")


@dataclass(frozen=True)
class Agent:
    """A resolved subagent definition."""

    name: str
    description: str
    tools: tuple[str, ...]
    body: str
    model: str | None = None
    max_turns: int = DEFAULT_MAX_TURNS
    skills: tuple[str, ...] = field(default_factory=tuple)
    source: str = "builtin"  # builtin | user | project
    path: Path | None = None

    @property
    def mode(self) -> str:
        """Derived: full if any mutating tool is allowed; none if empty; else read."""
        if not self.tools:
            return "none"
        if any(t in MUTATING_TOOLS for t in self.tools):
            return "full"
        return "read"


# ---------------------------------------------------------------------------
# Tool allowlist normalization
# ---------------------------------------------------------------------------


def normalize_tools(value: object) -> tuple[tuple[str, ...], list[str]]:
    """Resolve a frontmatter `tools:` value into a canonical allowlist.

    Returns (tools, warnings). Accepts:
      - None / missing: defaults to read mode.
      - String mode keyword: "none" / "read" / "full".
      - List or comma-separated string of explicit tool names.

    Unknown tool names are dropped with a warning. `agent` is never allowed.
    """
    warnings: list[str] = []
    if value is None or value == "":
        return READ_TOOLS, warnings

    if isinstance(value, str):
        stripped = value.strip()
        keyword = stripped.lower()
        if keyword in ("none", "read", "full"):
            return _mode_to_tools(keyword), warnings
        # Comma-separated list.
        names = [n.strip() for n in stripped.split(",") if n.strip()]
    elif isinstance(value, (list, tuple)):
        names = [str(n).strip() for n in value if str(n).strip()]
    else:
        warnings.append(f"tools: unrecognized value {value!r}; defaulting to read mode")
        return READ_TOOLS, warnings

    resolved: list[str] = []
    seen: set[str] = set()
    for n in names:
        if n == "agent":
            warnings.append("tools: `agent` is not allowed for subagents; dropped")
            continue
        if n not in ALL_TOOLS:
            warnings.append(f"tools: unknown tool {n!r}; dropped")
            continue
        if n in seen:
            continue
        seen.add(n)
        resolved.append(n)
    return tuple(resolved), warnings


def _mode_to_tools(mode: str) -> tuple[str, ...]:
    if mode == "none":
        return ()
    if mode == "full":
        return FULL_TOOLS
    return READ_TOOLS


# ---------------------------------------------------------------------------
# Frontmatter parsing (single-line + folded multi-line strings, plus simple lists)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Return (meta, body). Body is the markdown after the frontmatter block."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    meta: dict[str, object] = {}
    current: str | None = None
    current_list: list[str] | None = None
    for line in m.group(1).splitlines():
        if _KEY_RE.match(line):
            k, _, v = line.partition(":")
            key = k.strip()
            val = v.strip()
            if val in ("|", ">"):
                meta[key] = ""
                current = key
                current_list = None
            elif val == "":
                # Could be the start of a list; we will see "- ..." lines next.
                meta[key] = ""
                current = key
                current_list = []
            else:
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                meta[key] = val
                current = key
                current_list = None
        elif current is None:
            continue
        elif line.strip().startswith("- "):
            item = line.strip()[2:].strip()
            if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
                item = item[1:-1]
            if current_list is None:
                current_list = []
            current_list.append(item)
            meta[current] = list(current_list)
        elif line.strip():
            existing = meta.get(current, "")
            if isinstance(existing, list):
                continue
            joined = (str(existing) + " " + line.strip()).strip()
            meta[current] = joined
    return meta, body


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _coerce_str_list(value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return ()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def builtin_read_only() -> Agent:
    return Agent(
        name="read-only",
        description=(
            "General-purpose read-only subagent. Reads files, searches code "
            "and the web, and reports back. Cannot edit, write, or run shell "
            "commands. Safe to fan out in parallel."
        ),
        tools=READ_TOOLS,
        body=(
            "You are a focused read-only subagent. Your job is to investigate "
            "the task delegated by the parent assistant and return a concise, "
            "structured Markdown report.\n\n"
            "- Stick to the task. Do not invent scope.\n"
            "- Cite specific files, line numbers, URLs, and commands you used.\n"
            "- Distinguish observed facts from assumptions.\n"
            "- If you cannot finish within the turn budget, return what you "
            "have and clearly state what is unfinished.\n"
        ),
        source="builtin",
    )


def builtin_read_write() -> Agent:
    return Agent(
        name="read-write",
        description=(
            "General-purpose read-write subagent with full local tool access "
            "(read, write, edit, bash, glob, grep, webfetch, websearch). Use "
            "to delegate scoped work that mutates the working tree or runs "
            "commands. Should generally be invoked serially; if the parent "
            "runs multiple instances in parallel, it must give each disjoint "
            "scope."
        ),
        tools=FULL_TOOLS,
        body=(
            "You are a focused read-write subagent. Your job is to carry out "
            "the task delegated by the parent assistant, which may include "
            "editing files, writing new files, and running shell commands.\n\n"
            "- Stick to the task and the scope the parent gave you. Do not "
            "  touch unrelated files or wander outside the working tree.\n"
            "- Prefer the smallest change that satisfies the task. Do not "
            "  refactor opportunistically.\n"
            "- Assume you may be running alongside other subagents. If the "
            "  parent has not guaranteed disjoint scopes, avoid edits that "
            "  could race with concurrent work.\n"
            "- In your final report, list every file you mutated and every "
            "  shell command you ran, each with a one-line rationale.\n"
            "- Cite specific files, line numbers, URLs, and commands.\n"
            "- If you cannot finish within the turn budget, return what you "
            "  have, describe the partial state on disk, and clearly state "
            "  what is unfinished.\n"
        ),
        source="builtin",
    )


def _load_agent_file(path: Path, source: str) -> tuple[Agent | None, list[str]]:
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        warnings.append(f"{path}: {type(e).__name__}: {e}")
        return None, warnings
    meta, body = _parse_frontmatter(text)
    name = str(meta.get("name") or path.stem).strip()
    if not name:
        warnings.append(f"{path}: missing agent name")
        return None, warnings
    if name == "agent":
        warnings.append(f"{path}: name 'agent' is reserved; skipping")
        return None, warnings

    description = str(meta.get("description") or "").strip()
    tools, tool_warnings = normalize_tools(meta.get("tools"))
    for w in tool_warnings:
        warnings.append(f"{path}: {w}")
    model_raw = str(meta.get("model") or "").strip()
    model: str | None = model_raw or None
    max_turns = _coerce_int(
        meta.get("maxTurns", meta.get("max_turns")), DEFAULT_MAX_TURNS
    )
    if max_turns < 1:
        warnings.append(f"{path}: max_turns must be >= 1; using {DEFAULT_MAX_TURNS}")
        max_turns = DEFAULT_MAX_TURNS
    skills = _coerce_str_list(meta.get("skills"))

    return Agent(
        name=name,
        description=description,
        tools=tools,
        body=body.strip(),
        model=model,
        max_turns=max_turns,
        skills=skills,
        source=source,
        path=path,
    ), warnings


def _scan_directory(directory: Path, source: str) -> tuple[list[Agent], list[str]]:
    agents: list[Agent] = []
    warnings: list[str] = []
    if not directory.exists() or not directory.is_dir():
        return agents, warnings
    for p in sorted(directory.rglob("*.md")):
        if not p.is_file():
            continue
        agent, agent_warnings = _load_agent_file(p, source)
        warnings.extend(agent_warnings)
        if agent:
            agents.append(agent)
    return agents, warnings


def project_agents_dir(cwd: Path | str | None) -> Path:
    base = Path(cwd or Path.cwd()).expanduser().resolve()
    return base / PROJECT_AGENTS_REL


def discover_agents(cwd: Path | str | None = None) -> tuple[list[Agent], list[str]]:
    """Discover agents from project (highest), user, then built-ins.

    Higher-priority agents override lower-priority ones with the same name.
    Returns (agents, warnings).
    """
    warnings: list[str] = []
    by_name: dict[str, Agent] = {}

    project_agents, w = _scan_directory(project_agents_dir(cwd), source="project")
    warnings.extend(w)
    user_agents, w = _scan_directory(USER_AGENTS_DIR, source="user")
    warnings.extend(w)

    # Order: lowest priority first; later entries overwrite earlier ones.
    for agent in (builtin_read_only(), builtin_read_write()):
        by_name[agent.name] = agent
    for agent in user_agents:
        by_name[agent.name] = agent
    for agent in project_agents:
        by_name[agent.name] = agent

    return sorted(by_name.values(), key=lambda a: a.name), warnings


def list_agents(cwd: Path | str | None = None) -> list[Agent]:
    agents, _ = discover_agents(cwd)
    return agents


def find_agent(name: str, cwd: Path | str | None = None) -> Agent | None:
    name = (name or "").strip()
    if not name:
        return None
    for a in list_agents(cwd):
        if a.name == name:
            return a
    return None


# ---------------------------------------------------------------------------
# Parent-prompt catalog
# ---------------------------------------------------------------------------


def build_catalog_manifest(agents: Iterable[Agent]) -> str:
    """XML-shaped catalog the parent model sees, analogous to skill manifest."""
    agents = list(agents)
    if not agents:
        return ""
    from html import escape as xml_escape

    lines = [
        "# Runtime agent manifest",
        "",
        "The following subagents are available via the `agent` tool. Each has a fixed tool allowlist; delegate when the task fits an agent's scope. Pass `model` to route heavy reasoning to a stronger model and grunt work to a cheaper one; class names like `opus` or `haiku` resolve to whichever model is tagged that way in ~/.klimt/models.json.",
        "",
        "<available_agents>",
    ]
    for a in agents:
        lines.extend([
            "  <agent>",
            f"    <name>{xml_escape(a.name)}</name>",
            f"    <description>{xml_escape(a.description or '(no description)')}</description>",
            f"    <mode>{xml_escape(a.mode)}</mode>",
            f"    <tools>{xml_escape(', '.join(a.tools) or 'none')}</tools>",
        ])
        if a.model:
            lines.append(f"    <default_model>{xml_escape(a.model)}</default_model>")
        lines.append("  </agent>")
    lines.append("</available_agents>")
    return "\n".join(lines)
