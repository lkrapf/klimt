"""Subagent loader, allowlist normalization, and catalog rendering."""
from __future__ import annotations

from pathlib import Path

import pytest

from klimt import agents


# ---------------------------------------------------------------------------
# normalize_tools
# ---------------------------------------------------------------------------


def test_normalize_default_is_read():
    tools, warnings = agents.normalize_tools(None)
    assert set(tools) == set(agents.READ_TOOLS)
    assert warnings == []


def test_normalize_mode_none():
    tools, warnings = agents.normalize_tools("none")
    assert tools == ()
    assert warnings == []


def test_normalize_mode_full():
    tools, warnings = agents.normalize_tools("full")
    assert set(tools) == set(agents.FULL_TOOLS)
    assert warnings == []


def test_normalize_explicit_list():
    tools, warnings = agents.normalize_tools(["read", "grep"])
    assert tools == ("read", "grep")
    assert warnings == []


def test_normalize_comma_string():
    tools, warnings = agents.normalize_tools("read, grep, glob")
    assert tools == ("read", "grep", "glob")
    assert warnings == []


def test_normalize_drops_unknown():
    tools, warnings = agents.normalize_tools(["read", "magic", "bash"])
    assert tools == ("read", "bash")
    assert any("magic" in w for w in warnings)


def test_normalize_blocks_agent():
    tools, warnings = agents.normalize_tools(["read", "agent"])
    assert "agent" not in tools
    assert any("agent" in w for w in warnings)


def test_normalize_dedupes():
    tools, _ = agents.normalize_tools(["read", "read", "grep"])
    assert tools == ("read", "grep")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def test_parse_frontmatter_basic():
    meta, body = agents._parse_frontmatter(
        "---\nname: reviewer\ndescription: code reviewer\n---\nbody text\n"
    )
    assert meta == {"name": "reviewer", "description": "code reviewer"}
    assert body.strip() == "body text"


def test_parse_frontmatter_list():
    meta, _ = agents._parse_frontmatter(
        "---\nname: reviewer\ntools:\n  - read\n  - grep\n---\nx\n"
    )
    assert meta["tools"] == ["read", "grep"]


def test_parse_frontmatter_folded_description():
    meta, _ = agents._parse_frontmatter(
        "---\nname: reviewer\ndescription: >\n  multi line\n  description\n---\nx\n"
    )
    assert meta["description"].strip() == "multi line description"


def test_parse_no_frontmatter():
    meta, body = agents._parse_frontmatter("just text\n")
    assert meta == {}
    assert body == "just text\n"


# ---------------------------------------------------------------------------
# Agent loading + discovery
# ---------------------------------------------------------------------------


def _write_agent(dir: Path, name: str, content: str) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    p = dir / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_load_agent_file_defaults(tmp_path: Path):
    p = _write_agent(
        tmp_path,
        "reviewer",
        "---\nname: reviewer\ndescription: code reviewer\n---\nDo review.\n",
    )
    agent, warnings = agents._load_agent_file(p, source="user")
    assert agent is not None
    assert agent.name == "reviewer"
    assert agent.description == "code reviewer"
    assert agent.tools == agents.READ_TOOLS
    assert agent.mode == "read"
    assert agent.max_turns == agents.DEFAULT_MAX_TURNS
    assert agent.body == "Do review."
    assert warnings == []


def test_load_agent_full_mode(tmp_path: Path):
    p = _write_agent(
        tmp_path,
        "tester",
        "---\nname: tester\ntools: full\nmax_turns: 5\n---\nbody\n",
    )
    agent, _ = agents._load_agent_file(p, source="project")
    assert agent.mode == "full"
    assert agent.max_turns == 5


def test_load_agent_reserved_name(tmp_path: Path):
    p = _write_agent(tmp_path, "x", "---\nname: agent\n---\nbody\n")
    agent, warnings = agents._load_agent_file(p, source="user")
    assert agent is None
    assert any("reserved" in w for w in warnings)


def test_discover_project_overrides_user(tmp_path: Path, monkeypatch):
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "proj" / ".klimt" / "agents"
    _write_agent(
        user_dir,
        "reviewer",
        "---\nname: reviewer\ndescription: user version\n---\nu\n",
    )
    _write_agent(
        project_dir,
        "reviewer",
        "---\nname: reviewer\ndescription: project version\n---\np\n",
    )
    monkeypatch.setattr(agents, "USER_AGENTS_DIR", user_dir)

    found, warnings = agents.discover_agents(cwd=tmp_path / "proj")
    names = {a.name: a for a in found}
    assert names["reviewer"].source == "project"
    assert names["reviewer"].description == "project version"
    assert "general" in names  # built-in always present


def test_discover_user_overrides_builtin(tmp_path: Path, monkeypatch):
    user_dir = tmp_path / "user_agents"
    _write_agent(
        user_dir,
        "general",
        "---\nname: general\ndescription: custom\ntools: full\n---\ncustom body\n",
    )
    monkeypatch.setattr(agents, "USER_AGENTS_DIR", user_dir)
    found, _ = agents.discover_agents(cwd=tmp_path)
    by_name = {a.name: a for a in found}
    assert by_name["general"].source == "user"
    assert by_name["general"].mode == "full"


def test_builtin_general_present_when_no_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agents, "USER_AGENTS_DIR", tmp_path / "missing")
    found, warnings = agents.discover_agents(cwd=tmp_path / "no-project")
    names = [a.name for a in found]
    assert names == ["general"]


# ---------------------------------------------------------------------------
# Catalog manifest
# ---------------------------------------------------------------------------


def test_catalog_manifest_empty():
    assert agents.build_catalog_manifest([]) == ""


def test_catalog_manifest_shape():
    agent = agents.builtin_general()
    out = agents.build_catalog_manifest([agent])
    assert "<available_agents>" in out
    assert "<name>general</name>" in out
    assert "<mode>read</mode>" in out
    assert "read, glob, grep, webfetch, websearch" in out
