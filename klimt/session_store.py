"""Per-folder persistent chat sessions for Klimt."""
from __future__ import annotations

import hashlib
import contextlib
import json
import os
import time
import secrets
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

SESSIONS_DIR = Path.home() / ".klimt" / "sessions"
DEFAULT_SESSION = "default"  # legacy: still loadable, no longer used for new sessions
UNTITLED_PREFIX = "session-"


def random_session_name() -> str:
    return time.strftime("session-%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def title_from_prompt(text: str, max_len: int = 48) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    title = "-".join(words)[:max_len].strip("-")
    return title or random_session_name()


def _folder_key(folder: str) -> str:
    resolved = str(Path(folder).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:24]


def _name_key(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]


class SessionStore:
    """Stores named sessions under ~/.klimt/sessions/<folder-hash>/."""

    def __init__(self, folder: str | None = None) -> None:
        self.folder = str(Path(folder or os.getcwd()).expanduser().resolve())
        self.root = SESSIONS_DIR / _folder_key(self.folder)

    def _path(self, name: str) -> Path:
        return self.root / f"{_name_key(name)}.json"

    def exists(self, name: str) -> bool:
        name = (name or DEFAULT_SESSION).strip() or DEFAULT_SESSION
        if self._path(name).exists():
            return True
        if not self.root.exists():
            return False
        for p in self.root.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if data.get("name") == name:
                return True
        return False

    def unique_name(self, wanted: str) -> str:
        wanted = (wanted or random_session_name()).strip() or random_session_name()
        if not self.exists(wanted):
            return wanted
        for _ in range(20):
            candidate = f"{wanted}-{secrets.token_hex(3)}"
            if not self.exists(candidate):
                return candidate
        return random_session_name()

    def delete(self, name: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(name).unlink()

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def for_folder(self, folder: str) -> "SessionStore":
        return type(self)(folder)

    def save(
        self,
        name: str,
        history: List[Dict[str, Any]],
        input_history: List[str],
        model: str | None = None,
        cwd: str | None = None,
    ) -> None:
        name = (name or DEFAULT_SESSION).strip() or DEFAULT_SESSION
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": name,
            "folder": self.folder,
            "updated": time.time(),
            "history": history,
            "input_history": input_history,
            "model": model,
            "cwd": cwd or self.folder,
        }
        tmp = self._path(name).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path(name))

    def load(self, name: str) -> Optional[Dict[str, Any]]:
        name = (name or DEFAULT_SESSION).strip() or DEFAULT_SESSION
        direct = self._path(name)
        candidates = [direct] if direct.exists() else list(self.root.glob("*.json"))
        for p in candidates:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if data.get("name") == name:
                return data
        return None

    def list(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not self.root.exists():
            return out
        for p in self.root.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            out.append({
                "name": data.get("name") or p.stem,
                "updated": float(data.get("updated") or 0),
                "messages": len(data.get("history") or []),
                "inputs": len(data.get("input_history") or []),
                "model": data.get("model") or "",
                "cwd": data.get("cwd") or data.get("folder") or "",
            })
        out.sort(key=lambda x: x["updated"], reverse=True)
        return out
