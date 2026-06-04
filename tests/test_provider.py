from types import SimpleNamespace

import pytest

from reflecta.llm.provider import (
    BudgetExhausted,
    EmptyResponse,
    RateLimitError,
    call_with_retry,
    explain_rate_limit,
    strip_fences,
)


def test_explain_rate_limit_distinguishes_daily_and_minute():
    assert "DAILY" in explain_rate_limit("Quota exceeded: requests per day")
    assert "PER-MINUTE" in explain_rate_limit("rate limit: requests per minute (RPM)")
    # Unknown phrasing falls back to the general hint, never crashes.
    assert explain_rate_limit("429 Too Many Requests")


def test_budget_exhausted_message_names_provider_and_cause(monkeypatch):
    monkeypatch.setattr("reflecta.llm.provider.time.sleep", lambda s: None)

    def always():
        raise RateLimitError(
            "RESOURCE_EXHAUSTED: requests per day exceeded",
            provider="Gemini (test generation)",
        )

    with pytest.raises(BudgetExhausted) as ei:
        call_with_retry(always, max_retries=2, base_delay=1.0)

    msg = str(ei.value)
    assert "Gemini (test generation)" in msg  # which provider
    assert "429" in msg  # what kind of failure
    assert "DAILY" in msg  # actionable hint
    assert "requests per day" in msg  # raw API text echoed


def test_strip_fences_removes_language_fence():
    raw = "```python\nfrom calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n```"
    assert (
        strip_fences(raw)
        == "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3"
    )


def test_strip_fences_noop_without_fence():
    raw = "def test_x():\n    assert True\n"
    assert strip_fences(raw) == "def test_x():\n    assert True"


def test_strip_fences_concatenates_multiple_blocks():
    # Gemini frequently emits prose between several code fences. The previous
    # non-greedy regex kept only the FIRST block, producing a truncated file
    # missing imports/fixtures. We must reassemble every python block in order.
    raw = (
        "Here are the imports:\n"
        "```python\n"
        "from unittest import mock\n"
        "from calc import add\n"
        "```\n"
        "And the test itself:\n"
        "```python\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n"
        "```\n"
    )
    out = strip_fences(raw)
    assert "from unittest import mock" in out
    assert "from calc import add" in out
    assert "def test_add():" in out
    # The reassembled file must be valid, importable Python.
    import ast

    ast.parse(out)


def test_strip_fences_handles_py_and_bare_language_tags():
    raw = "```py\nx = 1\n```"
    assert strip_fences(raw) == "x = 1"


def test_strip_fences_ignores_empty_blocks():
    raw = "```python\n```\n```python\nx = 1\n```"
    assert strip_fences(raw) == "x = 1"


def test_success_no_delay(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(
        "reflecta.llm.provider.time.sleep", lambda s: sleep_calls.append(s)
    )

    result = call_with_retry(lambda: "ok")

    assert result == "ok"
    assert sleep_calls == []


def test_retries_429_three_times_then_success(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(
        "reflecta.llm.provider.time.sleep", lambda s: sleep_calls.append(s)
    )

    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise RateLimitError("429")
        return "ok"

    result = call_with_retry(flaky, max_retries=5, base_delay=1.0)

    assert result == "ok"
    assert call_count == 4
    assert sleep_calls == [1.0, 2.0, 4.0]


def test_budget_exhausted(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(
        "reflecta.llm.provider.time.sleep", lambda s: sleep_calls.append(s)
    )

    def always_429():
        raise RateLimitError("429")

    with pytest.raises(BudgetExhausted):
        call_with_retry(always_429, max_retries=3, base_delay=1.0)

    assert len(sleep_calls) == 3
    assert sleep_calls == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# AUDIT M2 — empty/None provider responses raise EmptyResponse, not crash
# ---------------------------------------------------------------------------


def test_gemini_none_text_raises_empty_response():
    from reflecta.llm import gemini

    class _FakeModels:
        def generate_content(self, *, model, contents):
            return SimpleNamespace(text=None)  # safety block / empty candidate

    fake_client = SimpleNamespace(models=_FakeModels())

    with pytest.raises(EmptyResponse):
        gemini.generate("prompt", client=fake_client)


def test_groq_none_content_raises_empty_response():
    from reflecta.llm import groq

    class _FakeCompletions:
        def create(self, *, model, messages):
            msg = SimpleNamespace(content=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

    with pytest.raises(EmptyResponse):
        groq.repair("prompt", client=fake_client)


# ---------------------------------------------------------------------------
# HTTP 413 "request too large" must NOT be treated as a retryable 429
# ---------------------------------------------------------------------------

# The real Groq message: its body mentions "tokens per minute" and a
# rate-limit-ish code, which the old 429 heuristic misclassified.
_GROQ_413 = (
    "Error code: 413 - {'error': {'message': 'Request too large for model "
    "`llama-3.1-8b-instant` in organization `org_x` service tier `on_demand` "
    "on tokens per minute (TPM): Limit 6000, Requested 8486, please reduce "
    "your message size and try again.', 'type': 'tokens', "
    "'code': 'rate_limit_exceeded'}}"
)


def test_groq_413_raises_request_too_large_not_rate_limit():
    from reflecta.llm import groq
    from reflecta.llm.provider import RequestTooLarge

    class _FakeCompletions:
        def create(self, *, model, messages):
            raise Exception(_GROQ_413)

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

    with pytest.raises(RequestTooLarge):
        groq.repair("prompt", client=fake_client)


def test_request_too_large_is_not_retried_by_call_with_retry(monkeypatch):
    from reflecta.llm.provider import RequestTooLarge

    sleeps = []
    monkeypatch.setattr("reflecta.llm.provider.time.sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def raises_413():
        calls["n"] += 1
        raise RequestTooLarge("413 request too large", provider="Groq")

    # Must propagate immediately — no backoff, no BudgetExhausted.
    with pytest.raises(RequestTooLarge):
        call_with_retry(raises_413, max_retries=5)
    assert calls["n"] == 1
    assert sleeps == []
