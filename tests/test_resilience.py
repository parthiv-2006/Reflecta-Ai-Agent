"""Task 11 — free-tier resilience regression tests (all mocked)."""

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


def _good_test(target: CoverageTarget, tmp_path: Path) -> GeneratedTest:
    p = tmp_path / f"test_{target.qualified_name}.py"
    src = "def test_x():\n    assert 1 + 1 == 2\n"
    p.write_text(src)
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code=src,
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )


def test_repair_budget_exhausted_marks_failed_continues(tmp_path):
    """Groq BudgetExhausted during repair → that target FAILED, loop continues to next."""
    from reflecta.llm.provider import BudgetExhausted
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b")]
    gen_a = _good_test(targets[0], tmp_path)
    gen_b = _good_test(targets[1], tmp_path)

    gen_iter = iter([gen_a, gen_b])
    run_iter = iter(
        [
            RunResult(passed=False, traceback="AssertionError", duration=0.1),
            RunResult(passed=True, traceback="", duration=0.1),
        ]
    )

    def fake_repair(test, result, source, *, repo_path, max_repairs, groq_client=None):
        raise BudgetExhausted("groq daily cap hit")

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
        patch("reflecta.loop.repair_test", side_effect=fake_repair),
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

    assert targets[0].status == TargetStatus.FAILED, (
        "repair-exhausted target should be FAILED"
    )
    assert targets[1].status == TargetStatus.KEPT, (
        "next target should still be attempted"
    )
    assert report.tests_kept == 1
    assert report.stop_reason != "budget", (
        "loop should not stop due to repair-side exhaustion"
    )


def test_generation_budget_exhausted_stops_loop(tmp_path):
    """Gemini BudgetExhausted during generation → stop_reason='budget' (regression)."""
    from reflecta.llm.provider import BudgetExhausted
    from reflecta.loop import run_loop

    targets = [_target("func_a"), _target("func_b")]

    def fake_generate(*a, **kw):
        raise BudgetExhausted("gemini daily cap hit")

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert report.stop_reason == "budget"
    assert targets[0].status == TargetStatus.FAILED
    assert targets[1].status == TargetStatus.PENDING


def test_budget_tracker_stops_before_cap(tmp_path):
    """max_llm_calls=3 with 5 targets → at most 3 generate calls before stop."""
    from reflecta.loop import run_loop

    targets = [_target(f"func_{i}") for i in range(5)]
    generate_calls = {"n": 0}

    def fake_generate(target, source, existing, *, repo_path, gemini_client=None):
        generate_calls["n"] += 1
        return _good_test(target, tmp_path)

    coverage_values = [50.0, 55.0, 60.0, 65.0, 70.0, 75.0]
    coverage_iter = iter(coverage_values)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
        ),
        patch(
            "reflecta.loop.measure_coverage_real",
            side_effect=lambda *a, **k: (next(coverage_iter), True),
        ),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage_iter), True),
        ),
    ):
        report = run_loop(tmp_path, max_iters=10, max_llm_calls=3)

    assert report.stop_reason == "budget"
    assert generate_calls["n"] <= 3
