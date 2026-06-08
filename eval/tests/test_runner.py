"""Tests for eval/runner.py — subprocess driver + EvalMetrics mapping."""

import json
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from eval.runner import _infer_stop_reason, _metrics_from_report, run_fixture


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_report(
    *,
    coverage_before: float = 50.0,
    coverage_after: float = 75.0,
    tests_kept: int = 2,
    tests_discarded: int = 1,
    repair_attempts_used: int = 0,
    stop_reason: str = "exhausted",
    targets: list | None = None,
    llm_calls_gemini: int = 3,
    llm_calls_groq: int = 0,
    llm_calls_claude: int = 0,
) -> dict:
    return {
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "tests_kept": tests_kept,
        "tests_discarded": tests_discarded,
        "repair_attempts_used": repair_attempts_used,
        "stop_reason": stop_reason,
        "llm_calls_gemini": llm_calls_gemini,
        "llm_calls_groq": llm_calls_groq,
        "llm_calls_claude": llm_calls_claude,
        "targets": targets
        or [
            {"status": "kept", "testability": "testable", "is_entrypoint": False},
            {"status": "kept", "testability": "testable", "is_entrypoint": False},
            {"status": "discarded", "testability": "testable", "is_entrypoint": False},
        ],
    }


# ── _metrics_from_report ──────────────────────────────────────────────────────


def test_metrics_from_report_basic():
    data = _make_report()
    m = _metrics_from_report("calc", data, run_time_seconds=5.5)
    assert m.fixture_name == "calc"
    assert m.coverage_before == 50.0
    assert m.coverage_after == 75.0
    assert m.coverage_delta == 25.0
    assert m.tests_accepted == 2
    assert m.tests_discarded == 1
    assert m.targets_attempted == 3
    assert m.llm_calls_gemini == 3
    assert m.llm_calls_groq == 0
    assert m.llm_calls_claude == 0
    assert m.run_time_seconds == 5.5
    assert m.stop_reason == "exhausted"


def test_metrics_from_report_triage_counts():
    targets = [
        {"status": "skipped", "testability": "blocked", "is_entrypoint": False},
        {"status": "skipped", "testability": "risky", "is_entrypoint": False},
        {"status": "skipped", "testability": "risky", "is_entrypoint": False},
        {"status": "skipped", "testability": "testable", "is_entrypoint": True},
    ]
    data = _make_report(
        coverage_before=0.0,
        coverage_after=0.0,
        tests_kept=0,
        tests_discarded=0,
        stop_reason="no_testable_targets",
        targets=targets,
        llm_calls_gemini=0,
    )
    m = _metrics_from_report("risky_io", data, run_time_seconds=1.0)
    assert m.targets_attempted == 0
    assert m.targets_skipped_blocked == 1
    assert m.targets_skipped_risky == 2
    assert m.targets_skipped_entrypoint == 1
    assert m.llm_calls_gemini == 0
    assert m.stop_reason == "no_testable_targets"


def test_metrics_from_report_coverage_delta_rounding():
    data = _make_report(coverage_before=33.333333, coverage_after=66.666667)
    m = _metrics_from_report("calc", data, run_time_seconds=1.0)
    # Ensure delta is computed and rounded, not a floating-point mess
    assert abs(m.coverage_delta - (66.666667 - 33.333333)) < 1e-5


def test_metrics_from_report_missing_llm_fields():
    """Old reports without llm_calls_* should deserialise to zeros."""
    data = _make_report()
    del data["llm_calls_gemini"]
    del data["llm_calls_groq"]
    del data["llm_calls_claude"]
    m = _metrics_from_report("calc", data, run_time_seconds=1.0)
    assert m.llm_calls_gemini == 0
    assert m.llm_calls_groq == 0
    assert m.llm_calls_claude == 0


# ── _infer_stop_reason ────────────────────────────────────────────────────────


def test_infer_stop_reason_no_testable():
    assert _infer_stop_reason("No testable targets found", "") == "no_testable_targets"


def test_infer_stop_reason_no_targets():
    assert _infer_stop_reason("", "no_targets stop") == "no_targets"


def test_infer_stop_reason_unknown():
    assert _infer_stop_reason("something else", "") == "unknown"


# ── run_fixture (mocked subprocess) ──────────────────────────────────────────


def test_run_fixture_reads_report_json(tmp_path):
    """run_fixture maps report JSON to EvalMetrics and cleans up temp dir."""
    # Build a minimal fake fixture directory
    fixture_name = "fake_calc"
    fixture_dir = tmp_path / "eval" / "fixtures" / fixture_name
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "calc.py").write_text("def add(a, b): return a+b\n")

    report_data = _make_report(
        coverage_before=50.0,
        coverage_after=80.0,
        tests_kept=2,
        stop_reason="exhausted",
    )

    captured_tmp_dirs: list[str] = []

    def fake_run(cmd, **kwargs):
        # Find the --path argument to locate where the report should be written
        path_idx = cmd.index("--path") + 1
        tmp_fixture_path = Path(cmd[path_idx])
        captured_tmp_dirs.append(str(tmp_fixture_path.parent))
        # Write the fake report
        (tmp_fixture_path / "reflecta-report.json").write_text(
            json.dumps(report_data), encoding="utf-8"
        )
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    # Patch _fixtures_dir to point to our tmp_path fixtures
    import eval.runner as runner_mod

    with patch.object(runner_mod, "_fixtures_dir", return_value=tmp_path / "eval" / "fixtures"):
        with patch("subprocess.run", side_effect=fake_run):
            m = run_fixture(fixture_name)

    assert m.fixture_name == fixture_name
    assert m.coverage_before == 50.0
    assert m.coverage_after == 80.0
    assert m.coverage_delta == 30.0
    assert m.tests_accepted == 2

    # Temp dir must be cleaned up
    for d in captured_tmp_dirs:
        assert not Path(d).exists(), f"Temp dir {d!r} was not cleaned up"


def test_run_fixture_cleans_up_on_no_report(tmp_path):
    """Temp dir is removed even when reflecta exits without writing a report."""
    fixture_name = "no_report"
    fixture_dir = tmp_path / "eval" / "fixtures" / fixture_name
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "stub.py").write_text("pass\n")

    captured_tmp_dirs: list[str] = []

    def fake_run_no_report(cmd, **kwargs):
        path_idx = cmd.index("--path") + 1
        tmp_fixture_path = Path(cmd[path_idx])
        captured_tmp_dirs.append(str(tmp_fixture_path.parent))
        # Do NOT write a report JSON
        r = MagicMock()
        r.returncode = 0
        r.stdout = "no testable targets found"
        r.stderr = ""
        return r

    import eval.runner as runner_mod

    with patch.object(runner_mod, "_fixtures_dir", return_value=tmp_path / "eval" / "fixtures"):
        with patch("subprocess.run", side_effect=fake_run_no_report):
            m = run_fixture(fixture_name)

    assert m.stop_reason == "no_testable_targets"
    assert m.tests_accepted == 0
    for d in captured_tmp_dirs:
        assert not Path(d).exists()


def test_run_fixture_raises_for_unknown_fixture(tmp_path):
    import pytest
    import eval.runner as runner_mod

    with patch.object(runner_mod, "_fixtures_dir", return_value=tmp_path / "fixtures"):
        with pytest.raises(FileNotFoundError, match="not found"):
            run_fixture("nonexistent")
