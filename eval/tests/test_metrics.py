"""Tests for eval/metrics.py — EvalMetrics, MetricResult, EvalReport dataclasses."""

import sys
import os

# Allow running as `pytest eval/tests/test_metrics.py` from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from eval.metrics import EvalMetrics, EvalReport, MetricResult


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


def _make_result(**overrides) -> MetricResult:
    defaults = dict(
        name="tests_accepted",
        actual=2.0,
        baseline=2.0,
        tolerance=0.0,
        passed=True,
        message="tests_accepted=2 >= min 2 ✓",
    )
    defaults.update(overrides)
    return MetricResult(**defaults)


# ── EvalMetrics ───────────────────────────────────────────────────────────────


def test_eval_metrics_fields():
    m = _make_metrics()
    assert m.fixture_name == "calc"
    assert m.coverage_before == 50.0
    assert m.coverage_after == 75.0
    assert m.coverage_delta == 25.0
    assert m.targets_attempted == 3
    assert m.tests_accepted == 2
    assert m.tests_discarded == 1
    assert m.repair_attempts_used == 1
    assert m.targets_skipped_blocked == 0
    assert m.targets_skipped_risky == 0
    assert m.targets_skipped_entrypoint == 0
    assert m.llm_calls_gemini == 3
    assert m.llm_calls_groq == 1
    assert m.llm_calls_claude == 0
    assert m.run_time_seconds == 12.5
    assert m.stop_reason == "exhausted"


def test_eval_metrics_json_roundtrip():
    m = _make_metrics()
    d = m.to_dict()
    assert isinstance(d, dict)
    assert d["fixture_name"] == "calc"
    assert d["coverage_delta"] == 25.0
    m2 = EvalMetrics.from_dict(d)
    assert m2 == m


def test_eval_metrics_all_field_names():
    """Ensure no field names were accidentally dropped or renamed."""
    m = _make_metrics()
    d = m.to_dict()
    expected_keys = {
        "fixture_name",
        "coverage_before",
        "coverage_after",
        "coverage_delta",
        "targets_attempted",
        "tests_accepted",
        "tests_discarded",
        "repair_attempts_used",
        "targets_skipped_blocked",
        "targets_skipped_risky",
        "targets_skipped_entrypoint",
        "llm_calls_gemini",
        "llm_calls_groq",
        "llm_calls_claude",
        "run_time_seconds",
        "stop_reason",
    }
    assert set(d.keys()) == expected_keys


# ── MetricResult ─────────────────────────────────────────────────────────────


def test_metric_result_fields():
    r = _make_result()
    assert r.name == "tests_accepted"
    assert r.actual == 2.0
    assert r.passed is True


def test_metric_result_json_roundtrip():
    r = _make_result(passed=False, message="tests_accepted=1 < min 2 ✗")
    d = r.to_dict()
    r2 = MetricResult.from_dict(d)
    assert r2 == r
    assert r2.passed is False


# ── EvalReport ────────────────────────────────────────────────────────────────


def test_eval_report_fields():
    m = _make_metrics()
    r1 = _make_result()
    r2 = _make_result(name="coverage_delta", actual=25.0, baseline=18.0)
    report = EvalReport(
        fixture_name="calc",
        metrics=m,
        results=[r1, r2],
        overall_passed=True,
    )
    assert report.fixture_name == "calc"
    assert report.overall_passed is True
    assert len(report.results) == 2


def test_eval_report_json_roundtrip():
    m = _make_metrics()
    r = _make_result()
    report = EvalReport(
        fixture_name="calc", metrics=m, results=[r], overall_passed=True
    )
    d = report.to_dict()
    report2 = EvalReport.from_dict(d)
    assert report2.fixture_name == report.fixture_name
    assert report2.overall_passed == report.overall_passed
    assert report2.metrics == report.metrics
    assert len(report2.results) == 1
    assert report2.results[0] == r


def test_eval_report_empty_results_default():
    m = _make_metrics()
    report = EvalReport(fixture_name="calc", metrics=m)
    assert report.results == []
    assert report.overall_passed is False


def test_eval_report_from_dict_without_results():
    m = _make_metrics()
    d = {
        "fixture_name": "calc",
        "metrics": m.to_dict(),
        "overall_passed": False,
    }
    report = EvalReport.from_dict(d)
    assert report.results == []
