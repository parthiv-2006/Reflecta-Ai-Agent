"""Tests for the mutation (honesty) gate — mutation.py + gates.passes_mutation_gate.

Two layers:
  • pure-AST unit tests for mutant generation and target-span location (fast,
    no subprocess);
  • end-to-end scoring of a real test against real mutants in a temp repo,
    proving a behaviour-checking test kills mutants while a coverage-padding
    test lets them survive.
"""

import ast
import sys
from pathlib import Path
from unittest.mock import patch

from reflecta.gates import passes_mutation_gate
from reflecta.models import (
    CoverageTarget,
    GeneratedTest,
    MutationResult,
    RunResult,
    TargetStatus,
)
from reflecta.mutation import (
    _find_function_span,
    generate_mutants,
    score_test,
)

# ---------------------------------------------------------------------------
# MutationResult.score
# ---------------------------------------------------------------------------


def test_score_is_kill_ratio():
    assert MutationResult(killed=3, total=4).score == 0.75


def test_score_is_one_when_no_mutable_surface():
    # A function with nothing to mutate can't be faulted — it passes the gate.
    assert MutationResult(killed=0, total=0).score == 1.0


# ---------------------------------------------------------------------------
# passes_mutation_gate
# ---------------------------------------------------------------------------


def test_gate_passes_at_or_above_threshold():
    assert passes_mutation_gate(MutationResult(killed=2, total=4), 0.5) is True
    assert passes_mutation_gate(MutationResult(killed=4, total=4), 0.5) is True


def test_gate_fails_below_threshold():
    assert passes_mutation_gate(MutationResult(killed=1, total=4), 0.5) is False


def test_gate_passes_empty_result():
    assert passes_mutation_gate(MutationResult(killed=0, total=0), 0.9) is True


# ---------------------------------------------------------------------------
# _find_function_span
# ---------------------------------------------------------------------------


def test_find_span_top_level_function():
    src = "x = 1\n\ndef foo(a):\n    return a + 1\n"
    lo, hi = _find_function_span(ast.parse(src), "foo")
    assert (lo, hi) == (3, 4)


def test_find_span_method_in_class():
    src = "def foo():\n    return 1\n\nclass C:\n    def foo(self):\n        return 2\n"
    # The ClassName.method convention must match the method, not the free fn.
    lo, _ = _find_function_span(ast.parse(src), "C.foo")
    assert lo == 5


def test_find_span_missing_returns_none():
    assert _find_function_span(ast.parse("def a():\n    pass\n"), "nope") is None


# ---------------------------------------------------------------------------
# generate_mutants
# ---------------------------------------------------------------------------


def test_generates_operator_and_constant_mutants():
    src = "def f(x):\n    if x > 0 and x <= 10:\n        return x + 1\n    return 0\n"
    lo, hi = _find_function_span(ast.parse(src), "f")
    mutants = generate_mutants(src, lo, hi, max_mutants=30)
    descs = " | ".join(m.description for m in mutants)
    # comparison, boolean, arithmetic and numeric-constant operators all mutate
    assert "Gt → LtE" in descs
    assert "And → Or" in descs
    assert "Add → Sub" in descs
    assert any("→ 1" in m.description for m in mutants)  # 0 → 1
    # every mutant is valid, parseable Python distinct from the original
    base = ast.unparse(ast.parse(src))
    for m in mutants:
        ast.parse(m.source)
        assert m.source != base


def test_respects_max_mutants_cap():
    src = "def f(a, b, c):\n    return a + b + c + 1 + 2 + 3 + 4 + 5\n"
    lo, hi = _find_function_span(ast.parse(src), "f")
    assert len(generate_mutants(src, lo, hi, max_mutants=2)) == 2


def test_only_mutates_within_target_span():
    # Two functions; only g's body is in the span we pass.
    src = "def f():\n    return 1 + 1\n\ndef g():\n    return 2 + 2\n"
    lo, hi = _find_function_span(ast.parse(src), "g")
    mutants = generate_mutants(src, lo, hi, max_mutants=30)
    # f's "1 + 1" must be untouched: the mutated "2 + 2" lines change, f stays.
    for m in mutants:
        assert "return 1 + 1" in m.source


def test_no_mutable_surface_yields_no_mutants():
    src = "def f(x):\n    return str(x)\n"
    lo, hi = _find_function_span(ast.parse(src), "f")
    assert generate_mutants(src, lo, hi) == []


# ---------------------------------------------------------------------------
# score_test — end to end against real mutants in a temp repo
# ---------------------------------------------------------------------------

_SOURCE = "def is_adult(age):\n    return age >= 18\n"


def _make_repo(tmp_path, test_body: str) -> GeneratedTest:
    (tmp_path / "calc.py").write_text(_SOURCE)
    test_file = tmp_path / "test_calc.py"
    test_file.write_text(test_body)
    target = CoverageTarget(
        file_path=tmp_path / "calc.py",
        qualified_name="is_adult",
        missing_lines=[2],
    )
    return GeneratedTest(
        target=target,
        test_file_path=test_file,
        source_code=test_body,
        model_used="test",
    )


def test_strong_test_kills_all_mutants(tmp_path):
    body = (
        "from calc import is_adult\n"
        "def test_boundary():\n"
        "    assert is_adult(18) is True\n"
        "    assert is_adult(17) is False\n"
        "    assert is_adult(100) is True\n"
    )
    test = _make_repo(tmp_path, body)
    result = score_test(test, _SOURCE, tmp_path, sys.executable)
    # `>=` has two mutants (GtE→Lt, 18→19); a boundary-checking test kills both.
    assert result.total >= 1
    assert result.killed == result.total
    assert result.score == 1.0


def test_padding_test_lets_mutants_survive(tmp_path):
    # Imports the module (bumps line coverage) but never asserts behaviour.
    body = "import calc\ndef test_import():\n    assert calc is not None\n"
    test = _make_repo(tmp_path, body)
    result = score_test(test, _SOURCE, tmp_path, sys.executable)
    assert result.total >= 1
    assert result.killed == 0
    assert result.score == 0.0
    assert len(result.survivors) == result.total


def test_score_test_leaves_real_tree_untouched(tmp_path):
    body = "from calc import is_adult\ndef test_b():\n    assert is_adult(18) is True\n"
    test = _make_repo(tmp_path, body)
    before = (tmp_path / "calc.py").read_text()
    score_test(test, _SOURCE, tmp_path, sys.executable)
    # Mutation runs in a temp copy: the real source file is never rewritten.
    assert (tmp_path / "calc.py").read_text() == before


# ---------------------------------------------------------------------------
# run_loop integration — the gate keeps strong tests and discards weak ones
# ---------------------------------------------------------------------------


def _loop_target(name: str) -> CoverageTarget:
    return CoverageTarget(
        file_path=Path("src/fake.py"),
        qualified_name=name,
        missing_lines=[10, 11, 12],
        priority=3.0,
    )


def _loop_test(target, tmp_path):
    p = tmp_path / f"test_{target.qualified_name}.py"
    p.write_text("def test_x():\n    assert 1 + 1 == 2\n")
    return GeneratedTest(
        target=target,
        test_file_path=p,
        source_code="def test_x():\n    assert 1 + 1 == 2\n",
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )


def _run_loop_with_mutation(tmp_path, mutation_result, **kwargs):
    from reflecta.loop import run_loop

    targets = [_loop_target("func_a")]
    coverage = iter([60.0])  # measure_coverage_real is patched separately

    def fake_generate(target, source, existing, *, repo_path, **kw):
        return _loop_test(target, tmp_path)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
        ),
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage), True),
        ),
        patch("reflecta.mutation.score_test", return_value=mutation_result),
    ):
        report = run_loop(tmp_path, max_iters=10, mutation=True, **kwargs)
    return report, targets[0]


def test_loop_mutation_gate_keeps_strong_test(tmp_path):
    report, target = _run_loop_with_mutation(
        tmp_path, MutationResult(killed=4, total=4), min_mutation_score=0.5
    )
    assert report.tests_kept == 1
    assert report.tests_failed_mutation == 0
    assert report.tests_mutation_tested == 1
    assert report.mutants_killed == 4
    assert report.mutants_total == 4
    assert target.status == TargetStatus.KEPT


def test_loop_mutation_gate_discards_weak_test(tmp_path):
    report, target = _run_loop_with_mutation(
        tmp_path,
        MutationResult(killed=1, total=4, survivors=["a", "b", "c"]),
        min_mutation_score=0.5,
    )
    # Coverage rose (delta gate would keep it) but it killed too few mutants.
    assert report.tests_kept == 0
    assert report.tests_discarded == 1
    assert report.tests_failed_mutation == 1
    assert report.mutants_total == 4
    assert target.status == TargetStatus.DISCARDED


def test_loop_mutation_disabled_skips_gate(tmp_path):
    """Without --mutation, score_test must never be called."""
    from reflecta.loop import run_loop

    targets = [_loop_target("func_a")]
    coverage = iter([60.0])

    def fake_generate(target, source, existing, *, repo_path, **kw):
        return _loop_test(target, tmp_path)

    with (
        patch("reflecta.loop.extract_targets", return_value=targets),
        patch("reflecta.loop.generate_test", side_effect=fake_generate),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
        ),
        patch("reflecta.loop.measure_coverage_real", return_value=(50.0, True)),
        patch(
            "reflecta.loop.measure_coverage_isolated",
            side_effect=lambda *a, **k: (next(coverage), True),
        ),
        patch("reflecta.mutation.score_test") as mock_score,
    ):
        report = run_loop(tmp_path, max_iters=10)  # mutation defaults to False

    mock_score.assert_not_called()
    assert report.tests_kept == 1
    assert report.tests_mutation_tested == 0
