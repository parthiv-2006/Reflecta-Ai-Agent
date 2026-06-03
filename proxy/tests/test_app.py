"""Tests for the reflecta proxy. Provider calls are stubbed — no real keys."""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

# The proxy is a standalone project (not the reflecta package); import app.py
# directly from the parent dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ProxyConfig, create_app  # noqa: E402


def _client(quota: int = 5):
    config = ProxyConfig(
        tokens={"good-token": quota},
        generate_fn=lambda prompt, model: f"GEN:{model}:{prompt}",
        repair_fn=lambda prompt, model: f"FIX:{model}:{prompt}",
    )
    return TestClient(create_app(config))


def _post(client, token, task="generate", model="gemini-2.5-flash", prompt="p"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/v1/complete",
        json={"task": task, "prompt": prompt, "model": model},
        headers=headers,
    )


def test_healthz():
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_generate_happy_path():
    r = _post(
        _client(), "good-token", task="generate", model="gemini-2.5-flash", prompt="hi"
    )
    assert r.status_code == 200
    assert r.json()["text"] == "GEN:gemini-2.5-flash:hi"


def test_repair_routes_to_repair_fn():
    r = _post(
        _client(), "good-token", task="repair", model="llama-3.1-8b-instant", prompt="x"
    )
    assert r.status_code == 200
    assert r.json()["text"].startswith("FIX:llama-3.1-8b-instant:")


def test_missing_token_is_401():
    assert _post(_client(), None).status_code == 401


def test_unknown_token_is_401():
    assert _post(_client(), "nope").status_code == 401


def test_unknown_task_is_400():
    assert _post(_client(), "good-token", task="translate").status_code == 400


def test_disallowed_model_is_400():
    r = _post(_client(), "good-token", task="generate", model="gpt-4o")
    assert r.status_code == 400


def test_repair_model_not_valid_for_generate():
    r = _post(_client(), "good-token", task="generate", model="llama-3.1-8b-instant")
    assert r.status_code == 400


def test_prompt_too_large_is_413():
    config = ProxyConfig(
        tokens={"good-token": 5},
        generate_fn=lambda p, m: "x",
        repair_fn=lambda p, m: "x",
        max_prompt_chars=10,
    )
    client = TestClient(create_app(config))
    r = _post(client, "good-token", prompt="x" * 11)
    assert r.status_code == 413


def test_quota_enforced_returns_429_when_exceeded():
    client = _client(quota=2)
    assert _post(client, "good-token").status_code == 200
    assert _post(client, "good-token").status_code == 200
    # Third call exceeds the daily quota.
    assert _post(client, "good-token").status_code == 429


def test_provider_error_is_502():
    def boom(prompt, model):
        raise RuntimeError("upstream down")

    config = ProxyConfig(tokens={"good-token": 5}, generate_fn=boom, repair_fn=boom)
    client = TestClient(create_app(config))
    assert _post(client, "good-token").status_code == 502


def test_parse_tokens_formats():
    from app import _parse_tokens

    assert _parse_tokens("a,b", 100) == {"a": 100, "b": 100}
    assert _parse_tokens('{"a": 50}', 100) == {"a": 50}
    assert _parse_tokens('{"a": {"daily_quota": 7}}', 100) == {"a": 7}
    assert _parse_tokens("", 100) == {}
