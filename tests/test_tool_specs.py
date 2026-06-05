"""Sanity checks on the centralized ToolSpec registry."""
from __future__ import annotations

from klimt import tools


def test_specs_cover_every_schema() -> None:
    schema_names = {s["function"]["name"] for s in tools.SCHEMAS}
    spec_names = {s.name for s in tools.SPECS}
    assert schema_names == spec_names


def test_read_only_partition_matches_mutating() -> None:
    # Every tool is classified exactly once.
    union = tools.READ_ONLY_TOOLS | tools.MUTATING_TOOLS
    intersection = tools.READ_ONLY_TOOLS & tools.MUTATING_TOOLS
    all_names = {s.name for s in tools.SPECS}
    assert union == all_names
    assert intersection == frozenset()


def test_known_read_only_tools() -> None:
    # Lock in the expected classification so accidental flips fail loudly.
    assert tools.READ_ONLY_TOOLS == frozenset(
        {"read", "glob", "grep", "webfetch", "websearch"}
    )


def test_known_mutating_tools() -> None:
    assert tools.MUTATING_TOOLS == frozenset({"edit", "write", "bash"})


def test_run_unknown_tool_returns_error_string() -> None:
    out = tools.run("no-such-tool", {})
    assert out.startswith("error: unknown tool")


def test_run_dispatches_read(tmp_path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")
    out = tools.run("read", {"path": str(p)})
    assert "hello" in out
    assert "world" in out


def test_run_swallows_exceptions_as_strings(tmp_path) -> None:
    # Missing required arg -> KeyError -> caught and returned as "error: ...".
    out = tools.run("read", {})
    assert out.startswith("error:")


def test_specs_by_name_lookup() -> None:
    spec = tools.SPECS_BY_NAME["bash"]
    assert spec.read_only is False
    assert spec.schema["function"]["name"] == "bash"


def test_all_tool_names_matches_specs() -> None:
    assert tools.ALL_TOOL_NAMES == tuple(s.name for s in tools.SPECS)
