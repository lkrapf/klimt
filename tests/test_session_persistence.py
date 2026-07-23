"""Deferred-persistence behavior: new sessions stay in memory until kept."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from klimt.api import ChatSession
from klimt.session_store import SessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    s = SessionStore.__new__(SessionStore)
    s.folder = str(tmp_path / "folder")
    s.root = tmp_path / "store"
    return s


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch):
    # ChatSession.__post_init__ builds a real client otherwise.
    monkeypatch.setattr(ChatSession, "reload_client", lambda self: None)


def _session(store: SessionStore, name: str = "session-test") -> ChatSession:
    return ChatSession(model="m", system="sys", store=store, session_name=name)


def test_new_session_is_not_kept(store):
    s = _session(store)
    assert s.kept is False


def test_persist_is_noop_until_kept(store):
    s = _session(store)
    s.history.append({"role": "user", "content": "hi"})
    s.persist()
    assert not store.root.exists() or not list(store.root.glob("*.json"))
    assert store.list() == []


def test_autotitle_does_not_persist(store):
    s = _session(store)
    s.maybe_title_from_first_input("what tar flag preserves perms")
    assert s.session_name == "what-tar-flag-preserves-perms"
    s.persist()
    assert store.list() == []  # labelled, but still in memory only


def test_keep_promotes_with_current_name(store):
    s = _session(store)
    s.maybe_title_from_first_input("remind me about chmod")
    s.history.append({"role": "user", "content": "remind me about chmod"})
    s.keep()
    assert s.kept is True
    assert [x["name"] for x in store.list()] == ["remind-me-about-chmod"]


def test_keep_with_explicit_name(store):
    s = _session(store)
    s.keep("my-real-work")
    assert s.session_name == "my-real-work"
    assert [x["name"] for x in store.list()] == ["my-real-work"]


def test_kept_session_persists_subsequent_turns(store):
    s = _session(store)
    s.keep("kept")
    s.history.append({"role": "user", "content": "later turn"})
    s.persist()
    loaded = store.load("kept")
    assert loaded is not None
    assert loaded["history"][-1]["content"] == "later turn"


def test_rename_marks_kept_and_writes(store):
    s = _session(store)
    s.rename_session("named-it")
    assert s.kept is True
    assert [x["name"] for x in store.list()] == ["named-it"]


def test_rename_unkept_does_not_delete_phantom_old(store):
    # An unkept session was never on disk, so renaming must not try to delete
    # a file under the old auto-title name (no-op delete is fine, just no crash).
    s = _session(store, name="session-orig")
    s.rename_session("final")
    assert [x["name"] for x in store.list()] == ["final"]


def test_load_session_marks_kept(store):
    s = _session(store)
    s.keep("on-disk")
    s.history.append({"role": "user", "content": "content"})
    s.persist()

    other = _session(store, name="scratch")
    assert other.kept is False
    assert other.load_session("on-disk") is True
    assert other.kept is True


def test_active_goal_persists_and_restores(store):
    from klimt.goal import Goal
    s = _session(store)
    s.keep("with-goal")
    s.goal = Goal(condition="all tests pass", max_turns=9)
    s.persist()

    other = _session(store, name="scratch")
    assert other.load_session("with-goal") is True
    assert other.goal is not None
    assert other.goal.condition == "all tests pass"
    assert other.goal.max_turns == 9


def test_no_goal_is_not_persisted(store):
    s = _session(store)
    s.keep("no-goal")
    s.persist()
    loaded = store.load("no-goal")
    assert loaded is not None
    assert "goal" not in loaded
