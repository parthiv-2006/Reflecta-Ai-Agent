"""Salvage integration: at repair exhaustion, a partially-passing generated
file is trimmed to its passing tests and re-enters the gates instead of being
deleted wholesale (all mocked — no real LLM or subprocess)."""

from pathlib import Path
from unittest.mock import patch

from reflecta.models import (
    CoverageTarget,
    GeneratedTest,
    RunResult,
    TargetStatus,
)

PARTIAL_SOURCE = (
    "def test_good():\n"
    "    assert 1 + 1 == 2\n"
    "\n"
    "def test_bad():\n"
    "    assert 1 + 1 == 3\n"
)

FAIL_OUTPUT = (
    "FAILED tests/_reflecta/test_fake_0.py::test_bad - AssertionError\n"
    "1 failed, 1 passed in 0.10s\n"
)


def _target(name: str) -> CoverageTarget:
    return CoverageTarget(
        file_path=Path("src/fake.py"),
        qualified_name=name,
        missing_lines=[10, 11, 12],
        priority=3.0,
    )


def _gen_test(target: CoverageTarget, tmp_path: Path) -> GeneratedTest:
    p = tmp_path / "test_fake_0.py"
    p.write_text(PARTIAL_SOURCE, encoding="utf-8")
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code=PARTIAL_SOURCE,
        model_used="gemini-2.5-flash",
        assertion_count=2,
    )


def _run_salvage_scenario(tmp_path, *, salvage_rerun_passes: bool):
    """Drive run_loop to repair exhaustion on a partially-passing file."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]

    def fake_generate(target, source, existing, *, repo_path, **kwargs):
        return _gen_test(target, tmp_path)

    run_calls = {"n": 0}

    def fake_run_test(test_file, repo_path, timeout_s=30, **kwargs):
        run_calls["n"] += 1
        if run_calls["n"] == 1:
            # initial run: partial failure with a parseable summary
            return RunResult(
                passed=False,
                traceback=FAIL_OUTPUT,
                duration=0.1,
                failure_kind="test_failure",
            )
        # the salvage re-run of the trimmed file
        return RunResult(
            passed=salvage_rerun_passes,
            traceback="" if salvage_rerun_passes else "still bad",
            duration=0.1,
            failure_kind="" if salvage_rerun_passes else "test_failure",
        )

    def fake_repair(test, result, source, *, repo_path, max_repairs, **kwargs):
        return None, []  # repair always exhausts without fixing anything

    coverage_sequence = iter([50.0, 60.0, 70.0])

    def fake_measure(*a, **k):
        return (next(coverage_sequence), True)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run_test),
        patch("reflecta.loop.repair_test", side_effect=fake_repair),
        patch("reflecta.loop.measure_coverage_real", side_effect=fake_measure),
        patch("reflecta.loop.measure_coverage_isolated", side_effect=fake_measure),
    ):
        report = run_loop(tmp_path, max_iters=5)
    return report, targets[0], tmp_path / "test_fake_0.py"


def test_salvage_keeps_passing_remainder(tmp_path):
    report, target, test_file = _run_salvage_scenario(
        tmp_path, salvage_rerun_passes=True
    )
    assert report.tests_salvaged == 1
    assert report.tests_kept == 1
    assert target.status == TargetStatus.KEPT
    on_disk = test_file.read_text(encoding="utf-8")
    assert "test_good" in on_disk
    assert "test_bad" not in on_disk


def test_salvage_rerun_failure_falls_back_to_failed(tmp_path):
    report, target, test_file = _run_salvage_scenario(
        tmp_path, salvage_rerun_passes=False
    )
    assert report.tests_salvaged == 0
    assert report.tests_kept == 0
    assert target.status == TargetStatus.FAILED
    assert not test_file.exists()  # hard rule: only KEPT tests stay on disk


def test_unsalvageable_failure_goes_straight_to_failed(tmp_path):
    """A file where every test failed has nothing to salvage — FAILED as before."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]
    all_fail = "FAILED tests/t.py::test_good - x\nFAILED tests/t.py::test_bad - y\n"

    def fake_generate(target, source, existing, *, repo_path, **kwargs):
        return _gen_test(target, tmp_path)

    def fake_run_test(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(
            passed=False,
            traceback=all_fail,
            duration=0.1,
            failure_kind="test_failure",
        )

    def fake_repair(test, result, source, *, repo_path, max_repairs, **kwargs):
        return None, []

    def fake_measure(*a, **k):
        return (50.0, True)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run_test),
        patch("reflecta.loop.repair_test", side_effect=fake_repair),
        patch("reflecta.loop.measure_coverage_real", side_effect=fake_measure),
        patch("reflecta.loop.measure_coverage_isolated", side_effect=fake_measure),
    ):
        report = run_loop(tmp_path, max_iters=5)

    assert report.tests_salvaged == 0
    assert targets[0].status == TargetStatus.FAILED
    assert not (tmp_path / "test_fake_0.py").exists()
