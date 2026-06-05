"""Scheme validation for Api.open_url (the Python half of the nav guard).

The JS half of the nav guard preventDefaults every anchor click before
handing the href to this bridge, so the bridge is the last line of
defense against a rendered link triggering local navigation or invoking
arbitrary URL handlers.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

# pywebview is not installed in CI/test environments. Stub it before
# importing klimt.app so the module import succeeds.
sys.modules.setdefault("webview", types.ModuleType("webview"))

from klimt.tab_api import Api  # noqa: E402


def _api() -> Api:
    # Avoid running Api.__init__, which builds a real ChatSession and would
    # require provider config. We only need the open_url method.
    return Api.__new__(Api)


def test_open_url_allows_http_and_https() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        assert api.open_url("http://example.com")["ok"] is True
        assert api.open_url("https://example.com/x?y=1")["ok"] is True
        assert wb.call_count == 2


def test_open_url_allows_mailto() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        assert api.open_url("mailto:lars@example.com")["ok"] is True
        wb.assert_called_once()


def test_open_url_refuses_javascript_scheme() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        result = api.open_url("javascript:alert(1)")
        assert result == {"ok": False, "error": "refused scheme: javascript"}
        wb.assert_not_called()


def test_open_url_refuses_file_scheme() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        result = api.open_url("file:///etc/passwd")
        assert result == {"ok": False, "error": "refused scheme: file"}
        wb.assert_not_called()


def test_open_url_refuses_custom_scheme() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        result = api.open_url("klimt-evil://payload")
        assert result["ok"] is False
        assert "refused scheme" in result["error"]
        wb.assert_not_called()


def test_open_url_refuses_empty() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        assert api.open_url("")["ok"] is False
        assert api.open_url("   ")["ok"] is False
        assert api.open_url(None)["ok"] is False  # type: ignore[arg-type]
        wb.assert_not_called()


def test_open_url_refuses_bare_string_without_scheme() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        result = api.open_url("example.com/login")
        assert result["ok"] is False
        wb.assert_not_called()


def test_open_url_is_case_insensitive_on_scheme() -> None:
    api = _api()
    with patch("klimt.tab_api.webbrowser.open") as wb:
        assert api.open_url("HTTPS://example.com")["ok"] is True
        wb.assert_called_once()
