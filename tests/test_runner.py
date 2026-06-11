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


def test_child_env_strips_reflecta_token(monkeypatch):
    """Security: REFLECTA_TOKEN must be stripped from the child env so a
    generated (or prompt-injected) test cannot read the user's key-broker
    auth token and exfiltrate it."""
    from reflecta.runner import child_env

    monkeypatch.setenv("REFLECTA_TOKEN", "secret-reflecta-token")
    monkeypatch.setenv("REFLECTA_TOKENS", "tok1:100,tok2:50")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = child_env()

    assert "REFLECTA_TOKEN" not in env
    assert "REFLECTA_TOKENS" not in env
    # Non-secret vars should pass through
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


def test_child_env_injects_pythonpath(tmp_path):
    """child_env(repo_path) must inject PYTHONPATH with candidate source directories."""
    from reflecta.runner import child_env
    # Create candidate source directories in the temp repo
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "foo.py").write_text("pass\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bar.py").write_text("pass\n")
    # A skipped directory should not be in the candidate sources
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "baz.py").write_text("pass\n")

    env = child_env(tmp_path)
    pythonpath = env.get("PYTHONPATH", "")

    # PYTHONPATH must contain scripts/ and src/ and the root path, but not node_modules/
    assert str(tmp_path / "scripts") in pythonpath
    assert str(tmp_path / "src") in pythonpath
    assert str(tmp_path) in pythonpath
    assert str(tmp_path / "node_modules") not in pythonpath


def test_strip_fences_handles_conversational_filler():
    """strip_fences must extract Python code block even when surrounded by conversational filler."""
    from reflecta.llm.provider import strip_fences
    response = (
        "Here is the code you requested:\n"
        "```python\n"
        "def test_example():\n"
        "    assert 1 == 1\n"
        "```\n"
        "Hope this helps!"
    )
    extracted = strip_fences(response)
    assert extracted == "def test_example():\n    assert 1 == 1"


def test_no_tests_collected_is_classified(tmp_path):
    """An empty test file → pytest exit 5 → failure_kind 'no_tests' so the loop
    can skip it instead of routing it to (pointless) repair."""
    test_file = tmp_path / "test_empty.py"
    test_file.write_text("# nothing here\n")

    result = run_test(test_file, tmp_path)

    assert result.passed is False
    assert result.failure_kind == "no_tests"


def test_missing_import_is_classified_as_import_error(tmp_path):
    """A test importing a non-existent module → ModuleNotFoundError at
    collection → failure_kind 'import_error' (an environment problem)."""
    test_file = tmp_path / "test_badimport.py"
    test_file.write_text(
        "import this_module_definitely_does_not_exist_xyz\n"
        "def test_x():\n    assert True\n"
    )

    result = run_test(test_file, tmp_path)

    assert result.passed is False
    assert result.failure_kind == "import_error"


def test_real_assertion_failure_is_test_failure(tmp_path):
    test_file = tmp_path / "test_fail2.py"
    test_file.write_text("def test_fail():\n    assert 1 == 2\n")

    result = run_test(test_file, tmp_path)

    assert result.failure_kind == "test_failure"


def test_run_test_isolated_ignores_heavy_dirs(tmp_path):
    """run_test_isolated must ignore node_modules, build, dist, and .omc during copy."""
    from reflecta.runner import run_test_isolated
    test_file = tmp_path / "tests" / "_reflecta" / "test_ok.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_ok(): assert True")

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("", "")
    fake_proc.returncode = 0

    with (
        patch("reflecta.runner.shutil.copytree") as mock_copytree,
        patch("reflecta.runner.subprocess.Popen", return_value=fake_proc),
    ):
        run_test_isolated(test_file, tmp_path)

    assert mock_copytree.called
    ignore_func = mock_copytree.call_args.kwargs.get("ignore")
    assert ignore_func is not None

    ignored_names = ignore_func("dummy_dir", ["node_modules", "build", "dist", ".omc", "src"])
    assert "node_modules" in ignored_names
    assert "build" in ignored_names
    assert "dist" in ignored_names
    assert ".omc" in ignored_names
    assert "src" not in ignored_names

