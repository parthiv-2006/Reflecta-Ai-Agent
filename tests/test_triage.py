"""Integration tests: static testability triage skips targets with ZERO LLM
calls, and the triage/dry-run preview spends no quota."""

from pathlib import Path
from unittest.mock import patch

from reflecta.models import CoverageTarget, TargetStatus


def _target(name: str, testability: str = "testable", *, entry: bool = False):
    return CoverageTarget(
        file_path=Path("mod.py"),
        qualified_name=name,
        missing_lines=[1, 2],
        priority=2.0,
        is_entrypoint=entry,
        testability=testability,
        testability_reason="" if testability == "testable" else f"{testability} I/O",
    )


def test_blocked_targets_never_call_the_llm(tmp_path):
    from reflecta.loop import run_loop

    targets = [_target("a", "blocked"), _target("b", "blocked")]
    gen_calls = {"n": 0}

    def fake_generate(*a, **k):
        gen_calls["n"] += 1
        raise AssertionError("generate_test must not be called for blocked targets")

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.measure_coverage_real", return_value=(0.0, True)),
        patch("reflecta.environment.preflight_imports", return_value=[]),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert gen_calls["n"] == 0
    assert report.stop_reason == "no_testable_targets"
    assert all(t.status == TargetStatus.SKIPPED for t in targets)
    assert report.tests_skipped == 2


def test_risky_skipped_by_default_but_testable_attempted(tmp_path):
    from reflecta.loop import run_loop
    from reflecta.models import GeneratedTest, RunResult

    risky = _target("net_call", "risky")
    clean = _target("pure", "testable")
    targets = [risky, clean]

    attempted = []

    def fake_generate(target, *a, **k):
        attempted.append(target.qualified_name)
        return GeneratedTest(
            target=target,
            test_file_path=tmp_path / "t.py",
            source_code="def test_x():\n    assert 1 == 1\n",
            model_used="gemini-2.5-flash",
            assertion_count=1,
        )

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.measure_coverage_real", return_value=(0.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
        patch("reflecta.environment.preflight_imports", return_value=[]),
        patch("reflecta.loop.collect_existing_tests", return_value=""),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
        ),
        patch("pathlib.Path.read_text", return_value="def pure():\n    return 1\n"),
    ):
        run_loop(tmp_path, max_iters=10)

    # risky was skipped (not generated); only the clean target was attempted.
    assert "net_call" not in attempted
    assert "pure" in attempted
    assert risky.status == TargetStatus.SKIPPED


def test_attempt_risky_flag_includes_risky(tmp_path):
    from reflecta.loop import run_loop
    from reflecta.models import GeneratedTest, RunResult

    risky = _target("net_call", "risky")
    targets = [risky]
    attempted = []

    def fake_generate(target, *a, **k):
        attempted.append(target.qualified_name)
        return GeneratedTest(
            target=target,
            test_file_path=tmp_path / "t.py",
            source_code="def test_x():\n    assert 1 == 1\n",
            model_used="gemini-2.5-flash",
            assertion_count=1,
        )

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.measure_coverage_real", return_value=(0.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
        patch("reflecta.environment.preflight_imports", return_value=[]),
        patch("reflecta.loop.collect_existing_tests", return_value=""),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
        ),
        patch("pathlib.Path.read_text", return_value="x = 1\n"),
    ):
        run_loop(tmp_path, max_iters=10, attempt_risky=True)

    assert attempted == ["net_call"]


def test_triage_repo_spends_no_quota(tmp_path):
    from reflecta.loop import triage_repo

    targets = [
        _target("pure", "testable"),
        _target("fetch", "risky"),
        _target("seed", "blocked"),
        _target("main", "testable", entry=True),
    ]

    # If any provider module were imported/called, these patches would catch it;
    # triage must rely only on coverage + static analysis.
    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.measure_coverage_real", return_value=(12.5, True)),
        patch("reflecta.environment.preflight_imports", return_value=[]),
    ):
        plan = triage_repo(tmp_path)

    assert plan.coverage_before == 12.5
    # Only the pure function would be attempted; risky/blocked/entrypoint skipped.
    assert [t.qualified_name for t in plan.attempt] == ["pure"]
    assert plan.count("blocked") == 1
    assert plan.count("risky") == 1
    assert plan.n_entrypoints == 1


def test_triage_repo_attempt_risky_includes_risky(tmp_path):
    from reflecta.loop import triage_repo

    targets = [_target("pure", "testable"), _target("fetch", "risky")]
    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.measure_coverage_real", return_value=(0.0, True)),
        patch("reflecta.environment.preflight_imports", return_value=[]),
    ):
        plan = triage_repo(tmp_path, attempt_risky=True)

    names = sorted(t.qualified_name for t in plan.attempt)
    assert names == ["fetch", "pure"]
