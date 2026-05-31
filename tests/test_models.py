from datetime import datetime
from pathlib import Path

from reflecta.models import (
    CoverageTarget,
    GeneratedTest,
    RepairAttempt,
    RepairResult,
    RunReport,
    TargetStatus,
)


def test_coverage_target_defaults():
    t = CoverageTarget(
        file_path=Path("src/foo.py"),
        qualified_name="foo.bar",
        missing_lines=[1, 2, 3],
    )
    assert t.file_path == Path("src/foo.py")
    assert t.qualified_name == "foo.bar"
    assert t.missing_lines == [1, 2, 3]
    assert t.priority == 0.0
    assert t.status is TargetStatus.PENDING


def test_coverage_target_status_values():
    statuses = {s.value for s in TargetStatus}
    assert statuses == {"pending", "generating", "repairing", "kept", "discarded", "escalated", "failed"}


def test_generated_test_defaults():
    target = CoverageTarget(Path("x.py"), "x.f", [1])
    gt = GeneratedTest(
        target=target,
        test_file_path=Path("tests/_reflecta/test_reflecta_x_0.py"),
        source_code="def test_f():\n    assert f() == 1\n",
        model_used="gemini-2.5-flash",
    )
    assert gt.assertion_count == 0
    assert gt.model_used == "gemini-2.5-flash"
    assert gt.target is target


def test_repair_attempt_fields():
    ra = RepairAttempt(
        attempt_number=1,
        traceback="AssertionError: expected 1 got 2",
        model_used="llama-3.1-8b-instant",
        result=RepairResult.FAIL,
    )
    assert ra.attempt_number == 1
    assert ra.result is RepairResult.FAIL
    assert RepairResult.PASS.value == "pass"
    assert RepairResult.FAIL.value == "fail"


def test_run_report_defaults():
    rr = RunReport(
        repo_path=Path("."),
        started_at=datetime(2026, 5, 31, 12, 0, 0),
        coverage_before=66.7,
        coverage_after=100.0,
    )
    assert rr.tests_kept == 0
    assert rr.tests_discarded == 0
    assert rr.repair_attempts_used == 0
    assert rr.stop_reason == ""
    assert rr.budget == ""
    assert rr.targets == []


def test_import_all_names():
    # Canonical import path that all future modules must use — never redefine these.
    from reflecta.models import (  # noqa: F401
        CoverageTarget,
        GeneratedTest,
        RepairAttempt,
        RepairResult,
        RunReport,
        TargetStatus,
    )
