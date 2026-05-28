"""Native read-only tools: glob and grep."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from klimt import tools


def _make_tree(root: Path) -> None:
    (root / "a.py").write_text("import os\nprint('hello')\n")
    (root / "b.py").write_text("x = 1\ny = 2\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("def foo():\n    return 'world'\n")
    (sub / "d.txt").write_text("not python\n")
    (root / "README.md").write_text("# project\n")


def test_glob_basic(tmp_path: Path):
    _make_tree(tmp_path)
    out = tools.run("glob", {"pattern": "*.py"}, cwd=str(tmp_path))
    assert "a.py" in out
    assert "b.py" in out
    assert "sub/c.py" not in out  # non-recursive


def test_glob_recursive(tmp_path: Path):
    _make_tree(tmp_path)
    out = tools.run("glob", {"pattern": "**/*.py"}, cwd=str(tmp_path))
    assert "a.py" in out
    assert "b.py" in out
    assert "sub/c.py" in out


def test_glob_sorted_by_mtime(tmp_path: Path):
    _make_tree(tmp_path)
    # Bump mtime of b.py so it is newer.
    later = time.time() + 60
    os.utime(tmp_path / "b.py", (later, later))
    out = tools.run("glob", {"pattern": "*.py"}, cwd=str(tmp_path))
    body = out.split("\n\n", 1)[-1]
    lines = [line for line in body.splitlines() if line and not line.startswith("[")]
    assert lines[0] == "b.py"


def test_glob_no_matches(tmp_path: Path):
    _make_tree(tmp_path)
    out = tools.run("glob", {"pattern": "*.rs"}, cwd=str(tmp_path))
    assert "no matches" in out


def test_glob_bad_root(tmp_path: Path):
    out = tools.run("glob", {"pattern": "*", "path": str(tmp_path / "missing")}, cwd=str(tmp_path))
    assert out.startswith("error: path does not exist")


def test_glob_empty_pattern(tmp_path: Path):
    out = tools.run("glob", {"pattern": ""}, cwd=str(tmp_path))
    assert out.startswith("error: empty pattern")


@pytest.mark.skipif(shutil.which("ag") is None, reason="ag not installed")
def test_grep_basic(tmp_path: Path):
    _make_tree(tmp_path)
    out = tools.run("grep", {"pattern": "hello"}, cwd=str(tmp_path))
    assert "a.py" in out
    assert "hello" in out


@pytest.mark.skipif(shutil.which("ag") is None, reason="ag not installed")
def test_grep_case_insensitive(tmp_path: Path):
    _make_tree(tmp_path)
    out = tools.run("grep", {"pattern": "HELLO", "case_insensitive": True}, cwd=str(tmp_path))
    assert "a.py" in out


@pytest.mark.skipif(shutil.which("ag") is None, reason="ag not installed")
def test_grep_glob_filter(tmp_path: Path):
    _make_tree(tmp_path)
    out = tools.run("grep", {"pattern": "world", "glob": "\\.py$"}, cwd=str(tmp_path))
    assert "sub/c.py" in out
    # txt files excluded
    assert "d.txt" not in out


@pytest.mark.skipif(shutil.which("ag") is None, reason="ag not installed")
def test_grep_no_matches(tmp_path: Path):
    _make_tree(tmp_path)
    out = tools.run("grep", {"pattern": "nothing_here_at_all"}, cwd=str(tmp_path))
    assert "no matches" in out


def test_grep_missing_ag(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    out = tools.run("grep", {"pattern": "hello"}, cwd=str(tmp_path))
    assert out.startswith("error:")
    assert "ag" in out


def test_grep_empty_pattern(tmp_path: Path):
    out = tools.run("grep", {"pattern": ""}, cwd=str(tmp_path))
    assert out.startswith("error: empty pattern")
