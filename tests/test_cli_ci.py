"""CLI tests for `reflecta ci` (run_loop mocked; no real LLM, git, or network)."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from reflecta.ci import CIPlan, CIResult
from reflecta.forge import PullRequest
from reflecta.models import CoverageTarget, RunReport, TargetStatus


def _report(repo_path: Path, kept=2) -> RunReport:
    return RunReport(
        repo_path=repo_path,
        started_at=datetime(2026, 1, 1),
        coverage_before=70.0,
        coverage_after=88.0,
        targets=[
            CoverageTarget(
                file_path=repo_path / "calc.py",
                qualified_name=f"calc.f{i}",
                missing_lines=[10 + i],
                status=TargetStatus.KEPT,
            )
            for i in range(kept)
        ],
        tests_kept=kept,
        tests_discarded=0,
        repair_attempts_used=0,
        stop_reason="exhausted",
    )


def _write_kept(repo_path: Path, n=2) -> None:
    d = repo_path / "tests" / "_reflecta"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"test_reflecta_calc_{i}.py").write_text(
            "def test_x():\n    assert True\n"
        )


def test_ci_dry_run_prints_plan_no_side_effects(tmp_path, monkeypatch):
    from reflecta.cli import app

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    _write_kept(tmp_path, 2)

    # If anything tried real git/network it would explode; assert it doesn't.
    with (
        patch("reflecta.cli.run_loop", return_value=_report(tmp_path, 2)),
        patch("reflecta.git_ops.push", side_effect=AssertionError("must not push")),
    ):
        result = CliRunner().invoke(app, ["ci", "--path", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "CI dry-run" in result.output
    assert "reflecta/auto-tests" in result.output
    assert "reflecta added 2 tests" in result.output


def test_ci_opens_pr(tmp_path, monkeypatch):
    from reflecta.cli import app

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    _write_kept(tmp_path, 1)

    fake_result = CIResult(
        status="opened",
        pr=PullRequest(number=5, url="https://gh/pr/5"),
        plan=CIPlan("reflecta/auto-tests", "main", "msg", "title", "body", []),
    )
    with (
        patch("reflecta.cli.run_loop", return_value=_report(tmp_path, 1)),
        patch("reflecta.ci.submit", return_value=fake_result) as submit,
    ):
        result = CliRunner().invoke(app, ["ci", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Opened pull request" in result.output
    assert "https://gh/pr/5" in result.output
    # dry_run defaulted False and base/head threaded through
    _, kwargs = submit.call_args
    assert kwargs["dry_run"] is False
    assert kwargs["head_branch"] == "reflecta/auto-tests"


def test_ci_reads_reflecta_toml_defaults(tmp_path, monkeypatch):
    from reflecta.cli import app

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    (tmp_path / "reflecta.toml").write_text(
        '[tool.reflecta]\nmax_iters = 3\nmutation = true\nhead_branch = "reflecta/bot"\n'
    )
    _write_kept(tmp_path, 1)

    captured = {}

    def fake_run_loop(path, **kw):
        captured.update(kw)
        return _report(tmp_path, 1)

    fake_result = CIResult(
        "dry_run", None, CIPlan("reflecta/bot", "main", "m", "t", "b", [])
    )
    with (
        patch("reflecta.cli.run_loop", side_effect=fake_run_loop),
        patch("reflecta.ci.submit", return_value=fake_result) as submit,
    ):
        result = CliRunner().invoke(app, ["ci", "--path", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    # toml values flowed into the loop
    assert captured["max_iters"] == 3
    assert captured["mutation"] is True
    # and into the ci submit head branch
    assert submit.call_args.kwargs["head_branch"] == "reflecta/bot"


def test_ci_cli_flag_overrides_toml(tmp_path, monkeypatch):
    from reflecta.cli import app

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    (tmp_path / "reflecta.toml").write_text("[tool.reflecta]\nmax_iters = 3\n")
    _write_kept(tmp_path, 1)

    captured = {}

    def fake_run_loop(path, **kw):
        captured.update(kw)
        return _report(tmp_path, 1)

    fake_result = CIResult(
        "dry_run", None, CIPlan("reflecta/auto-tests", "main", "m", "t", "b", [])
    )
    with (
        patch("reflecta.cli.run_loop", side_effect=fake_run_loop),
        patch("reflecta.ci.submit", return_value=fake_result),
    ):
        result = CliRunner().invoke(
            app, ["ci", "--path", str(tmp_path), "--max-iters", "9", "--dry-run"]
        )

    assert result.exit_code == 0, result.output
    assert captured["max_iters"] == 9  # explicit flag beats toml's 3
