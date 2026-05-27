"""Anthropic OAuth 2.0 Authorization Code + PKCE support."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import queue
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 53692
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
SCOPES = " ".join([
    "org:create_api_key",
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
])
TOKEN_EXCHANGE_TIMEOUT = 30
LOGIN_TIMEOUT = 300
EXPIRY_SKEW = 300
KLIMT_USER_AGENT = "Klimt/0.1"
STORE_PATH = Path.home() / ".klimt" / "anthropic-oauth.json"
LOCK_PATH = Path.home() / ".klimt" / "anthropic-oauth.lock"


class OAuthError(RuntimeError):
    """Anthropic OAuth failed."""


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


def access_token() -> str:
    """Return a valid Anthropic OAuth access token, logging in or refreshing if needed."""
    with _FileLock(LOCK_PATH):
        data = _load_store()
        if _valid(data):
            return str(data["access_token"])

        refresh = str(data.get("refresh_token") or "")
        if refresh:
            try:
                refreshed = _refresh(refresh)
            except Exception:
                pass
            else:
                _save_store(refreshed)
                return str(refreshed["access_token"])

        fresh = _login()
        _save_store(fresh)
        return str(fresh["access_token"])


def _valid(data: dict[str, Any]) -> bool:
    return bool(data.get("access_token")) and float(data.get("expires_at") or 0) > time.time()


def _load_store() -> dict[str, Any]:
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise OAuthError(f"invalid Anthropic OAuth store: {STORE_PATH}") from exc


def _save_store(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(STORE_PATH)
    with contextlib.suppress(FileNotFoundError):
        os.chmod(STORE_PATH, 0o600)


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _login() -> dict[str, Any]:
    verifier, challenge = _pkce_pair()
    state = verifier
    code_queue: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)
    server = _callback_server(state, code_queue)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    input_thread = threading.Thread(target=_manual_code_entry, args=(code_queue,), daemon=True)
    if sys.stdin.isatty():
        input_thread.start()
    try:
        url = _authorize_url(challenge, state)
        webbrowser.open(url)
        if sys.stdin.isatty():
            print(f"Anthropic OAuth URL:\n{url}\n", file=sys.stderr)
        try:
            result = code_queue.get(timeout=LOGIN_TIMEOUT)
        except queue.Empty as exc:
            raise OAuthError("timed out waiting for Anthropic OAuth callback") from exc
        if isinstance(result, BaseException):
            raise OAuthError(str(result)) from result
        return _exchange_code(result, state, verifier)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _authorize_url(challenge: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })


def _callback_server(expected_state: str, code_queue: queue.Queue[str | BaseException]) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/callback":
                self._reply(404, "not found")
                return
            state = (qs.get("state") or [""])[0]
            code = (qs.get("code") or [""])[0]
            if state != expected_state:
                self._put(OAuthError("OAuth state mismatch"))
                self._reply(400, "OAuth state mismatch. You can close this tab.")
                return
            if not code:
                self._put(OAuthError("OAuth callback did not include a code"))
                self._reply(400, "OAuth callback did not include a code. You can close this tab.")
                return
            self._put(code)
            self._reply(200, "Anthropic OAuth complete. You can close this tab.")

        def _put(self, value: str | BaseException) -> None:
            with contextlib.suppress(queue.Full):
                code_queue.put_nowait(value)

        def _reply(self, status: int, body: str) -> None:
            raw = f"<!doctype html><title>Klimt</title><p>{body}</p>".encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)


def _manual_code_entry(code_queue: queue.Queue[str | BaseException]) -> None:
    with contextlib.suppress(Exception):
        value = input("Paste Anthropic OAuth redirect URL or code, or press Enter to wait for browser callback: ").strip()
        if value:
            with contextlib.suppress(queue.Full):
                code_queue.put_nowait(_parse_manual_code(value))


def _parse_manual_code(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    if parsed.query:
        code = (urllib.parse.parse_qs(parsed.query).get("code") or [""])[0]
        if code:
            return code
    if value.startswith("?"):
        code = (urllib.parse.parse_qs(value[1:]).get("code") or [""])[0]
        if code:
            return code
    return value


def _exchange_code(code: str, state: str, verifier: str) -> dict[str, Any]:
    return _token_request({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "state": state,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })


def _refresh(refresh_token: str) -> dict[str, Any]:
    return _token_request({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    })


def _token_request(payload: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        TOKEN_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": KLIMT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TOKEN_EXCHANGE_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OAuthError(f"Anthropic OAuth token error {exc.code}: {body}") from exc

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        raise OAuthError("Anthropic OAuth token response did not include both access_token and refresh_token")
    expires_in = max(0, int(data.get("expires_in") or 0))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": time.time() + max(0, expires_in - EXPIRY_SKEW),
    }
