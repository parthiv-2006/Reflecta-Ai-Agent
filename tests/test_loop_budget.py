"""Task 8b — repair loop and budget stop-condition tests (all mocked)."""

from pathlib import Path
from unittest.mock import patch


from reflecta.models import (
    CoverageTarget,
    GeneratedTest,
    RepairAttempt,
    RepairResult,
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


def _good_test(target: CoverageTarget, tmp_path: Path) -> GeneratedTest:
    p = tmp_path / f"test_{target.qualified_name}.py"
    p.write_text("def test_x():\n    assert 1 + 1 == 2\n")
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code="def test_x():\n    assert 1 + 1 == 2\n",
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )


def test_repair_fixes_on_attempt_2(tmp_path):
    """Repair succeeds on attempt 2 → target kept, repair_attempts_used == 1."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]
    gen_test = _good_test(targets[0], tmp_path)

    # run_test: first call fails, second call (after repair) passes
    run_results = iter(
        [
            RunResult(passed=False, traceback="AssertionError", duration=0.1),
            RunResult(passed=True, traceback="", duration=0.1),
        ]
    )

    repaired = GeneratedTest(
        target=targets[0],
        test_file_path=gen_test.test_file_path,
        source_code="def test_x():\n    assert 1 + 1 == 2\n",
        model_used="groq-fast",
        assertion_count=1,
    )
    repair_return = (
        repaired,
        [RepairAttempt(1, "AssertionError", "groq-fast", RepairResult.PASS)],
    )

    coverage_seq = iter([50.0, 60.0])

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", return_value=gen_test),
        patch(
            "reflecta.loop.run_test_isolated",
            side_effect=lambda *a, **kw: next(run_results),
        ),
        patch("reflecta.loop.repair_test", return_value=repair_return),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10, max_repairs=2)

    assert report.tests_kept == 1
    assert report.repair_attempts_used == 1
    assert targets[0].status == TargetStatus.KEPT


def test_repair_exhausted_loop_continues(tmp_path):
    """Repair never fixes → that target marked FAILED, loop continues to next target."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b")]
    gen_a = _good_test(targets[0], tmp_path)
    gen_b = _good_test(targets[1], tmp_path)

    gen_iter = iter([gen_a, gen_b])

    # func_a: run fails, repair exhausted → FAILED
    # func_b: run passes
    run_iter = iter(
        [
            RunResult(passed=False, traceback="AssertionError", duration=0.1),
            RunResult(passed=True, traceback="", duration=0.1),
        ]
    )

    # repair_test called only for func_a, returns (None, attempts) — exhausted
    repair_return = (
        None,
        [
            RepairAttempt(1, "AssertionError", "groq-fast", RepairResult.FAIL),
            RepairAttempt(2, "AssertionError", "groq-hard", RepairResult.FAIL),
        ],
    )

    coverage_seq = iter([50.0, 60.0])

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch(
            "reflecta.loop.generate_test", side_effect=lambda *a, **kw: next(gen_iter)
        ),
        patch(
            "reflecta.loop.run_test_isolated",
            side_effect=lambda *a, **kw: next(run_iter),
        ),
        patch("reflecta.loop.repair_test", return_value=repair_return),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10, max_repairs=2)

    assert report.tests_kept == 1
    assert targets[0].status == TargetStatus.FAILED
    assert targets[1].status == TargetStatus.KEPT
    assert report.stop_reason == "exhausted"


def test_budget_exhausted_stops_loop(tmp_path):
    """Budget exhausted mid-loop → stop_reason == 'budget'."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b"), _target("func_c")]

    call_count = {"n": 0}

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        call_count["n"] += 1
        return _good_test(target, tmp_path)

    def fake_run(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(passed=True, traceback="", duration=0.1)

    coverage_seq = iter([50.0, 60.0, 70.0, 80.0])

    # max_llm_calls=1 → budget exhausted after the first generate call
    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10, max_llm_calls=1)

    assert report.stop_reason == "budget"
    # Only one generate call should have happened before budget stopped the loop
    assert call_count["n"] <= 2


def test_generation_exception_marks_failed_and_continues(tmp_path):
    """HARDENING-0-9 §1.6: an exception on one target marks it FAILED and the
    loop proceeds to the next target instead of crashing."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b")]
    gen_b = _good_test(targets[1], tmp_path)

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        if target.qualified_name == "func_a":
            raise ImportError("un-importable target module")
        return gen_b

    coverage_seq = iter([50.0, 60.0])

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.run_test_isolated",
            side_effect=lambda *a, **kw: RunResult(
                passed=True, traceback="", duration=0.1
            ),
        ),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert targets[0].status == TargetStatus.FAILED
    assert targets[1].status == TargetStatus.KEPT
    assert report.tests_kept == 1
    assert report.stop_reason == "exhausted"


def test_provider_budget_exhausted_stops_cleanly(tmp_path):
    """HARDENING-0-9 §1.5: a BudgetExhausted from the provider stops the loop
    with stop_reason='budget' rather than propagating a traceback."""
    from reflecta.llm.provider import BudgetExhausted
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        raise BudgetExhausted("rate-limited after 5 retries")

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (50.0, True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (50.0, True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert report.stop_reason == "budget"
    assert targets[0].status == TargetStatus.FAILED
    # Loop stopped at the first target; the second was never attempted.
    assert targets[1].status == TargetStatus.PENDING


def test_report_budget_field_populated(tmp_path):
    """HARDENING-0-9 §4.4: RunReport.budget reports used/cap instead of staying blank."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch(
            "reflecta.loop.generate_test",
            side_effect=lambda *a, **kw: _good_test(targets[0], tmp_path),
        ),
        patch(
            "reflecta.loop.run_test_isolated",
            side_effect=lambda *a, **kw: RunResult(
                passed=True, traceback="", duration=0.1
            ),
        ),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(iter_cov), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(iter_cov), True),
        ),
    ):
        iter_cov = iter([50.0, 60.0])
        report = run_loop(tmp_path, max_iters=10, max_llm_calls=50)

    assert report.budget == "1/50"


def test_target_coverage_reached_stops_loop(tmp_path):
    """HARDENING-0-9 §2.1: once coverage meets target_coverage the loop stops
    with stop_reason='target_reached'."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        return _good_test(target, tmp_path)

    def fake_run(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(passed=True, traceback="", duration=0.1)

    # initial 50 → first keep raises to 60, which meets target 55
    coverage_seq = iter([50.0, 60.0])

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10, target_coverage=55.0)

    assert report.stop_reason == "target_reached"
    assert report.tests_kept == 1
    assert targets[1].status == TargetStatus.PENDING


def test_coverage_stall_stops_loop(tmp_path):
    """HARDENING-0-9 §2.1: stall_k consecutive non-improving targets stop the
    loop with stop_reason='stalled'."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b"), _target("func_c")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        return _good_test(target, tmp_path)

    def fake_run(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(passed=True, traceback="", duration=0.1)

    # coverage never rises → every target discarded by the delta gate
    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (50.0, True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (50.0, True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10, stall_k=2)

    assert report.stop_reason == "stalled"
    assert report.tests_discarded == 2
    assert targets[2].status == TargetStatus.PENDING


def test_suite_breaking_test_discarded_even_if_coverage_rises(tmp_path):
    """AUDIT H2: a test that passes in isolation but breaks the full suite is
    discarded, never kept — even though the coverage delta is positive."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        return _good_test(target, tmp_path)

    def fake_run(test_file, repo_path, timeout_s=30, **kwargs):
        # Passes when run alone (this is the isolated single-test validation).
        return RunResult(passed=True, traceback="", duration=0.1)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run),
        # Baseline suite is green; coverage rises to 60 but the full suite now
        # fails because of the new test.
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(60.0, False)),
    ):
        report = run_loop(tmp_path, max_iters=1)

    assert report.tests_kept == 0, "a suite-breaking test must not be kept"
    assert report.tests_discarded == 1
    assert targets[0].status == TargetStatus.DISCARDED


def test_run_loop_default_stall_k_is_7(tmp_path):
    """stall_k default must be 7 so easy-win repos don't stop prematurely."""
    import inspect

    from reflecta.loop import run_loop

    sig = inspect.signature(run_loop)
    assert sig.parameters["stall_k"].default == 7


def test_run_loop_default_max_iters_is_20(tmp_path):
    import inspect

    from reflecta.loop import run_loop

    sig = inspect.signature(run_loop)
    assert sig.parameters["max_iters"].default == 20


def test_max_iters_stops_at_two(tmp_path):
    """max_iters=2 with 3 targets → loop stops after exactly 2 iterations."""
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b"), _target("func_c")]

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None, **kwargs):
        return _good_test(target, tmp_path)

    def fake_run(test_file, repo_path, timeout_s=30, **kwargs):
        return RunResult(passed=True, traceback="", duration=0.1)

    coverage_seq = iter([50.0, 60.0, 70.0, 80.0])

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.run_test_isolated", side_effect=fake_run),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage_seq), True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=2)

    assert report.tests_kept == 2
    assert report.stop_reason == "max_iters"
    # Third target still pending
    assert targets[2].status == TargetStatus.PENDING
