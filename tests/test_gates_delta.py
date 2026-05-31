from pathlib import Path

from reflecta.gates import passes_delta_gate
from reflecta.loop import process_test
from reflecta.models import CoverageTarget, GeneratedTest, TargetStatus


# ---------------------------------------------------------------------------
# passes_delta_gate unit tests
# ---------------------------------------------------------------------------


def test_delta_gate_passes_when_coverage_rises():
    assert passes_delta_gate(60.0, 65.0) is True


def test_delta_gate_fails_when_coverage_same():
    assert passes_delta_gate(60.0, 60.0) is False


def test_delta_gate_fails_when_coverage_drops():
    assert passes_delta_gate(60.0, 59.0) is False


# ---------------------------------------------------------------------------
# process_test integration tests (loop stub)
# ---------------------------------------------------------------------------


def _make_test(tmp_path: Path) -> GeneratedTest:
    target = CoverageTarget(
        file_path=tmp_path / "calc.py",
        qualified_name="calc.add",
        missing_lines=[5],
    )
    test_file = tmp_path / "test_reflecta_calc_0.py"
    test_file.write_text("def test_add():\n    assert 1 + 1 == 2\n")
    return GeneratedTest(
        target=target,
        test_file_path=test_file,
        source_code="def test_add():\n    assert 1 + 1 == 2\n",
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )


def test_kept_file_remains_on_pass(tmp_path):
    test = _make_test(tmp_path)
    # coverage rose: before=60.0, after=65.0
    outcome = process_test(test, coverage_before=60.0, coverage_after=65.0)

    assert outcome == "kept"
    assert test.test_file_path.exists()
    assert test.target.status == TargetStatus.KEPT


def test_discarded_file_removed_on_fail(tmp_path):
    test = _make_test(tmp_path)
    # coverage did not rise: before=60.0, after=60.0
    outcome = process_test(test, coverage_before=60.0, coverage_after=60.0)

    assert outcome == "discarded"
    assert not test.test_file_path.exists()
    assert test.target.status == TargetStatus.DISCARDED
