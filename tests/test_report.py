"""Round-trip tests for report serialization (AUDIT M1)."""

from datetime import datetime

from reflecta.models import CoverageTarget, RunReport, TargetStatus
from reflecta.report import read_report, write_report


def _sample_report(repo_path) -> RunReport:
    return RunReport(
        repo_path=repo_path,
        started_at=datetime(2026, 6, 1, 12, 0, 0),
        coverage_before=40.0,
        coverage_after=72.5,
        targets=[
            CoverageTarget(
                file_path=repo_path / "mod.py",
                qualified_name="Calc.add",
                missing_lines=[3, 4],
                priority=2.0,
                status=TargetStatus.KEPT,
            )
        ],
        tests_kept=3,
        tests_discarded=1,
        repair_attempts_used=2,
        escalations_attempted=2,
        escalations_succeeded=1,
        budget="7/50",
        stop_reason="exhausted",
    )


def test_report_round_trip_preserves_all_numeric_fields(tmp_path):
    """write_report → read_report must not lose any field — escalation counts
    were silently dropped before the fix."""
    original = _sample_report(tmp_path)
    path = tmp_path / "reflecta-report.json"

    write_report(original, path)
    restored = read_report(path)

    assert restored.coverage_before == original.coverage_before
    assert restored.coverage_after == original.coverage_after
    assert restored.tests_kept == original.tests_kept
    assert restored.tests_discarded == original.tests_discarded
    assert restored.repair_attempts_used == original.repair_attempts_used
    assert restored.escalations_attempted == original.escalations_attempted
    assert restored.escalations_succeeded == original.escalations_succeeded
    assert restored.budget == original.budget
    assert restored.stop_reason == original.stop_reason
    assert restored.started_at == original.started_at
    assert len(restored.targets) == 1
    assert restored.targets[0].qualified_name == "Calc.add"
    assert restored.targets[0].status == TargetStatus.KEPT


def test_report_read_tolerates_legacy_json_without_escalation_fields(tmp_path):
    """A report written before escalation tracking existed must still load."""
    path = tmp_path / "reflecta-report.json"
    path.write_text(
        """
        {
            "repo_path": "/repo",
            "started_at": "2026-06-01T12:00:00",
            "coverage_before": 10.0,
            "coverage_after": 20.0,
            "targets": [],
            "tests_kept": 1,
            "tests_discarded": 0,
            "repair_attempts_used": 0,
            "budget": "1/50",
            "stop_reason": "exhausted"
        }
        """,
        encoding="utf-8",
    )

    restored = read_report(path)

    assert restored.escalations_attempted == 0
    assert restored.escalations_succeeded == 0
