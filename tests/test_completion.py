from __future__ import annotations

from dataclasses import dataclass

from klimt import completion


@dataclass
class FakeSession:
    cwd: str

    def list_sessions(self):
        return [
            {"name": "alpha-work"},
            {"name": "beta work"},
        ]


def values(result):
    return [x["value"] for x in result["items"]]


def test_slash_commands_complete():
    result = completion.complete(FakeSession("/tmp"), "/mo", 3)
    assert "/model" in values(result)
    assert result["range"] == {"start": 0, "end": 3}


def test_cd_completes_directories_only(tmp_path):
    (tmp_path / "dir-one").mkdir()
    (tmp_path / "file-one").write_text("x")

    result = completion.complete(FakeSession(str(tmp_path)), "/cd d", 5)

    assert values(result) == ["dir-one/"]
    assert result["range"] == {"start": 4, "end": 5}


def test_bang_path_completion_is_offset_after_bang(tmp_path):
    (tmp_path / "some file.txt").write_text("x")

    result = completion.complete(FakeSession(str(tmp_path)), "!cat so", 7)

    assert values(result) == ["some\\ file.txt"]
    assert result["range"] == {"start": 5, "end": 7}


def test_regular_prompt_path_completion_uses_active_token(tmp_path):
    (tmp_path / "PLAN.md").write_text("x")

    result = completion.complete(FakeSession(str(tmp_path)), "read PL please", 7)

    assert values(result) == ["PLAN.md"]
    assert result["range"] == {"start": 5, "end": 7}


def test_quoted_path_completion_preserves_spaces(tmp_path):
    (tmp_path / "some file.txt").write_text("x")

    result = completion.complete(FakeSession(str(tmp_path)), "open 'so", 8)

    assert values(result) == ["some file.txt"]
    assert result["range"] == {"start": 6, "end": 8}


def test_sessions_complete_names():
    result = completion.complete(FakeSession("/tmp"), "/sessions resume a", 18)

    assert values(result) == ["alpha-work"]
    assert result["range"] == {"start": 17, "end": 18}
