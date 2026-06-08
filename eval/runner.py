"""
runner.py — eval harness subprocess driver.

run_fixture() copies a fixture to a temp directory, invokes ``reflecta run``
as a subprocess, reads the resulting ``reflecta-report.json``, and maps the
fields to an EvalMetrics instance.  The temp copy is always cleaned up, even
on failure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from eval.metrics import EvalMetrics

# Path to this repository root so we can locate the eval/fixtures/ directory.
_REPO_ROOT = Path(__file__).parent.parent


def _fixtures_dir() -> Path:
    return _REPO_ROOT / "eval" / "fixtures"


def run_fixture(
    fixture_name: str,
    cache_dir: Path | None = None,
    python: str | None = None,
    verbose: bool = False,
    extra_flags: list[str] | None = None,
) -> EvalMetrics:
    """Run the harness against one fixture and return the collected EvalMetrics.

    Steps:
    1. Locate ``eval/fixtures/<fixture_name>/``.
    2. Copy it to a temp directory so the original is never mutated.
    3. Run ``reflecta run --path <tmp> --max-iters 10`` as a subprocess.
    4. Read the resulting ``reflecta-report.json``.
    5. Map fields to EvalMetrics and clean up the temp copy.

    Parameters
    ----------
    fixture_name:
        Name of the directory under ``eval/fixtures/``.
    cache_dir:
        When provided, passed as ``--cache-dir`` so LLM responses are stored
        or replayed from that directory (CI-safe caching).
    python:
        Override the interpreter for the fixture subprocess.  Defaults to the
        same interpreter running this process.
    verbose:
        Pass ``--verbose`` to reflecta run.
    extra_flags:
        Additional CLI flags forwarded verbatim to ``reflecta run``.
    """
    fixture_dir = _fixtures_dir() / fixture_name
    if not fixture_dir.is_dir():
        raise FileNotFoundError(
            f"Fixture '{fixture_name}' not found at {fixture_dir}"
        )

    python_exe = python or sys.executable
    tmp_root = Path(tempfile.mkdtemp(prefix=f"reflecta_eval_{fixture_name}_"))
    try:
        tmp_fixture = tmp_root / fixture_name
        shutil.copytree(
            fixture_dir,
            tmp_fixture,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache",
                "coverage_baseline.json",  # do not carry the committed baseline
            ),
        )

        cmd = [
            python_exe,
            "-m",
            "reflecta",
            "run",
            "--path",
            str(tmp_fixture),
            "--max-iters",
            "10",
        ]
        if cache_dir is not None:
            cmd += ["--cache-dir", str(cache_dir)]  # type: ignore[arg-type]
        if python is not None:
            cmd += ["--python", python]
        if verbose:
            cmd.append("--verbose")
        if extra_flags:
            cmd.extend(extra_flags)

        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=str(tmp_fixture),
            capture_output=not verbose,
            text=True,
        )
        elapsed = time.monotonic() - start

        report_path = tmp_fixture / "reflecta-report.json"
        if not report_path.exists():
            # reflecta may exit early (no testable targets) without writing a
            # report.  Build a minimal EvalMetrics reflecting that outcome.
            stop_reason = _infer_stop_reason(proc.stdout or "", proc.stderr or "")
            return EvalMetrics(
                fixture_name=fixture_name,
                coverage_before=0.0,
                coverage_after=0.0,
                coverage_delta=0.0,
                targets_attempted=0,
                tests_accepted=0,
                tests_discarded=0,
                repair_attempts_used=0,
                targets_skipped_blocked=0,
                targets_skipped_risky=0,
                targets_skipped_entrypoint=0,
                llm_calls_gemini=0,
                llm_calls_groq=0,
                llm_calls_claude=0,
                run_time_seconds=elapsed,
                stop_reason=stop_reason,
            )

        data = json.loads(report_path.read_text(encoding="utf-8"))
        return _metrics_from_report(fixture_name, data, elapsed)

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _infer_stop_reason(stdout: str, stderr: str) -> str:
    """Best-effort stop reason when no report JSON was written."""
    combined = (stdout + stderr).lower()
    if "no testable" in combined or "no_testable" in combined:
        return "no_testable_targets"
    if "no_targets" in combined or "no targets" in combined:
        return "no_targets"
    return "unknown"


def _metrics_from_report(
    fixture_name: str, data: dict, run_time_seconds: float
) -> EvalMetrics:
    """Map a deserialized ``reflecta-report.json`` dict to EvalMetrics."""
    targets = data.get("targets", [])

    targets_attempted = sum(
        1 for t in targets if t.get("status") in ("kept", "discarded", "failed", "escalated")
    )
    tests_accepted = data.get("tests_kept", 0)
    tests_discarded = data.get("tests_discarded", 0)
    repair_attempts_used = data.get("repair_attempts_used", 0)

    skipped_blocked = sum(
        1
        for t in targets
        if t.get("status") == "skipped" and t.get("testability") == "blocked"
    )
    skipped_risky = sum(
        1
        for t in targets
        if t.get("status") == "skipped" and t.get("testability") == "risky"
    )
    skipped_entrypoint = sum(
        1 for t in targets if t.get("status") == "skipped" and t.get("is_entrypoint", False)
    )

    coverage_before = data.get("coverage_before", 0.0)
    coverage_after = data.get("coverage_after", 0.0)

    return EvalMetrics(
        fixture_name=fixture_name,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        coverage_delta=round(coverage_after - coverage_before, 6),
        targets_attempted=targets_attempted,
        tests_accepted=tests_accepted,
        tests_discarded=tests_discarded,
        repair_attempts_used=repair_attempts_used,
        targets_skipped_blocked=skipped_blocked,
        targets_skipped_risky=skipped_risky,
        targets_skipped_entrypoint=skipped_entrypoint,
        llm_calls_gemini=data.get("llm_calls_gemini", 0),
        llm_calls_groq=data.get("llm_calls_groq", 0),
        llm_calls_claude=data.get("llm_calls_claude", 0),
        run_time_seconds=run_time_seconds,
        stop_reason=data.get("stop_reason", ""),
    )
