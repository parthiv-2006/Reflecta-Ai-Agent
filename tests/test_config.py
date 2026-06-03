"""HARDENING-0-9 §2.2 — .env loading and API-key preflight."""

import pytest

from reflecta.config import load_dotenv, require_api_keys


def test_load_dotenv_populates_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        'GEMINI_API_KEY="abc123"\n# comment\nGROQ_API_KEY=def\n'
    )

    load_dotenv(tmp_path)

    import os

    assert os.environ["GEMINI_API_KEY"] == "abc123"
    assert os.environ["GROQ_API_KEY"] == "def"


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-shell")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=from-file\n")

    load_dotenv(tmp_path)

    import os

    assert os.environ["GEMINI_API_KEY"] == "from-shell"


def test_load_dotenv_missing_file_is_noop(tmp_path):
    load_dotenv(tmp_path)  # no .env present — must not raise


def test_require_api_keys_names_missing_variable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "present")

    with pytest.raises(EnvironmentError) as exc:
        require_api_keys()

    assert "GEMINI_API_KEY" in str(exc.value)


def test_require_api_keys_passes_when_all_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "y")
    require_api_keys()  # must not raise


def test_cli_run_missing_key_exits_with_clear_message(tmp_path, monkeypatch):
    """The preflight failure surfaces a named-variable message, not a traceback."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    from reflecta.cli import app

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    # Prevent the real repo .env from satisfying the check.
    with patch("reflecta.cli.load_dotenv", lambda *a, **kw: None):
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--path", str(tmp_path)])

    assert result.exit_code == 1
    assert "GEMINI_API_KEY" in result.output
