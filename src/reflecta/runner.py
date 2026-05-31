import subprocess
import time
from pathlib import Path

from reflecta.models import RunResult


def run_test(test_file: Path, repo_path: Path, timeout_s: int = 30) -> RunResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", str(test_file), "--tb=short", "-q"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=timeout_s,
        )
        duration = time.monotonic() - start
        passed = proc.returncode == 0
        tb = "" if passed else (proc.stdout + proc.stderr).strip()
        return RunResult(passed=passed, traceback=tb, duration=duration)
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return RunResult(passed=False, traceback="timeout", duration=duration)
