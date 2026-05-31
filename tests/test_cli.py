"""Task 9 — CLI and report tests (run_loop mocked, no real LLM calls)."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


from reflecta.models import RunReport


def _minimal_report(repo_path: Path) -> RunReport:
    return RunReport(
        repo_path=repo_path,
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        coverage_before=65.0,
        coverage_after=78.5,
        targets=[],
        tests_kept=3,
        tests_discarded=1,
        repair_attempts_used=2,
        stop_reason="exhausted",
    )


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------


def test_run_writes_report_and_prints_summary(tmp_path, monkeypatch):
    """reflecta run writes reflecta-report.json and prints the summary."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    report = _minimal_report(tmp_path)

    with patch("reflecta.cli.run_loop", return_value=report):
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    report_path = tmp_path / "reflecta-report.json"
    assert report_path.exists(), "reflecta-report.json not written"
    data = json.loads(report_path.read_text())
    assert data["tests_kept"] == 3
    assert "65.0" in result.output or "Coverage" in result.output
    assert "78.5" in result.output


def test_run_summary_format(tmp_path, monkeypatch):
    """Summary line shows before/after/delta, kept, discarded, repairs, stop reason."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    report = _minimal_report(tmp_path)

    with patch("reflecta.cli.run_loop", return_value=report):
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--path", str(tmp_path)])

    assert "65.0" in result.output
    assert "78.5" in result.output
    assert "3" in result.output  # kept
    assert "exhausted" in result.output


# ---------------------------------------------------------------------------
# clean command
# ---------------------------------------------------------------------------


def test_clean_removes_only_reflecta_files(tmp_path):
    """reflecta clean deletes _reflecta generated tests and leaves human tests untouched."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    # Set up fixture
    reflecta_dir = tmp_path / "tests" / "_reflecta"
    reflecta_dir.mkdir(parents=True)
    generated = reflecta_dir / "test_reflecta_calc_0.py"
    generated.write_text("def test_x(): pass\n")

    tests_dir = tmp_path / "tests"
    human = tests_dir / "test_calc.py"
    human.write_text("def test_human(): assert 1 == 1\n")

    runner = CliRunner()
    result = runner.invoke(app, ["clean", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert not generated.exists(), "Generated test should have been removed"
    assert human.exists(), "Human test must NOT be touched"


def test_clean_no_reflecta_dir_is_ok(tmp_path):
    """reflecta clean on a repo with no _reflecta dir exits cleanly."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["clean", "--path", str(tmp_path)])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# report --last command
# ---------------------------------------------------------------------------


def test_report_last_prints_summary(tmp_path):
    """reflecta report --last reads the JSON and reprints the summary."""
    from reflecta.cli import app
    from reflecta.report import write_report
    from typer.testing import CliRunner

    report = _minimal_report(tmp_path)
    write_report(report, tmp_path / "reflecta-report.json")

    runner = CliRunner()
    result = runner.invoke(app, ["report", "--last", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "65.0" in result.output
    assert "78.5" in result.output
    assert "exhausted" in result.output


def test_report_last_missing_file_exits_with_error(tmp_path):
    """reflecta report --last with no previous run exits with a clear error message."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["report", "--last", "--path", str(tmp_path)])

    assert result.exit_code != 0 or "No report" in result.output
