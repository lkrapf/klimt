"""GitHub Copilot OAuth device-code flow support.

Mirrors how the official Copilot CLI/editor integrations authenticate: a GitHub
OAuth device-code login yields a long-lived GitHub token, which is exchanged
for a short-lived Copilot API token via GitHub's internal token-exchange
endpoint. Only the Copilot token is sent to `api.githubcopilot.com`; the
long-lived GitHub token never touches the model API.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable

OnDeviceCode = Callable[[str, str], None]

CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
SCOPE = "read:user"
EDITOR_HEADERS = {
    "User-Agent": "GithubCopilot/1.155.0",
    "Editor-Version": "Klimt/0.1",
    "Editor-Plugin-Version": "klimt/0.1",
}
LOGIN_TIMEOUT = 300
EXPIRY_SKEW = 300
STORE_PATH = Path.home() / ".klimt" / "copilot-oauth.json"
LOCK_PATH = Path.home() / ".klimt" / "copilot-oauth.lock"


class OAuthError(RuntimeError):
    """GitHub Copilot OAuth failed."""


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any = None

    def __enter__(self) -> "_FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+", encoding="utf-8")
        try:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fh is None:
            return
        try:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        self._fh.close()
        self._fh = None


def access_token(on_device_code: OnDeviceCode | None = None) -> str:
    """Return a valid Copilot API token, exchanging or logging in as needed.

    `on_device_code`, if given, is called with `(verification_uri, user_code)`
    when an interactive device-code login is required, so a caller can surface
    the prompt somewhere other than the terminal (e.g. the chat window).
    """
    with _FileLock(LOCK_PATH):
        data = _load_store()
        if _valid(data):
            return str(data["copilot_token"])

        github_token = str(data.get("github_token") or "")
        if github_token:
            try:
                copilot_token, expires_at = _exchange_copilot_token(github_token)
            except Exception:
                github_token = ""
            else:
                data["copilot_token"] = copilot_token
                data["expires_at"] = expires_at
                _save_store(data)
                return copilot_token

        fresh = _login(on_device_code)
        _save_store(fresh)
        return str(fresh["copilot_token"])


def _valid(data: dict[str, Any]) -> bool:
    return bool(data.get("copilot_token")) and float(data.get("expires_at") or 0) > time.time()


def _load_store() -> dict[str, Any]:
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise OAuthError(f"invalid Copilot OAuth store: {STORE_PATH}") from exc


def _save_store(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(STORE_PATH)
    with contextlib.suppress(FileNotFoundError):
        os.chmod(STORE_PATH, 0o600)


def _login(on_device_code: OnDeviceCode | None) -> dict[str, Any]:
    device = _request_device_code()
    verification_uri = str(device["verification_uri"])
    user_code = str(device["user_code"])
    print(
        f"GitHub Copilot login: open {verification_uri} and enter code {user_code}\n",
        flush=True,
    )
    if on_device_code is not None:
        with contextlib.suppress(Exception):
            on_device_code(verification_uri, user_code)
    with contextlib.suppress(Exception):
        webbrowser.open(verification_uri)

    github_token = _poll_for_access_token(
        device_code=str(device["device_code"]),
        interval=float(device.get("interval") or 5),
        expires_in=float(device.get("expires_in") or LOGIN_TIMEOUT),
    )
    copilot_token, expires_at = _exchange_copilot_token(github_token)
    return {
        "github_token": github_token,
        "copilot_token": copilot_token,
        "expires_at": expires_at,
    }


def _request_device_code() -> dict[str, Any]:
    data = _form_request(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPE})
    if not data.get("device_code") or not data.get("user_code") or not data.get("verification_uri"):
        raise OAuthError("GitHub device code response missing required fields")
    return data


def _poll_for_access_token(device_code: str, interval: float, expires_in: float) -> str:
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(max(1.0, interval))
        data = _form_request(ACCESS_TOKEN_URL, {
            "client_id": CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        token = data.get("access_token")
        if token:
            return str(token)
        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = float(data.get("interval") or interval + 5)
            continue
        raise OAuthError(f"GitHub device login failed: {error or 'unknown error'}")
    raise OAuthError("timed out waiting for GitHub device login")


def _exchange_copilot_token(github_token: str) -> tuple[str, float]:
    req = urllib.request.Request(
        COPILOT_TOKEN_URL,
        headers={
            "Authorization": f"token {github_token}",
            "Accept": "application/json",
            **EDITOR_HEADERS,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OAuthError(f"Copilot token exchange error {exc.code}: {body}") from exc

    token = data.get("token")
    if not token:
        raise OAuthError("Copilot token exchange response missing token")
    expires_at = float(data.get("expires_at") or 0)
    return str(token), max(0.0, expires_at - EXPIRY_SKEW)


def _form_request(url: str, payload: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("ascii"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            **EDITOR_HEADERS,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OAuthError(f"GitHub OAuth request error {exc.code}: {body}") from exc
