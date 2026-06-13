"""Claude Haiku text-only generation client — Gemini overflow path.

Uses the same httpx + OAuth-token auto-detection as escalate._ClaudeClient,
but stripped down to a single text-completion call. Activated only after
Gemini raises BudgetExhausted so the Pro subscription is used minimally.

Overflow is capped at MAX_OVERFLOW calls per process (default 20, override
with REFLECTA_CLAUDE_OVERFLOW env var) so large repos never drain the
subscription unexpectedly.
"""

from __future__ import annotations

import os

import httpx

from reflecta.llm.provider import (
    EmptyResponse,
    RateLimitError,
    call_with_retry,
    strip_fences,
)

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096

_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_OAUTH_BETA = "oauth-2025-04-20"
_CLAUDE_CODE_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."
_ROUND_TRIP_TIMEOUT_S = 60.0

# Per-process overflow counter — reset between test cases via monkeypatch.
_overflow_used: int = 0
MAX_OVERFLOW: int = int(os.environ.get("REFLECTA_CLAUDE_OVERFLOW", "20"))


def _make_headers(token: str) -> dict:
    headers = {
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    if token.startswith("sk-ant-oat"):
        headers["authorization"] = f"Bearer {token}"
        headers["anthropic-beta"] = _OAUTH_BETA
    else:
        headers["x-api-key"] = token
    return headers


def generate(prompt: str, *, client=None) -> str:
    """Generate test code via Claude Haiku.

    ``client`` is injectable for tests — any object with a ``post(url, json=,
    headers=)`` method that returns a response with ``.status_code`` and
    ``.json()``.  When None, builds a real httpx client from ANTHROPIC_API_KEY.

    The overflow cap check and counter increment are managed by router.py so
    that test doubles injected via monkeypatch still participate in budget
    accounting.
    """
    global _overflow_used

    token = os.environ.get("ANTHROPIC_API_KEY", "")
    owns_client = client is None
    if owns_client:
        if not token:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. Add it to .env or export it. "
                "Without it the Claude overflow path cannot activate."
            )
        headers = _make_headers(token)
        client = httpx.Client(
            timeout=httpx.Timeout(_ROUND_TRIP_TIMEOUT_S), headers=headers
        )

    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": _CLAUDE_CODE_SYSTEM}],
        "messages": [{"role": "user", "content": prompt}],
    }

    def _call():
        try:
            resp = client.post(_API_URL, json=body)
        except Exception as exc:
            raise RuntimeError(f"Claude API request failed: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitError(
                f"Claude API rate limited: {resp.text[:200]}",
                provider="Claude Haiku (generation overflow)",
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Claude API {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        blocks = data.get("content", [])
        for block in blocks:
            if block.get("type") == "text":
                return block.get("text")
        return None

    try:
        raw = call_with_retry(_call)
    finally:
        if owns_client:
            client.close()

    if not raw:
        raise EmptyResponse("Claude Haiku returned an empty response")

    return strip_fences(raw)
