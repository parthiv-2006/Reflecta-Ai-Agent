"""Task 10 — edge-case regression tests (all mocked)."""

from pathlib import Path
from unittest.mock import patch

import pytest

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


def _test_with_assertions(target: CoverageTarget, tmp_path: Path) -> GeneratedTest:
    p = tmp_path / f"test_{target.qualified_name}.py"
    source = "def test_x():\n    assert 1 + 1 == 2\n"
    p.write_text(source)
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code=source,
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )


def _test_no_assertions(target: CoverageTarget, tmp_path: Path) -> GeneratedTest:
    p = tmp_path / f"test_{target.qualified_name}_noassert.py"
    source = "def test_x():\n    pass\n"
    p.write_text(source)
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code=source,
        model_used="gemini-2.5-flash",
        assertion_count=0,
    )


def test_empty_repo_stop_reason_no_targets(tmp_path):
    """Empty repo: extract_targets returns [] → stop_reason='no_targets', no LLM calls."""
    from reflecta.loop import run_loop

    generate_calls = {"n": 0}

    def fake_generate(*a, **kw):
        generate_calls["n"] += 1
        raise AssertionError(
            "generate_test should not be called when there are no targets"
        )

    with (
        patch("reflecta.loop.extract_targets", return_value=[]),
        patch("reflecta.loop.measure_coverage_real", return_value=(0.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(0.0, True)),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert report.stop_reason == "no_targets"
    assert generate_calls["n"] == 0


def test_zero_coverage_gap_stop_reason_no_targets(tmp_path):
    """All lines already covered: extract_targets returns [] → stop_reason='no_targets'."""
    from reflecta.loop import run_loop

    with (
        patch("reflecta.loop.extract_targets", return_value=[]),
        patch("reflecta.loop.measure_coverage_real", return_value=(100.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(100.0, True)),
    ):
        report = run_loop(tmp_path, max_iters=10)

    assert report.stop_reason == "no_targets"
    assert report.coverage_before == 100.0


def test_no_existing_tests_loop_proceeds(tmp_path):
    """No tests/ dir → collect_existing_tests returns '' → loop proceeds normally."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]
    gen = _test_with_assertions(targets[0], tmp_path)
    coverage_seq = iter([0.0, 10.0])

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.collect_existing_tests", return_value=""),
        patch("reflecta.loop.generate_test", return_value=gen),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
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
        report = run_loop(tmp_path, max_iters=5)

    assert report.tests_kept == 1
    assert report.stop_reason == "exhausted"


def test_broken_target_marked_failed_loop_continues(tmp_path):
    """Broken/un-importable target raises ImportError → marked FAILED, loop continues."""
    from reflecta.loop import run_loop

    targets = [_target("broken_func"), _target("good_func")]
    gen_b = _test_with_assertions(targets[1], tmp_path)
    coverage_seq = iter([50.0, 60.0])

    def fake_generate(
        target, source, existing, *, repo_path, gemini_client=None, **kwargs
    ):
        if target.qualified_name == "broken_func":
            raise ImportError("cannot import module: SyntaxError in target file")
        return gen_b

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
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


def test_hanging_test_enters_repair_path(tmp_path):
    """Subprocess timeout → RunResult.traceback='timeout' → repair path entered."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]
    gen = _test_with_assertions(targets[0], tmp_path)
    repair_calls = {"n": 0}

    def fake_repair(
        test, result, source, *, repo_path, max_repairs, groq_client=None, **kwargs
    ):
        repair_calls["n"] += 1
        assert result.traceback == "timeout"
        return (None, [RepairAttempt(1, "timeout", "groq-fast", RepairResult.FAIL)])

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", return_value=gen),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=False, traceback="timeout", duration=30.0),
        ),
        patch("reflecta.loop.repair_test", side_effect=fake_repair),
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
    ):
        run_loop(tmp_path, max_iters=5)

    assert repair_calls["n"] == 1
    assert targets[0].status == TargetStatus.FAILED


def test_missing_api_key_names_variable_in_error(monkeypatch):
    """Missing API key → EnvironmentError message clearly names the variable."""
    from reflecta.config import require_api_keys

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(EnvironmentError) as exc_info:
        require_api_keys()

    error_msg = str(exc_info.value)
    assert "GEMINI_API_KEY" in error_msg
    assert "not set" in error_msg


def test_invalid_python_from_gemini_enters_repair_path(tmp_path):
    """Gemini returns syntactically invalid Python → enters repair path, not discard."""
    from reflecta.loop import run_loop

    targets = [_target("func_a")]

    invalid_path = tmp_path / "test_invalid.py"
    invalid_path.write_text("def test_x(\n    # unclosed parenthesis — invalid syntax")
    invalid_gen = GeneratedTest(
        target=targets[0],
        test_file_path=invalid_path,
        source_code="def test_x(\n    # unclosed parenthesis — invalid syntax",
        model_used="gemini-2.5-flash",
        assertion_count=0,
    )

    repair_calls = {"n": 0}

    def fake_repair(
        test, result, source, *, repo_path, max_repairs, groq_client=None, **kwargs
    ):
        repair_calls["n"] += 1
        return (
            None,
            [RepairAttempt(1, result.traceback, "groq-fast", RepairResult.FAIL)],
        )

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", return_value=invalid_gen),
        patch("reflecta.loop.repair_test", side_effect=fake_repair),
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(50.0, True)),
    ):
        report = run_loop(tmp_path, max_iters=5)

    assert repair_calls["n"] == 1, "repair should be called for invalid Python"
    assert targets[0].status == TargetStatus.FAILED
    assert report.tests_discarded == 0, (
        "invalid Python should not be counted as discarded"
    )
