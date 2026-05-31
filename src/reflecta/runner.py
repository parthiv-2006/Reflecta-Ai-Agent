import subprocess
import time
from pathlib import Path

from reflecta.models import RunResult


def run_test(test_file: Path, repo_path: Path, timeout_s: int = 30) -> RunResult:
    start = time.monotonic()
    proc = subprocess.Popen(
        ["python", "-m", "pytest", str(test_file), "--tb=short", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=repo_path,
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
