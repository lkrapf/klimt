from klimt.model_config import ModelConfig
from klimt.providers import ChatProvider


def _fake_openai_client():
    class _FakeCompletions:
        def create(self, **kwargs):
            return "ok"

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    return _FakeClient()


def test_copilot_oauth_provider_defers_client_creation_without_api_key_env():
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")

    provider = ChatProvider(cfg)

    assert provider._copilot_oauth is True
    assert provider.client is None


def test_copilot_static_api_key_builds_client_with_headers_and_base_url(monkeypatch):
    monkeypatch.setenv("COPILOT_TOKEN", "static-token")
    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1", api_key_env="COPILOT_TOKEN")

    provider = ChatProvider(cfg)

    assert provider._copilot_oauth is False
    assert provider.client is not None
    assert str(provider.client.base_url).rstrip("/") == "https://api.githubcopilot.com"
    assert provider.client.default_headers["Copilot-Integration-Id"] == "vscode-chat"


def test_copilot_oauth_complete_fetches_fresh_token_per_call(monkeypatch):
    calls = []

    def fake_access_token(on_device_code=None):
        calls.append(1)
        return "fresh-token"

    monkeypatch.setattr("klimt.providers.copilot_oauth.access_token", fake_access_token)

    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")
    provider = ChatProvider(cfg)

    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return "ok"

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(ChatProvider, "_make_client", staticmethod(lambda config, api_key: _FakeClient()))

    result = provider.complete([{"role": "user", "content": "hi"}], 100)

    assert result == "ok"
    assert calls == [1]
    assert captured["model"] == "gpt-4.1"
    assert captured["max_completion_tokens"] == 100


def test_copilot_oauth_emits_device_code_prompt_into_chat(monkeypatch):
    captured_on_device_code = {}

    def fake_access_token(on_device_code=None):
        captured_on_device_code["cb"] = on_device_code
        if on_device_code is not None:
            on_device_code("https://github.com/login/device", "ABCD-1234")
        return "fresh-token"

    monkeypatch.setattr("klimt.providers.copilot_oauth.access_token", fake_access_token)
    monkeypatch.setattr(ChatProvider, "_make_client", staticmethod(lambda config, api_key: _fake_openai_client()))

    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")
    provider = ChatProvider(cfg)

    events = []
    provider.complete([{"role": "user", "content": "hi"}], 100, emit=events.append)

    assert captured_on_device_code["cb"] is not None
    assert len(events) == 1
    assert events[0]["type"] == "text"
    assert "ABCD-1234" in events[0]["content"]
    assert "https://github.com/login/device" in events[0]["content"]


def test_copilot_oauth_without_emit_passes_no_device_code_callback(monkeypatch):
    seen = {}

    def fake_access_token(on_device_code=None):
        seen["cb"] = on_device_code
        return "fresh-token"

    monkeypatch.setattr("klimt.providers.copilot_oauth.access_token", fake_access_token)
    monkeypatch.setattr(ChatProvider, "_make_client", staticmethod(lambda config, api_key: _fake_openai_client()))

    cfg = ModelConfig(name="copilot-gpt", provider="copilot", model="gpt-4.1")
    provider = ChatProvider(cfg)

    provider.complete([{"role": "user", "content": "hi"}], 100)

    assert seen["cb"] is None
