"""System prompt assembly for Klimt.

The harness prompt is layered deliberately:

1. Kernel: non-persona harness/tool protocol.
2. Runtime manifest: tools and skills discovered by the harness.
3. Global profile: user-wide instructions from ~/.klimt/AGENTS.md.
4. Project instructions: AGENTS.md files from the current working tree.
"""
from __future__ import annotations

from html import escape as xml_escape
from pathlib import Path
from typing import Any, Iterable

KLIMT_DIR = Path.home() / ".klimt"
GLOBAL_AGENTS_PATH = KLIMT_DIR / "AGENTS.md"
PROJECT_AGENTS_NAME = "AGENTS.md"
KERNEL_PROMPT_PATH = Path(__file__).with_name("KERNEL.md")


def _read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _section(title: str, body: str) -> str:
    body = body.strip()
    if not body:
        return ""
    return f"# {title}\n\n{body}"


def build_tools_manifest(schemas: Iterable[dict[str, Any]], cwd: str | None = None) -> str:
    lines = [
        "# Runtime tool manifest",
        "",
        "The following tools are available in this session. Their schemas are enforced by Klimt.",
    ]
    if cwd:
        lines.extend([
            "",
            f"The current working directory for relative file and shell operations is `{cwd}`.",
        ])
    lines.append("")
    found = False
    for schema in schemas:
        fn = schema.get("function", {})
        name = str(fn.get("name") or "")
        desc = str(fn.get("description") or "")
        if not name:
            continue
        found = True
        lines.append(f"- `{name}` — {desc}")
    if not found:
        lines.append("- none")
    return "\n".join(lines)


def build_skills_manifest(items: Iterable[dict[str, Any]]) -> str:
    items = list(items)
    if not items:
        return ""

    lines = [
        "# Runtime skill manifest",
        "",
        "The following skills are available. Load a skill when the user's task matches its description.",
        "",
        "<available_skills>",
    ]
    for s in items:
        name = xml_escape(str(s.get("name") or ""))
        desc = xml_escape(str(s.get("description") or "(no description)"))
        location = xml_escape(str(Path(s.get("path") or "").expanduser().resolve()))
        lines.extend([
            "  <skill>",
            f"    <name>{name}</name>",
            f"    <description>{desc}</description>",
            f"    <location>{location}</location>",
            "  </skill>",
        ])
    lines.append("</available_skills>")
    return "\n".join(lines)


def load_global_profile() -> str:
    return _read_text_if_exists(GLOBAL_AGENTS_PATH)


def project_agents_paths(start: Path | None = None) -> list[Path]:
    """Return project AGENTS.md files from outermost to nearest directory."""
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent

    paths: list[Path] = []
    for folder in [current, *current.parents]:
        candidate = folder / PROJECT_AGENTS_NAME
        if candidate == GLOBAL_AGENTS_PATH:
            continue
        if candidate.exists() and candidate.is_file():
            paths.append(candidate)
    paths.reverse()
    return paths


def load_project_agents(start: Path | None = None) -> str:
    sections: list[str] = []
    for path in project_agents_paths(start):
        body = _read_text_if_exists(path)
        if body:
            sections.append(f"## {path}\n\n{body}")
    return "\n\n".join(sections)


def build_system_prompt(
    tool_schemas: Iterable[dict[str, Any]],
    skill_items: Iterable[dict[str, Any]],
    start: Path | None = None,
    cwd: str | None = None,
    agent_manifest: str = "",
) -> str:
    parts = [
        _read_text_if_exists(KERNEL_PROMPT_PATH),
        build_tools_manifest(tool_schemas, cwd),
        build_skills_manifest(skill_items),
        agent_manifest,
        _section("Global user profile", load_global_profile()),
        _section("Project instructions", load_project_agents(start)),
    ]
    return "\n\n".join(part for part in parts if part.strip()) + "\n"
