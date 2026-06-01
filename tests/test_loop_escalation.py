"""Integration tests for Claude escalation wired into the main loop (TDD)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from reflecta.models import CoverageTarget, GeneratedTest, RunResult, TargetStatus


# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_target(tmp_path):
    src = tmp_path / "mymod.py"
    src.write_text("def add(a, b):\n    return a + b\n")
    return CoverageTarget(
        file_path=src,
        qualified_name="mymod.add",
        missing_lines=[2],
        priority=1.0,
    )


def _make_generated_test(tmp_path, target):
    d = tmp_path / "tests" / "_reflecta"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "test_reflecta_mymod_0.py"
    p.write_text("def test_add():\n    assert add(1,2)==3\n")
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code=p.read_text(),
        model_used="gemini",
        assertion_count=1,
    )


# ---------------------------------------------------------------------------
# Tests for loop escalation wiring
# ---------------------------------------------------------------------------


def test_loop_escalation_disabled_by_default_marks_failed(tmp_path, sample_target):
    """Without --escalate, a target that exhausts repairs is marked FAILED."""
    from reflecta.loop import run_loop

    with (
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
        patch("reflecta.loop.extract_targets", return_value=[sample_target]),
        patch("reflecta.loop.generate_test") as mock_gen,
        patch("reflecta.loop.passes_assertion_gate", return_value=True),
        patch("reflecta.loop.run_test_isolated") as mock_run,
        patch("reflecta.loop.repair_test") as mock_repair,
    ):
        gen_test = _make_generated_test(tmp_path, sample_target)
        mock_gen.return_value = gen_test
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        mock_repair.return_value = (None, [])  # repair exhausted

        report = run_loop(tmp_path, max_iters=1, max_llm_calls=50, escalate=False)

    assert sample_target.status == TargetStatus.FAILED
    assert report.escalations_attempted == 0


def test_loop_escalation_enabled_calls_escalate_target(tmp_path, sample_target):
    """With escalate=True, after repair fails, escalate_target is called."""
    from reflecta.loop import run_loop

    with (
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
        patch("reflecta.loop.extract_targets", return_value=[sample_target]),
        patch("reflecta.loop.generate_test") as mock_gen,
        patch("reflecta.loop.passes_assertion_gate", return_value=True),
        patch("reflecta.loop.run_test_isolated") as mock_run,
        patch("reflecta.loop.repair_test") as mock_repair,
        patch("reflecta.escalate.escalate_target") as mock_escalate,
    ):
        gen_test = _make_generated_test(tmp_path, sample_target)
        mock_gen.return_value = gen_test
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        mock_repair.return_value = (None, [])
        mock_escalate.return_value = None  # escalation also fails

        report = run_loop(tmp_path, max_iters=1, max_llm_calls=50, escalate=True)

    mock_escalate.assert_called_once()
    assert report.escalations_attempted == 1


def test_loop_escalation_success_keeps_test(tmp_path, sample_target):
    """When escalation succeeds, the test is kept and coverage delta gate applied."""
    from reflecta.loop import run_loop

    with (
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(60.0, True)),
        patch("reflecta.loop.extract_targets", return_value=[sample_target]),
        patch("reflecta.loop.generate_test") as mock_gen,
        patch("reflecta.loop.passes_assertion_gate", return_value=True),
        patch("reflecta.loop.run_test_isolated") as mock_run,
        patch("reflecta.loop.repair_test") as mock_repair,
        patch("reflecta.escalate.escalate_target") as mock_escalate,
    ):
        gen_test = _make_generated_test(tmp_path, sample_target)
        mock_gen.return_value = gen_test
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        mock_repair.return_value = (None, [])

        # Escalation returns a repaired test and marks target KEPT
        repaired = _make_generated_test(tmp_path, sample_target)
        sample_target.status = TargetStatus.PENDING  # reset for the mock
        mock_escalate.return_value = repaired

        report = run_loop(tmp_path, max_iters=1, max_llm_calls=50, escalate=True)

    assert report.escalations_attempted == 1
    assert report.escalations_succeeded == 1
    assert report.tests_kept == 1


def test_loop_report_tracks_escalation_counts(tmp_path, sample_target):
    """RunReport.escalations_attempted and escalations_succeeded are populated."""
    from reflecta.models import RunReport

    assert hasattr(
        RunReport(
            repo_path=tmp_path,
            started_at=__import__("datetime").datetime.now(),
            coverage_before=0.0,
            coverage_after=0.0,
        ),
        "escalations_attempted",
    )
    assert hasattr(
        RunReport(
            repo_path=tmp_path,
            started_at=__import__("datetime").datetime.now(),
            coverage_before=0.0,
            coverage_after=0.0,
        ),
        "escalations_succeeded",
    )


def test_loop_escalation_failure_marks_escalated(tmp_path, sample_target):
    """When escalation itself fails (returns None), target is marked ESCALATED."""
    from reflecta.loop import run_loop

    with (
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
        patch("reflecta.loop.extract_targets", return_value=[sample_target]),
        patch("reflecta.loop.generate_test") as mock_gen,
        patch("reflecta.loop.passes_assertion_gate", return_value=True),
        patch("reflecta.loop.run_test_isolated") as mock_run,
        patch("reflecta.loop.repair_test") as mock_repair,
        patch("reflecta.escalate.escalate_target") as mock_escalate,
    ):
        gen_test = _make_generated_test(tmp_path, sample_target)
        mock_gen.return_value = gen_test
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        mock_repair.return_value = (None, [])
        mock_escalate.return_value = None  # escalation failed

        run_loop(tmp_path, max_iters=1, max_llm_calls=50, escalate=True)

    assert sample_target.status == TargetStatus.ESCALATED
