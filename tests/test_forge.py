"""Tests for forge.py — GitHub PR host, driven via httpx.MockTransport.

No network: every request is answered by an in-process handler so we can assert
on the exact URL, payload, and auth header reflecta sends.
"""

import httpx
import pytest

from reflecta import forge


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _host(handler) -> forge.GitHubHost:
    return forge.GitHubHost("owner", "repo", "tok-123", client=_client(handler))


def test_open_pull_request_posts_payload_and_returns_pr():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"number": 7, "html_url": "https://gh/pr/7"})

    pr = _host(handler).open_pull_request(
        title="T", body="B", head="reflecta/auto-tests", base="main"
    )

    assert pr == forge.PullRequest(number=7, url="https://gh/pr/7")
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/repos/owner/repo/pulls")
    assert seen["auth"] == "Bearer tok-123"
    assert seen["body"] == {
        "title": "T",
        "body": "B",
        "head": "reflecta/auto-tests",
        "base": "main",
    }


def test_find_open_pr_returns_existing():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "head=owner%3Areflecta%2Fauto-tests" in str(request.url)
        assert "state=open" in str(request.url)
        return httpx.Response(200, json=[{"number": 3, "html_url": "https://gh/pr/3"}])

    pr = _host(handler).find_open_pr("reflecta/auto-tests")
    assert pr == forge.PullRequest(number=3, url="https://gh/pr/3")


def test_find_open_pr_returns_none_when_empty():
    def handler(request):
        return httpx.Response(200, json=[])

    assert _host(handler).find_open_pr("reflecta/auto-tests") is None


def test_open_pull_request_raises_forge_error_on_422():
    def handler(request):
        return httpx.Response(422, json={"message": "A pull request already exists"})

    with pytest.raises(forge.ForgeError) as exc:
        _host(handler).open_pull_request(title="T", body="B", head="h", base="main")
    assert "could not open pull request" in str(exc.value)
    assert "already exists" in str(exc.value)


def test_error_message_does_not_leak_token():
    def handler(request):
        return httpx.Response(403, json={"message": "Forbidden"})

    with pytest.raises(forge.ForgeError) as exc:
        _host(handler).find_open_pr("b")
    assert "tok-123" not in str(exc.value)


def test_host_from_repo_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(forge.ForgeError) as exc:
        forge.host_from_repo("/tmp/whatever")
    assert "GITHUB_TOKEN" in str(exc.value)
