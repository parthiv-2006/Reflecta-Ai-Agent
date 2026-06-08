"""Tests for eval/compare.py — compare_to_baseline()."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from eval.compare import compare_to_baseline
from eval.metrics import EvalMetrics, MetricResult


def _make_metrics(**overrides) -> EvalMetrics:
    defaults = dict(
        fixture_name="calc",
        coverage_before=50.0,
        coverage_after=75.0,
        coverage_delta=25.0,
        targets_attempted=3,
        tests_accepted=2,
        tests_discarded=1,
        repair_attempts_used=1,
        targets_skipped_blocked=0,
        targets_skipped_risky=0,
        targets_skipped_entrypoint=0,
        llm_calls_gemini=3,
        llm_calls_groq=1,
        llm_calls_claude=0,
        run_time_seconds=12.5,
        stop_reason="exhausted",
    )
    defaults.update(overrides)
    return EvalMetrics(**defaults)


# ── exact constraint ──────────────────────────────────────────────────────────


def test_exact_passes():
    m = _make_metrics(tests_accepted=2)
    results = compare_to_baseline(m, {"tests_accepted": {"exact": 2}})
    assert len(results) == 1
    assert results[0].passed is True
    assert "✓" in results[0].message


def test_exact_fails():
    m = _make_metrics(tests_accepted=3)
    results = compare_to_baseline(m, {"tests_accepted": {"exact": 2}})
    assert results[0].passed is False
    assert "✗" in results[0].message


def test_exact_zero():
    m = _make_metrics(llm_calls_gemini=0)
    results = compare_to_baseline(m, {"llm_calls_gemini": {"exact": 0}})
    assert results[0].passed is True


# ── min constraint ────────────────────────────────────────────────────────────


def test_min_passes_at_boundary():
    m = _make_metrics(tests_accepted=2)
    results = compare_to_baseline(m, {"tests_accepted": {"min": 2}})
    assert results[0].passed is True


def test_min_passes_above():
    m = _make_metrics(tests_accepted=3)
    results = compare_to_baseline(m, {"tests_accepted": {"min": 2}})
    assert results[0].passed is True


def test_min_fails_below():
    m = _make_metrics(tests_accepted=1)
    results = compare_to_baseline(m, {"tests_accepted": {"min": 2}})
    assert results[0].passed is False


def test_min_coverage_delta():
    m = _make_metrics(coverage_delta=0.20)
    results = compare_to_baseline(m, {"coverage_delta": {"min": 0.18}})
    assert results[0].passed is True


# ── max constraint ────────────────────────────────────────────────────────────


def test_max_passes_at_boundary():
    m = _make_metrics(tests_discarded=2)
    results = compare_to_baseline(m, {"tests_discarded": {"max": 2}})
    assert results[0].passed is True


def test_max_passes_below():
    m = _make_metrics(tests_discarded=0)
    results = compare_to_baseline(m, {"tests_discarded": {"max": 2}})
    assert results[0].passed is True


def test_max_fails_above():
    m = _make_metrics(tests_discarded=3)
    results = compare_to_baseline(m, {"tests_discarded": {"max": 2}})
    assert results[0].passed is False


# ── min+max constraint ────────────────────────────────────────────────────────


def test_min_max_passes_in_range():
    m = _make_metrics(llm_calls_gemini=3)
    results = compare_to_baseline(m, {"llm_calls_gemini": {"min": 1, "max": 6}})
    assert results[0].passed is True


def test_min_max_fails_below_range():
    m = _make_metrics(llm_calls_gemini=0)
    results = compare_to_baseline(m, {"llm_calls_gemini": {"min": 1, "max": 6}})
    assert results[0].passed is False


def test_min_max_fails_above_range():
    m = _make_metrics(llm_calls_gemini=7)
    results = compare_to_baseline(m, {"llm_calls_gemini": {"min": 1, "max": 6}})
    assert results[0].passed is False


def test_min_max_passes_at_boundaries():
    m_lo = _make_metrics(llm_calls_gemini=1)
    m_hi = _make_metrics(llm_calls_gemini=6)
    assert compare_to_baseline(m_lo, {"llm_calls_gemini": {"min": 1, "max": 6}})[0].passed
    assert compare_to_baseline(m_hi, {"llm_calls_gemini": {"min": 1, "max": 6}})[0].passed


# ── multiple metrics ──────────────────────────────────────────────────────────


def test_multiple_metrics_all_pass():
    m = _make_metrics(
        tests_accepted=2,
        tests_discarded=1,
        llm_calls_gemini=3,
        targets_skipped_blocked=0,
        targets_skipped_risky=0,
    )
    baseline = {
        "tests_accepted": {"min": 2},
        "tests_discarded": {"max": 2},
        "llm_calls_gemini": {"min": 1, "max": 6},
        "targets_skipped_blocked": {"exact": 0},
        "targets_skipped_risky": {"exact": 0},
    }
    results = compare_to_baseline(m, baseline)
    assert len(results) == 5
    assert all(r.passed for r in results)


def test_multiple_metrics_partial_failure():
    m = _make_metrics(tests_accepted=1, tests_discarded=3, llm_calls_gemini=0)
    baseline = {
        "tests_accepted": {"min": 2},
        "tests_discarded": {"max": 2},
        "llm_calls_gemini": {"exact": 0},
    }
    results = compare_to_baseline(m, baseline)
    passed = [r.passed for r in results]
    assert passed == [False, False, True]


# ── unknown metric name ───────────────────────────────────────────────────────


def test_unknown_metric_raises_attribute_error():
    m = _make_metrics()
    with pytest.raises(AttributeError, match="unknown metric"):
        compare_to_baseline(m, {"nonexistent_field": {"exact": 0}})


# ── result fields ─────────────────────────────────────────────────────────────


def test_result_fields_populated():
    m = _make_metrics(tests_accepted=2)
    results = compare_to_baseline(m, {"tests_accepted": {"min": 2}})
    r = results[0]
    assert r.name == "tests_accepted"
    assert r.actual == 2.0
    assert r.baseline == 2.0  # min value is the reference
    assert r.tolerance == 0.0
    assert isinstance(r.message, str)
    assert len(r.message) > 0


def test_empty_spec_vacuously_passes():
    """A spec with only a 'note' key (no constraint) should pass without error."""
    m = _make_metrics()
    # note-only spec: no exact/min/max keys
    results = compare_to_baseline(m, {"tests_accepted": {"note": "informational only"}})
    assert results[0].passed is True
