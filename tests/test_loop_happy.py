"""Task 8a — happy-path loop tests (all mocked, no real LLM or subprocess)."""

from pathlib import Path
from unittest.mock import patch


from reflecta.models import (
    CoverageTarget,
    GeneratedTest,
    RunResult,
    TargetStatus,
)


def _target(name: str, missing: int = 3) -> CoverageTarget:
    return CoverageTarget(
        file_path=Path("src/fake.py"),
        qualified_name=name,
        missing_lines=list(range(10, 10 + missing)),
        priority=float(missing),
    )


def _gen_test(target: CoverageTarget, tmp_path: Path) -> GeneratedTest:
    p = tmp_path / f"test_{target.qualified_name}.py"
    p.write_text("def test_x():\n    assert 1 + 1 == 2\n")
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code="def test_x():\n    assert 1 + 1 == 2\n",
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )


def test_happy_path_two_targets_both_kept(tmp_path):
    """Two targets, both generate-pass-assert-run-delta — report shows 2 kept, 0 discarded."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        return _gen_test(target, tmp_path)

    def fake_run_test(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(passed=True, traceback="", duration=0.1)

    # measure_coverage: before=50, after first=60, after second=70.
    # Baseline (real) is called once before any candidate (isolated), so a single
    # shared iterator preserves ordering; suite always green so H2 stays inert.
    coverage_sequence = iter([50.0, 60.0, 70.0])

    def fake_measure(*a, **k):
        return (next(coverage_sequence), True)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run_test),
        patch("reflecta.loop.measure_coverage_real", side_effect=fake_measure),
        patch("reflecta.loop.measure_coverage_isolated", side_effect=fake_measure),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert report.tests_kept == 2
    assert report.tests_discarded == 0
    assert report.coverage_after > report.coverage_before
    assert report.coverage_before == 50.0
    assert report.coverage_after == 70.0
    assert report.stop_reason == "exhausted"


def test_happy_path_max_iters_stops_early(tmp_path):
    """Three targets but max_iters=1 — loop stops after one iteration."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b"), _target("func_c")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        return _gen_test(target, tmp_path)

    def fake_run_test(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(passed=True, traceback="", duration=0.1)

    coverage_sequence = iter([50.0, 60.0, 70.0, 80.0])

    def fake_measure(*a, **k):
        return (next(coverage_sequence), True)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run_test),
        patch("reflecta.loop.measure_coverage_real", side_effect=fake_measure),
        patch("reflecta.loop.measure_coverage_isolated", side_effect=fake_measure),
    ):
        report = run_loop(tmp_path, max_iters=1)

    assert report.tests_kept == 1
    assert report.stop_reason == "max_iters"


def test_happy_path_assertion_gate_discards(tmp_path):
    """Target whose generated test fails assertion gate is discarded, not run."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        p = tmp_path / "test_bad.py"
        p.write_text("def test_x(): pass\n")  # no assertions → gate rejects
        return GeneratedTest(
            target=target,
            test_file_path=p,
            source_code="def test_x(): pass\n",
            model_used="gemini-2.5-flash",
            assertion_count=0,
        )

    def fake_measure(*a, **k):
        return (50.0, True)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.measure_coverage_real", side_effect=fake_measure),
        patch("reflecta.loop.measure_coverage_isolated", side_effect=fake_measure),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert report.tests_kept == 0
    assert report.tests_discarded == 1
    assert targets[0].status == TargetStatus.DISCARDED
    assert report.stop_reason == "exhausted"


def test_happy_path_run_fails_marks_failed(tmp_path):
    """Target whose test fails at run time (no repair yet) is marked FAILED."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        return _gen_test(target, tmp_path)

    def fake_run_test(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(passed=False, traceback="AssertionError", duration=0.1)

    def fake_measure(*a, **k):
        return (50.0, True)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run_test),
        patch("reflecta.loop.repair_test", return_value=(None, [])),
        patch("reflecta.loop.measure_coverage_real", side_effect=fake_measure),
        patch("reflecta.loop.measure_coverage_isolated", side_effect=fake_measure),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert report.tests_kept == 0
    assert targets[0].status == TargetStatus.FAILED
    assert report.stop_reason == "exhausted"
