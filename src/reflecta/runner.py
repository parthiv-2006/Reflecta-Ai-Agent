import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from reflecta.models import RunResult


def child_env() -> dict[str, str]:
    """Environment for subprocesses that execute LLM-generated tests.

    Strips every ``*_API_KEY`` so a generated (or prompt-injected) test cannot
    read the provider secrets that live in the parent process. The interpreter
    still needs PATH/SYSTEMROOT/PYTHONPATH, so we copy everything else.
    """
    return {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}


def run_test(test_file: Path, repo_path: Path, timeout_s: int = 30) -> RunResult:
    start = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", str(test_file), "--tb=short", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=repo_path,
        env=child_env(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        duration = time.monotonic() - start
        passed = proc.returncode == 0
        tb = "" if passed else (stdout + stderr).strip()
        return RunResult(passed=passed, traceback=tb, duration=duration)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        duration = time.monotonic() - start
        return RunResult(passed=False, traceback="timeout", duration=duration)


def run_test_isolated(
    test_file: Path, repo_path: Path, timeout_s: int = 30
) -> RunResult:
    """Run a generated test in a disposable temp copy of the repo.

    Prevents a malicious or buggy generated test from deleting or writing files
    in the actual working tree. The original test file and all source files are
    untouched regardless of what the generated test does.
    """
    repo_path = Path(repo_path).resolve()
    test_file = Path(test_file).resolve()
    rel = test_file.relative_to(repo_path)

    tmp_root = Path(tempfile.mkdtemp(prefix="reflecta_iso_"))
    try:
        tmp_repo = tmp_root / "repo"
        shutil.copytree(
            repo_path,
            tmp_repo,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                "*.pyc",
                ".venv",
                "venv",
                ".reflecta",
                ".pytest_cache",
            ),
        )
        return run_test(tmp_repo / rel, tmp_repo, timeout_s=timeout_s)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
