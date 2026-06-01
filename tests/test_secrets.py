"""Task 13 — secrets pass regression tests.

Confirms that API keys never appear in the run report, logs, or error messages,
and that .env is excluded from version control.
"""

from datetime import datetime
from pathlib import Path

import pytest

from reflecta.models import RunReport
from reflecta.report import write_report
from reflecta.runner import child_env


def _minimal_report() -> RunReport:
    return RunReport(
        repo_path=Path("/fake/repo"),
        started_at=datetime(2026, 1, 1),
        coverage_before=50.0,
        coverage_after=60.0,
        targets=[],
    )


def test_gitignore_contains_dotenv():
    """Hard rule 5: .env must be gitignored so API keys are never committed."""
    gitignore = Path(__file__).parents[1] / ".gitignore"
    assert gitignore.exists(), ".gitignore file must exist"
    lines = gitignore.read_text(encoding="utf-8").splitlines()
    assert ".env" in lines, ".env must appear as a standalone line in .gitignore"


def test_report_does_not_expose_api_key_values(tmp_path, monkeypatch):
    """write_report must not serialize API key values even if they're in the env."""
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-value-xyz")
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-value-xyz")

    report_path = tmp_path / "reflecta-report.json"
    write_report(_minimal_report(), report_path)

    content = report_path.read_text(encoding="utf-8")
    assert "secret-gemini-value-xyz" not in content
    assert "secret-groq-value-xyz" not in content


def test_child_env_strips_all_api_key_vars(monkeypatch):
    """child_env() must strip any env var whose name ends with _API_KEY."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret")
    monkeypatch.setenv("MY_CUSTOM_API_KEY", "custom-secret")
    monkeypatch.setenv("SAFE_VAR", "not-a-key")

    env = child_env()

    assert "GEMINI_API_KEY" not in env
    assert "GROQ_API_KEY" not in env
    assert "MY_CUSTOM_API_KEY" not in env
    assert env.get("SAFE_VAR") == "not-a-key"


def test_config_error_names_variable_not_value(monkeypatch):
    """require_api_keys() error message must name the missing variable, not expose a value."""
    from reflecta.config import require_api_keys

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(EnvironmentError) as exc_info:
        require_api_keys()

    msg = str(exc_info.value)
    assert "GEMINI_API_KEY" in msg, "Error message must name the missing key"
    assert "secret" not in msg.lower(), "Error message must not contain a secret value"


def test_report_budget_field_contains_no_secrets(tmp_path, monkeypatch):
    """The budget string (e.g. '5/50') must not contain any key values."""
    monkeypatch.setenv("GEMINI_API_KEY", "budget-leak-canary")

    report = _minimal_report()
    report.budget = "5/50"
    report_path = tmp_path / "r.json"
    write_report(report, report_path)

    content = report_path.read_text(encoding="utf-8")
    assert "budget-leak-canary" not in content
