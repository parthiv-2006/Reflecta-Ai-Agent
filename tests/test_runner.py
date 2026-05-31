import sys
from unittest.mock import MagicMock, patch

from reflecta.runner import run_test


def test_runner_uses_sys_executable(tmp_path):
    """Regression (HARDENING-0-9 §1.4): the runner must invoke the active
    interpreter, not a bare 'python' that may resolve to a different env."""
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_ok():\n    assert True\n")

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("", "")
    fake_proc.returncode = 0

    with patch(
        "reflecta.runner.subprocess.Popen", return_value=fake_proc
    ) as mock_popen:
        run_test(test_file, tmp_path)

    cmd = mock_popen.call_args.args[0]
    assert cmd[0] == sys.executable


def test_child_env_strips_api_keys(monkeypatch):
    """Regression (HARDENING-0-9 §1.2): generated tests run with a scrubbed env
    so they cannot read provider secrets."""
    from reflecta.runner import child_env

    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = child_env()

    assert "GEMINI_API_KEY" not in env
    assert "GROQ_API_KEY" not in env
    assert env.get("PATH") == "/usr/bin"


def test_runner_passes_scrubbed_env_to_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini")
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_ok():\n    assert True\n")

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("", "")
    fake_proc.returncode = 0

    with patch(
        "reflecta.runner.subprocess.Popen", return_value=fake_proc
    ) as mock_popen:
        run_test(test_file, tmp_path)

    passed_env = mock_popen.call_args.kwargs["env"]
    assert "GEMINI_API_KEY" not in passed_env


def test_passing_test_returns_passed(tmp_path):
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_ok():\n    assert 1 + 1 == 2\n")

    result = run_test(test_file, tmp_path)

    assert result.passed is True
    assert result.traceback == ""
    assert result.duration >= 0.0


def test_failing_test_returns_traceback(tmp_path):
    test_file = tmp_path / "test_fail.py"
    test_file.write_text("def test_fail():\n    assert 1 == 2\n")

    result = run_test(test_file, tmp_path)

    assert result.passed is False
    assert "FAILED" in result.traceback


def test_timeout_kills_process(tmp_path):
    test_file = tmp_path / "test_hang.py"
    test_file.write_text("import time\ndef test_hang():\n    time.sleep(100)\n")

    result = run_test(test_file, tmp_path, timeout_s=1)

    assert result.passed is False
    assert "timeout" in result.traceback
