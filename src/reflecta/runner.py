import os
import subprocess
import sys
import time
from pathlib import Path

from reflecta.models import RunResult


def child_env() -> dict[str, str]:
    """Environment for subprocesses that execute LLM-generated tests.

    Strips every ``*_API_KEY`` so a generated (or prompt-injected) test cannot
    read the provider secrets that live in the parent process. The interpreter
    still needs PATH/SYSTEMROOT/PYTHONPATH, so we copy everything else.
    HARDENING-0-9 §1.2.
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
