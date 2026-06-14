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


def test_run_forwards_budget_and_stop_options(tmp_path, monkeypatch):
    """HARDENING-0-9 §4.6: --max-llm-calls/--target-coverage/--stall-k reach run_loop."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    report = _minimal_report(tmp_path)

    with patch("reflecta.cli.run_loop", return_value=report) as mock_loop:
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run",
                "--path",
                str(tmp_path),
                "--max-llm-calls",
                "7",
                "--target-coverage",
                "90",
                "--stall-k",
                "4",
            ],
        )

    assert result.exit_code == 0, result.output
    kwargs = mock_loop.call_args.kwargs
    assert kwargs["max_llm_calls"] == 7
    assert kwargs["target_coverage"] == 90.0
    assert kwargs["stall_k"] == 4


def test_run_reads_reflecta_toml_defaults(tmp_path, monkeypatch):
    """reflecta.toml [tool.reflecta] defaults flow into run_loop under `run`."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    (tmp_path / "reflecta.toml").write_text(
        "[tool.reflecta]\n"
        "max_iters = 3\n"
        "mutation = true\n"
        "min_mutation_score = 0.6\n"
        "attempt_risky = true\n"
    )
    report = _minimal_report(tmp_path)

    with patch("reflecta.cli.run_loop", return_value=report) as mock_loop:
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    kwargs = mock_loop.call_args.kwargs
    assert kwargs["max_iters"] == 3
    assert kwargs["mutation"] is True
    assert kwargs["min_mutation_score"] == 0.6
    assert kwargs["attempt_risky"] is True


def test_run_cli_flag_overrides_toml(tmp_path, monkeypatch):
    """An explicit CLI flag beats a reflecta.toml value (CLI > toml > default)."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    (tmp_path / "reflecta.toml").write_text("[tool.reflecta]\nmax_iters = 3\n")
    report = _minimal_report(tmp_path)

    with patch("reflecta.cli.run_loop", return_value=report) as mock_loop:
        runner = CliRunner()
        result = runner.invoke(
            app, ["run", "--path", str(tmp_path), "--max-iters", "9"]
        )

    assert result.exit_code == 0, result.output
    assert mock_loop.call_args.kwargs["max_iters"] == 9  # flag beats toml's 3


def test_run_without_toml_uses_builtin_defaults(tmp_path, monkeypatch):
    """No reflecta.toml + no flags → built-in defaults reach run_loop."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    report = _minimal_report(tmp_path)

    with patch("reflecta.cli.run_loop", return_value=report) as mock_loop:
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    kwargs = mock_loop.call_args.kwargs
    assert kwargs["max_iters"] == 20
    assert kwargs["max_repairs"] == 2
    assert kwargs["max_llm_calls"] == 50
    assert kwargs["stall_k"] == 7
    assert kwargs["skip_entrypoints"] is True
    assert kwargs["attempt_risky"] is False
    assert kwargs["mutation"] is False
    assert kwargs["min_mutation_score"] == 0.5
    assert kwargs["max_mutants"] == 30


def test_run_invalid_toml_exits_with_error(tmp_path, monkeypatch):
    """An unparseable reflecta.toml fails fast rather than silently ignoring it."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    (tmp_path / "reflecta.toml").write_text("this is = = not toml")

    with patch("reflecta.cli.run_loop") as mock_loop:
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--path", str(tmp_path)])

    assert result.exit_code != 0
    mock_loop.assert_not_called()


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


# ---------------------------------------------------------------------------
# login / logout (remote key-broker mode)
# ---------------------------------------------------------------------------


def test_login_saves_token_and_enables_remote(tmp_path, monkeypatch):
    from reflecta.cli import app
    from reflecta.llm import remote
    from typer.testing import CliRunner

    monkeypatch.setenv("REFLECTA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("REFLECTA_TOKEN", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["login", "--token", "tok_abc123"])

    assert result.exit_code == 0, result.output
    assert "Logged in" in result.output
    assert remote.get_token() == "tok_abc123"
    assert remote.remote_enabled() is True


def test_logout_removes_credentials(tmp_path, monkeypatch):
    from reflecta.cli import app
    from reflecta.llm import remote
    from typer.testing import CliRunner

    monkeypatch.setenv("REFLECTA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("REFLECTA_TOKEN", raising=False)
    remote.save_credentials("tok_abc123")

    runner = CliRunner()
    result = runner.invoke(app, ["logout"])

    assert result.exit_code == 0, result.output
    assert "Logged out" in result.output
    assert remote.remote_enabled() is False


def test_run_in_remote_mode_needs_no_provider_keys(tmp_path, monkeypatch):
    """With a token set, `run` must not demand GEMINI/GROQ keys."""
    from reflecta.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("REFLECTA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("REFLECTA_TOKEN", "tok")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    report = _minimal_report(tmp_path)
    with patch("reflecta.cli.run_loop", return_value=report):
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
