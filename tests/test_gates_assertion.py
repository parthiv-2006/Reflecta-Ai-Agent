from pathlib import Path


from reflecta.gates import passes_assertion_gate
from reflecta.models import CoverageTarget, GeneratedTest, TargetStatus


def _make_test(source_code: str) -> GeneratedTest:
    target = CoverageTarget(
        file_path=Path("src/calc.py"),
        qualified_name="add",
        missing_lines=[5],
        status=TargetStatus.PENDING,
    )
    return GeneratedTest(
        target=target,
        test_file_path=Path("tests/_reflecta/test_reflecta_calc_0.py"),
        source_code=source_code,
        model_used="gemini-2.5-flash",
    )


def test_no_assertions_rejected():
    src = "def test_nothing():\n    pass\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_assert_true_rejected():
    src = "def test_trivial():\n    assert True\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_assert_literal_equality_rejected():
    src = "def test_trivial():\n    assert 1 == 1\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_real_assertion_accepted():
    src = "def test_add():\n    assert add(2, 3) == 5\n"
    assert passes_assertion_gate(_make_test(src)) is True


def test_syntax_error_rejected():
    src = "def test_broken(:\n    assert True\n"
    assert passes_assertion_gate(_make_test(src)) is False


def test_mixed_trivial_and_real_accepted():
    src = "def test_mix():\n    assert True\n    assert add(2, 3) == 5\n"
    assert passes_assertion_gate(_make_test(src)) is True


def test_assert_string_self_equality_rejected():
    src = 'def test_trivial():\n    assert "foo" == "foo"\n'
    assert passes_assertion_gate(_make_test(src)) is False
