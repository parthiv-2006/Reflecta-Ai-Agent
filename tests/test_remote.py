"""Tests for remote key-broker mode (client side)."""

from types import SimpleNamespace

import pytest

from reflecta.llm import remote
from reflecta.llm.provider import BudgetExhausted, EmptyResponse


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeHTTPClient:
    """Records the last request and returns a scripted sequence of responses."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append(SimpleNamespace(url=url, json=json, headers=headers))
        return self._responses.pop(0)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Point config at a throwaway dir and clear token env so tests never read
    or write the real ~/.reflecta, and remote mode is off unless opted in."""
    monkeypatch.setenv("REFLECTA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("REFLECTA_TOKEN", raising=False)
    monkeypatch.delenv("REFLECTA_PROXY_URL", raising=False)


# ---------------------------------------------------------------------------
# Credential resolution + enable flag
# ---------------------------------------------------------------------------


def test_remote_disabled_by_default():
    assert remote.remote_enabled() is False
    assert remote.get_token() is None


def test_token_from_env_takes_precedence(monkeypatch):
    remote.save_credentials("file-token")
    monkeypatch.setenv("REFLECTA_TOKEN", "env-token")
    assert remote.get_token() == "env-token"
    assert remote.remote_enabled() is True


def test_token_from_credentials_file():
    remote.save_credentials("file-token", proxy_url="https://proxy.example")
    assert remote.get_token() == "file-token"
    assert remote.get_proxy_url() == "https://proxy.example"
    assert remote.remote_enabled() is True


def test_proxy_url_env_overrides_file(monkeypatch):
    remote.save_credentials("t", proxy_url="https://file.example")
    monkeypatch.setenv("REFLECTA_PROXY_URL", "https://env.example")
    assert remote.get_proxy_url() == "https://env.example"


def test_proxy_url_defaults_when_unset():
    assert remote.get_proxy_url() == remote.DEFAULT_PROXY_URL


def test_save_and_clear_credentials():
    remote.save_credentials("tok")
    assert remote.credentials_path().exists()
    assert remote.clear_credentials() is True
    assert not remote.credentials_path().exists()
    # Clearing again is a no-op returning False.
    assert remote.clear_credentials() is False


# ---------------------------------------------------------------------------
# complete() — the proxy round-trip
# ---------------------------------------------------------------------------


def test_complete_posts_expected_request_and_returns_text():
    http = _FakeHTTPClient(
        _FakeResponse(200, {"text": "from m import f\n\ndef test(): assert f()"})
    )
    out = remote.complete(
        "PROMPT",
        task="generate",
        model="gemini-2.5-flash",
        http_client=http,
        token="tok123",
        proxy_url="https://proxy.example",
    )
    assert out == "from m import f\n\ndef test(): assert f()"
    call = http.calls[0]
    assert call.url == "https://proxy.example/v1/complete"
    assert call.headers["Authorization"] == "Bearer tok123"
    assert call.json == {
        "task": "generate",
        "prompt": "PROMPT",
        "model": "gemini-2.5-flash",
    }


def test_complete_strips_code_fences():
    http = _FakeHTTPClient(
        _FakeResponse(200, {"text": "```python\ndef test(): assert 1\n```"})
    )
    out = remote.complete(
        "p", task="repair", model="llama-3.1-8b-instant", http_client=http, token="t"
    )
    assert out == "def test(): assert 1"


def test_complete_empty_text_raises_empty_response():
    http = _FakeHTTPClient(_FakeResponse(200, {"text": ""}))
    with pytest.raises(EmptyResponse):
        remote.complete(
            "p", task="generate", model="gemini-2.5-flash", http_client=http, token="t"
        )


def test_complete_429_retries_then_budget_exhausted(monkeypatch):
    monkeypatch.setattr("reflecta.llm.provider.time.sleep", lambda s: None)
    http = _FakeHTTPClient(*[_FakeResponse(429, text="quota") for _ in range(6)])
    with pytest.raises(BudgetExhausted):
        remote.complete(
            "p", task="generate", model="gemini-2.5-flash", http_client=http, token="t"
        )


def test_complete_non_200_raises_runtime_error():
    http = _FakeHTTPClient(_FakeResponse(500, text="boom"))
    with pytest.raises(RuntimeError):
        remote.complete(
            "p", task="generate", model="gemini-2.5-flash", http_client=http, token="t"
        )


def test_complete_requires_a_token():
    with pytest.raises(EnvironmentError):
        remote.complete(
            "p",
            task="generate",
            model="gemini-2.5-flash",
            http_client=_FakeHTTPClient(),
        )


# ---------------------------------------------------------------------------
# gemini/groq clients route through the proxy in remote mode
# ---------------------------------------------------------------------------


def test_gemini_generate_uses_remote_when_enabled(monkeypatch):
    monkeypatch.setenv("REFLECTA_TOKEN", "tok")
    captured = {}

    def fake_complete(prompt, *, task, model):
        captured.update(prompt=prompt, task=task, model=model)
        return "remote result"

    monkeypatch.setattr(remote, "complete", fake_complete)
    from reflecta.llm import gemini

    out = gemini.generate("PROMPT")
    assert out == "remote result"
    assert captured == {"prompt": "PROMPT", "task": "generate", "model": gemini.MODEL}


def test_groq_repair_uses_remote_when_enabled(monkeypatch):
    monkeypatch.setenv("REFLECTA_TOKEN", "tok")
    captured = {}

    def fake_complete(prompt, *, task, model):
        captured.update(prompt=prompt, task=task, model=model)
        return "patched"

    monkeypatch.setattr(remote, "complete", fake_complete)
    from reflecta.llm import groq

    out = groq.repair("PROMPT", model=groq.MODEL_HARD)
    assert out == "patched"
    assert captured == {"prompt": "PROMPT", "task": "repair", "model": groq.MODEL_HARD}


def test_explicit_client_bypasses_remote_even_when_enabled(monkeypatch):
    """An injected SDK client (tests/dev) must win over remote mode."""
    monkeypatch.setenv("REFLECTA_TOKEN", "tok")
    monkeypatch.setattr(
        remote, "complete", lambda *a, **k: pytest.fail("should not hit remote")
    )
    from reflecta.llm import gemini

    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda *, model, contents: SimpleNamespace(text="direct")
        )
    )
    assert gemini.generate("p", client=fake_client) == "direct"
