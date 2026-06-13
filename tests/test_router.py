"""TDD tests for llm/router.py — written before implementation."""

import pytest

from reflecta.llm.provider import BudgetExhausted


# ---------------------------------------------------------------------------
# Test 1: Claude invoked when Gemini raises BudgetExhausted
# ---------------------------------------------------------------------------


def test_claude_invoked_on_gemini_budget_exhausted(monkeypatch, tmp_path):
    """When Gemini raises BudgetExhausted the router falls back to Claude."""
    from reflecta.llm import router

    def gemini_exhausted(prompt, *, client=None):
        raise BudgetExhausted("Gemini daily cap hit")

    claude_calls = []

    def claude_ok(prompt, *, client=None):
        claude_calls.append(prompt)
        return "def test_x():\n    assert True\n"

    monkeypatch.setattr(router.gemini, "generate", gemini_exhausted)
    monkeypatch.setattr(router.claude_generate, "generate", claude_ok)

    result = router.generate("some prompt", cache_dir=tmp_path / "cache")

    assert result == "def test_x():\n    assert True\n"
    assert len(claude_calls) == 1


# ---------------------------------------------------------------------------
# Test 2: Cache hit skips LLM entirely on second call
# ---------------------------------------------------------------------------


def test_cache_hit_skips_llm_call(monkeypatch, tmp_path):
    """Second call with the same prompt hits the disk cache; no LLM called."""
    from reflecta.llm import router

    call_count = {"n": 0}

    def counting_gemini(prompt, *, client=None):
        call_count["n"] += 1
        return "def test_cached():\n    assert 1 == 1\n"

    monkeypatch.setattr(router.gemini, "generate", counting_gemini)

    cache_dir = tmp_path / "gen_cache"
    prompt = "generate a test for add()"

    first = router.generate(prompt, cache_dir=cache_dir)
    second = router.generate(prompt, cache_dir=cache_dir)

    assert first == second
    assert call_count["n"] == 1  # LLM called exactly once; second was a cache hit


# ---------------------------------------------------------------------------
# Test 3: Claude overflow cap is respected
# ---------------------------------------------------------------------------


def test_claude_overflow_cap_respected(monkeypatch, tmp_path):
    """After MAX_OVERFLOW Claude calls the router raises BudgetExhausted."""
    from reflecta.llm import router, claude_generate

    monkeypatch.setattr(
        router.gemini,
        "generate",
        lambda *a, **kw: (_ for _ in ()).throw(BudgetExhausted("Gemini exhausted")),
    )

    claude_call_count = {"n": 0}

    def limited_claude(prompt, *, client=None):
        claude_call_count["n"] += 1
        return f"def test_{claude_call_count['n']}():\n    assert True\n"

    monkeypatch.setattr(router.claude_generate, "generate", limited_claude)
    monkeypatch.setattr(claude_generate, "MAX_OVERFLOW", 2)
    # Reset the in-process counter between tests
    monkeypatch.setattr(claude_generate, "_overflow_used", 0)

    cache_dir = tmp_path / "gen_cache"

    # First two calls should succeed (different prompts to avoid cache hits)
    router.generate("prompt A", cache_dir=cache_dir)
    router.generate("prompt B", cache_dir=cache_dir)

    # Third call must raise BudgetExhausted, not call Claude again
    with pytest.raises(BudgetExhausted):
        router.generate("prompt C", cache_dir=cache_dir)

    assert claude_call_count["n"] == 2
