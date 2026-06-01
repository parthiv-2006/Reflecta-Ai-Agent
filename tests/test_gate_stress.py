"""Task 14 — gate stress test (the honesty pass).

Adversarial generated-test patterns: assertion-free, trivially-true, and
import-only-to-bump-coverage. Confirms both gates reject all of them so
reflecta cannot be gamed by coverage theater.
"""

from pathlib import Path
from unittest.mock import patch

from reflecta.gates import passes_assertion_gate, passes_delta_gate
from reflecta.models import CoverageTarget, GeneratedTest, RunResult, TargetStatus


def _make_test(source: str) -> GeneratedTest:
    target = CoverageTarget(
        file_path=Path("src/calc.py"),
        qualified_name="calc.add",
        missing_lines=[10, 11, 12],
        status=TargetStatus.PENDING,
    )
    return GeneratedTest(
        target=target,
        test_file_path=Path("tests/_reflecta/test_reflecta_calc_0.py"),
        source_code=source,
        model_used="gemini-2.5-flash",
    )


# ---------------------------------------------------------------------------
# Assertion gate — adversarial inputs that must be REJECTED
# ---------------------------------------------------------------------------


def test_import_only_test_rejected():
    """A test that only imports the module (no function body assertions) is rejected."""
    src = "import calc\n\ndef test_bump():\n    pass\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_function_call_without_assertion_rejected():
    """Calling a function without asserting its result is rejected."""
    src = "def test_bump():\n    calc.add(1, 2)\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_import_and_call_no_assertion_rejected():
    """Import + call + no assert is the classic coverage-theater pattern."""
    src = "import calc\n\ndef test_theater():\n    calc.add(1, 2)\n    calc.multiply(2, 3)\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_assert_none_rejected():
    """`assert None` is a constant falsy value — trivially false constant."""
    src = "def test_x():\n    assert None\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_assert_zero_rejected():
    """`assert 0` is a falsy integer constant."""
    src = "def test_x():\n    assert 0\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_assert_empty_string_rejected():
    """`assert ''` is a falsy string constant."""
    src = "def test_x():\n    assert ''\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_assert_false_rejected():
    """`assert False` is an explicit constant falsy value."""
    src = "def test_x():\n    assert False\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_all_trivial_mixed_rejected():
    """A mix of trivial assertions is still rejected if none are real."""
    src = "def test_x():\n    assert True\n    assert None\n    assert 1 == 1\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_empty_test_body_rejected():
    """A test function with only `pass` has no assertions."""
    src = "def test_nothing():\n    pass\n"
    assert passes_assertion_gate(_make_test(src)) is False


# ---------------------------------------------------------------------------
# Assertion gate — real assertions that must be ACCEPTED
# ---------------------------------------------------------------------------


def test_real_function_call_assertion_accepted():
    """A genuine assertion on a function return value is accepted."""
    src = "def test_add():\n    assert calc.add(1, 2) == 3\n"
    assert passes_assertion_gate(_make_test(src)) is True


def test_mixed_trivial_and_real_accepted():
    """If at least one assertion is non-trivial, the test passes the gate."""
    src = "def test_mix():\n    assert True\n    assert calc.add(1, 2) == 3\n"
    assert passes_assertion_gate(_make_test(src)) is True


# ---------------------------------------------------------------------------
# Delta gate — coverage-theater via delta
# ---------------------------------------------------------------------------


def test_delta_gate_rejects_zero_delta():
    """A test that passes but doesn't move coverage is discarded."""
    assert passes_delta_gate(50.0, 50.0) is False


def test_delta_gate_rejects_coverage_decrease():
    """If coverage somehow drops, the test is discarded."""
    assert passes_delta_gate(60.0, 59.9) is False


def test_delta_gate_accepts_any_increase():
    """Even a tiny coverage increase is enough to keep the test."""
    assert passes_delta_gate(50.0, 50.01) is True


def test_delta_gate_accepts_large_increase():
    assert passes_delta_gate(0.0, 80.0) is True


# ---------------------------------------------------------------------------
# Integration: assertion gate + delta gate collaboration
# ---------------------------------------------------------------------------


def test_coverage_theater_caught_by_delta_gate(tmp_path):
    """A test with a real assertion that passes pytest but covers already-covered
    code is caught by the delta gate and discarded (not kept)."""
    from reflecta.loop import run_loop
    from reflecta.models import CoverageTarget

    target = CoverageTarget(
        file_path=tmp_path / "src" / "calc.py",
        qualified_name="calc.add",
        missing_lines=[5, 6],
        priority=2.0,
    )
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target.file_path.write_text("def add(a, b):\n    return a + b\n")

    test_path = tmp_path / "tests" / "_reflecta" / "test_theater.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("def test_real():\n    assert 1 + 1 == 2\n")

    gen_test = GeneratedTest(
        target=target,
        test_file_path=test_path,
        source_code="def test_real():\n    assert 1 + 1 == 2\n",
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )

    # coverage stays flat — the test runs but adds no new coverage
    with (
        patch("reflecta.loop.extract_targets", return_value=[target]),
        patch("reflecta.loop.generate_test", return_value=gen_test),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(passed=True, traceback="", duration=0.1),
        ),
        patch("reflecta.loop.measure_coverage", return_value=60.0),
    ):
        report = run_loop(tmp_path, max_iters=5)

    assert report.tests_kept == 0, "coverage-theater test must not be kept"
    assert report.tests_discarded == 1, "coverage-theater test must be discarded"
    assert target.status == TargetStatus.DISCARDED
