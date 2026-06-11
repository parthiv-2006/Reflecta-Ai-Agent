import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from reflecta.models import RunResult


def child_env(repo_path: Path | None = None) -> dict[str, str]:
    """Environment for subprocesses that execute LLM-generated tests.

    Strips every ``*_API_KEY`` and every reflecta-specific credential/token so
    a generated (or prompt-injected) test cannot read the provider secrets or
    the reflecta auth token that live in the parent process. The interpreter
    still needs PATH/SYSTEMROOT/PYTHONPATH, so we copy everything else.

    Scrubbed names (exact match or suffix):
      • anything ending in ``_API_KEY``   — provider keys (Gemini, Groq, Anthropic…)
      • ``REFLECTA_TOKEN``                — user's key-broker auth token
      • ``REFLECTA_TOKENS``               — operator's token list (proxy side)
      • ``REFLECTA_CONFIG_DIR``           — not a secret, but no test needs it
    """
    _SCRUB_EXACT = frozenset({"REFLECTA_TOKEN", "REFLECTA_TOKENS", "REFLECTA_CONFIG_DIR"})
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.endswith("_API_KEY") and k not in _SCRUB_EXACT
    }
    if repo_path is not None:
        repo_path = Path(repo_path).resolve()
        if repo_path.exists():
            from reflecta.loop import _source_dirs
            sources = [str(repo_path / s) for s in _source_dirs(repo_path)]
            sources.append(str(repo_path))
            existing_pythonpath = os.environ.get("PYTHONPATH", "")
            if existing_pythonpath:
                sources.append(existing_pythonpath)
            env["PYTHONPATH"] = os.pathsep.join(sources)
    return env


def _classify_failure(returncode: int, traceback: str) -> str:
    """Map a non-zero pytest exit code + output to a failure kind.

    pytest exit codes: 1=tests failed, 2=collection/internal error, 5=no tests
    collected. A missing module at collection (``ModuleNotFoundError`` /
    ``ImportError``) is an environment problem repair can never fix, so it is
    split out from ordinary collection errors and from genuine test failures.
    """
    if returncode == 5:
        return "no_tests"
    if "ModuleNotFoundError" in traceback or "ImportError" in traceback:
        return "import_error"
    if returncode == 2:
        return "collection_error"
    return "test_failure"


def run_test(
    test_file: Path,
    repo_path: Path,
    timeout_s: int = 30,
    python_exe: str | None = None,
) -> RunResult:
    from reflecta.environment import detect_interpreter

    exe = python_exe or detect_interpreter(repo_path)
    start = time.monotonic()
    proc = subprocess.Popen(
        [exe, "-m", "pytest", str(test_file), "--tb=short", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=repo_path,
        env=child_env(repo_path),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        duration = time.monotonic() - start
        passed = proc.returncode == 0
        tb = "" if passed else (stdout + stderr).strip()
        kind = "" if passed else _classify_failure(proc.returncode, tb)
        return RunResult(
            passed=passed, traceback=tb, duration=duration, failure_kind=kind
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        duration = time.monotonic() - start
        return RunResult(
            passed=False, traceback="timeout", duration=duration, failure_kind="timeout"
        )


def run_test_isolated(
    test_file: Path,
    repo_path: Path,
    timeout_s: int = 30,
    python_exe: str | None = None,
) -> RunResult:
    """Run a generated test in a disposable temp copy of the repo.

    Prevents a malicious or buggy generated test from deleting or writing files
    in the actual working tree. The original test file and all source files are
    untouched regardless of what the generated test does.
    """
    from reflecta.environment import detect_interpreter

    repo_path = Path(repo_path).resolve()
    test_file = Path(test_file).resolve()
    rel = test_file.relative_to(repo_path)
    # Detect the interpreter on the *original* repo — the temp copy excludes the
    # virtualenv, so detection must happen before copytree.
    exe = python_exe or detect_interpreter(repo_path)

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
                "node_modules",
                "build",
                "dist",
                ".omc",
            ),
        )
        return run_test(
            tmp_repo / rel, tmp_repo, timeout_s=timeout_s, python_exe=exe
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
