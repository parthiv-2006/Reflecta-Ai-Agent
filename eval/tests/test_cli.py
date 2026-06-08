"""Tests for eval/cli.py — run / update-baseline / cache command group."""

import json
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from typer.testing import CliRunner

from eval.cli import eval_app
from eval.metrics import EvalMetrics, EvalReport, MetricResult


runner = CliRunner()


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
        llm_calls_groq=0,
        llm_calls_claude=0,
        run_time_seconds=5.0,
        stop_reason="exhausted",
    )
    defaults.update(overrides)
    return EvalMetrics(**defaults)


def _make_passing_result(name: str = "tests_accepted") -> MetricResult:
    return MetricResult(
        name=name,
        actual=2.0,
        baseline=2.0,
        tolerance=0.0,
        passed=True,
        message=f"{name}=2 >= min 2 ✓",
    )


def _make_failing_result(name: str = "tests_accepted") -> MetricResult:
    return MetricResult(
        name=name,
        actual=1.0,
        baseline=2.0,
        tolerance=0.0,
        passed=False,
        message=f"{name}=1 < min 2 ✗",
    )


# ── helpers for patching ──────────────────────────────────────────────────────

def _baseline_ctx(fixture_names: list[str], baseline_data: dict):
    """Context managers to mock baseline loading and fixture directory listing."""
    import eval.cli as cli_mod

    baseline_json = json.dumps(baseline_data)
    fake_path = MagicMock()
    fake_path.exists.return_value = True
    fake_path.read_text.return_value = baseline_json

    def fake_fixture_names():
        return fixture_names

    return (
        patch.object(cli_mod, "_BASELINES_PATH", fake_path),
        patch.object(cli_mod, "_fixture_names", side_effect=fake_fixture_names),
    )


# ── eval run ─────────────────────────────────────────────────────────────────


def test_eval_run_all_pass_exits_0(tmp_path):
    """eval run exits 0 when all metrics pass."""
    metrics = _make_metrics()
    results = [_make_passing_result("tests_accepted"), _make_passing_result("coverage_delta")]

    import eval.cli as cli_mod

    baseline = {"calc": {"tests_accepted": {"min": 2}}}
    baseline_json = json.dumps(baseline)
    fake_path = MagicMock()
    fake_path.exists.return_value = True
    fake_path.read_text.return_value = baseline_json
    fake_recordings_dir = tmp_path / "recordings"

    with patch.object(cli_mod, "_BASELINES_PATH", fake_path):
        with patch.object(cli_mod, "_fixture_names", return_value=["calc"]):
            with patch.object(cli_mod, "_RECORDINGS_DIR", fake_recordings_dir):
                with patch("eval.runner.run_fixture", return_value=metrics):
                    with patch("eval.compare.compare_to_baseline", return_value=results):
                        result = runner.invoke(eval_app, ["run", "--fixture", "calc"])

    assert result.exit_code == 0
    assert "PASSED" in result.output


def test_eval_run_failure_exits_1(tmp_path):
    """eval run exits 1 when any metric fails."""
    metrics = _make_metrics(tests_accepted=0)
    results = [_make_failing_result("tests_accepted")]

    import eval.cli as cli_mod

    baseline = {"calc": {"tests_accepted": {"min": 2}}}
    baseline_json = json.dumps(baseline)
    fake_path = MagicMock()
    fake_path.exists.return_value = True
    fake_path.read_text.return_value = baseline_json
    fake_recordings_dir = tmp_path / "recordings"

    with patch.object(cli_mod, "_BASELINES_PATH", fake_path):
        with patch.object(cli_mod, "_fixture_names", return_value=["calc"]):
            with patch.object(cli_mod, "_RECORDINGS_DIR", fake_recordings_dir):
                with patch("eval.runner.run_fixture", return_value=metrics):
                    with patch("eval.compare.compare_to_baseline", return_value=results):
                        result = runner.invoke(eval_app, ["run", "--fixture", "calc"])

    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_eval_run_output_contains_fixture_name(tmp_path):
    """eval run output includes the fixture name."""
    metrics = _make_metrics()
    results = [_make_passing_result()]

    import eval.cli as cli_mod

    baseline = {"calc": {"tests_accepted": {"min": 2}}}
    baseline_json = json.dumps(baseline)
    fake_path = MagicMock()
    fake_path.exists.return_value = True
    fake_path.read_text.return_value = baseline_json
    fake_recordings_dir = tmp_path / "recordings"

    with patch.object(cli_mod, "_BASELINES_PATH", fake_path):
        with patch.object(cli_mod, "_fixture_names", return_value=["calc"]):
            with patch.object(cli_mod, "_RECORDINGS_DIR", fake_recordings_dir):
                with patch("eval.runner.run_fixture", return_value=metrics):
                    with patch("eval.compare.compare_to_baseline", return_value=results):
                        result = runner.invoke(eval_app, ["run", "--fixture", "calc"])

    assert "calc" in result.output


# ── eval --help ───────────────────────────────────────────────────────────────


def test_eval_help_prints_command_group():
    result = runner.invoke(eval_app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "cache" in result.output


def test_eval_run_help():
    result = runner.invoke(eval_app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--fixture" in result.output
    assert "--verbose" in result.output


def test_eval_cache_help():
    result = runner.invoke(eval_app, ["cache", "--help"])
    assert result.exit_code == 0
    assert "--fixture" in result.output


def test_eval_update_baseline_help():
    result = runner.invoke(eval_app, ["update-baseline", "--help"])
    assert result.exit_code == 0
    assert "--fixture" in result.output
