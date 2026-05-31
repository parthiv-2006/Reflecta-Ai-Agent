import pytest

from reflecta.llm.provider import BudgetExhausted, RateLimitError, call_with_retry


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
