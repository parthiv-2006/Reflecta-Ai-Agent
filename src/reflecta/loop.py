import ast
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from reflecta.budget import BudgetTracker
from reflecta.coverage_report import extract_targets
from reflecta.gates import passes_assertion_gate, passes_delta_gate
from reflecta.generate import collect_existing_tests, generate_test
from reflecta.llm.provider import BudgetExhausted
from reflecta.models import GeneratedTest, RunReport, RunResult, TargetStatus
from reflecta.repair import repair_test
from reflecta.runner import child_env, run_test_isolated
from reflecta.selection import select_next

logger = logging.getLogger("reflecta")


COVERAGE_DIR = ".reflecta"

# Wall-clock ceiling for a full-suite coverage run. Bounds a generated test that
# only hangs under full-suite conditions (a hang in isolation is already caught
# by run_test_isolated's own timeout). Generous because it covers the *whole*
# suite, not a single test.
COVERAGE_TIMEOUT_S = 300

# Directories never worth copying into an isolated coverage run.
_ISOLATION_IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    "*.pyc",
    ".venv",
    "venv",
    ".reflecta",
    ".pytest_cache",
)


def coverage_paths(repo_path: Path) -> tuple[Path, Path]:
    """Reflecta-owned coverage data-file and json paths inside ``repo_path``.

    Kept under ``.reflecta/`` so reflecta never clobbers the target repo's own
    ``.coverage`` / ``coverage.json``.
    """
    d = Path(repo_path).resolve() / COVERAGE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / ".coverage", d / "coverage.json"


def _run_coverage(
    cwd: Path, data_file: Path, json_file: Path, *, timeout_s: int
) -> tuple[float, bool]:
    """Run the suite under coverage in ``cwd``; return (percent_covered, passed).

    ``passed`` is the pytest exit status (True only if every collected test
    passed). On timeout we report ``(0.0, False)`` so a hung suite can never be
    mistaken for a coverage gain. Both subprocesses are time-boxed.
    """
    env = child_env()
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--data-file={data_file}",
                "-m",
                "pytest",
                "--tb=no",
                "-q",
            ],
            cwd=cwd,
            capture_output=True,
            env=env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 0.0, False
    passed = proc.returncode == 0
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "json",
                f"--data-file={data_file}",
                "-o",
                str(json_file),
            ],
            cwd=cwd,
            capture_output=True,
            env=env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 0.0, passed
    if not json_file.exists():
        return 0.0, passed
    data = json.loads(json_file.read_text(encoding="utf-8"))
    return data.get("totals", {}).get("percent_covered", 0.0), passed


def measure_coverage_real(repo_path: Path) -> tuple[float, bool]:
    """Baseline measurement, run in place in the real tree.

    Runs in place (not isolated) for two reasons: the resulting ``coverage.json``
    must carry the repo's real file paths so ``extract_targets`` can resolve
    them, and at baseline the only tests present are human-written or
    reflecta's own previously-kept tests — never a test generated *this*
    iteration. Coverage artifacts go under ``.reflecta/`` so the repo's own
    ``.coverage`` / ``coverage.json`` are untouched. Returns (percent, passed).
    """
    repo_path = Path(repo_path).resolve()
    data_file, json_file = coverage_paths(repo_path)
    return _run_coverage(repo_path, data_file, json_file, timeout_s=COVERAGE_TIMEOUT_S)


def measure_coverage(repo_path: Path) -> float:
    """Backwards-compatible float-only baseline measurement."""
    return measure_coverage_real(repo_path)[0]


def measure_coverage_isolated(
    repo_path: Path, *, timeout_s: int = COVERAGE_TIMEOUT_S
) -> tuple[float, bool]:
    """Measure total coverage by running the full suite in a disposable copy.

    Used after a test is generated this iteration: the generated test executes
    only against the throwaway copy, so a destructive or hanging test cannot
    corrupt the real working tree or wedge the run. The copy mirrors the real
    tree (which already contains the candidate test), so the percent it reports
    is directly comparable to the in-place baseline. Returns (percent, passed).
    """
    repo_path = Path(repo_path).resolve()
    tmp_root = Path(tempfile.mkdtemp(prefix="reflecta_cov_"))
    try:
        tmp_repo = tmp_root / "repo"
        shutil.copytree(repo_path, tmp_repo, symlinks=True, ignore=_ISOLATION_IGNORE)
        # Coverage artifacts live outside the copied tree so they are never
        # collected and never need ignoring.
        return _run_coverage(
            tmp_repo,
            tmp_root / ".coverage",
            tmp_root / "coverage.json",
            timeout_s=timeout_s,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def process_test(
    test: GeneratedTest, *, coverage_before: float, coverage_after: float
) -> str:
    """Apply the coverage-delta gate to a passing test.

    Returns "kept" if coverage strictly rose (file left on disk),
    or "discarded" if coverage did not rise (file deleted, target marked DISCARDED).
    """
    if passes_delta_gate(coverage_before, coverage_after):
        test.target.status = TargetStatus.KEPT
        return "kept"

    test.test_file_path.unlink(missing_ok=True)
    test.target.status = TargetStatus.DISCARDED
    return "discarded"


def run_loop(
    repo_path: Path,
    *,
    max_iters: int = 10,
    max_repairs: int = 2,
    max_llm_calls: int = 50,
    target_coverage: float | None = None,
    stall_k: int = 3,
    escalate: bool = False,
    max_claude_iters: int = 3,
    gemini_client=None,
    groq_client=None,
    claude_client=None,
) -> RunReport:
    """Main orchestration loop.

    extract → select → generate → assertion gate → run → [repair] → delta gate
    → keep/discard. Repeats until one of the four SPEC stop conditions fires:
    target coverage reached, max_iters hit, coverage stalled across ``stall_k``
    consecutive targets, or the LLM budget is depleted.

    Budget scope: ``max_llm_calls`` counts only free-tier calls (Gemini
    generation + Groq repair). Claude escalation draws on a separate
    subscription/quota and is bounded per target by ``max_claude_iters``; it is
    deliberately not charged against ``max_llm_calls``. Escalation activity is
    surfaced via ``RunReport.escalations_attempted`` / ``escalations_succeeded``.
    """
    repo_path = Path(repo_path).resolve()
    budget = BudgetTracker(max_llm_calls=max_llm_calls)

    coverage_before, baseline_suite_passed = measure_coverage_real(repo_path)

    _, coverage_json_path = coverage_paths(repo_path)
    if coverage_json_path.exists():
        coverage_json = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    else:
        coverage_json = {}
    targets = extract_targets(coverage_json, repo_path)

    if not targets:
        report = RunReport(
            repo_path=repo_path,
            started_at=datetime.now(),
            coverage_before=coverage_before,
            coverage_after=coverage_before,
            targets=[],
            stop_reason="no_targets",
        )
        return report

    report = RunReport(
        repo_path=repo_path,
        started_at=datetime.now(),
        coverage_before=coverage_before,
        coverage_after=coverage_before,
        targets=targets,
    )

    iter_count = 0
    stall = 0  # consecutive targets that did not raise coverage
    while (target := select_next(targets)) is not None:
        if target_coverage is not None and coverage_before >= target_coverage:
            report.stop_reason = "target_reached"
            break

        if stall >= stall_k:
            report.stop_reason = "stalled"
            break

        if iter_count >= max_iters:
            report.stop_reason = "max_iters"
            break

        if budget.exhausted():
            report.stop_reason = "budget"
            break

        target.status = TargetStatus.GENERATING
        logger.info(
            "target %s (missing=%d, priority=%.1f)",
            target.qualified_name,
            len(target.missing_lines),
            target.priority,
        )

        source = (
            target.file_path.read_text(encoding="utf-8")
            if target.file_path.exists()
            else ""
        )
        existing_tests = collect_existing_tests(repo_path, target.file_path.stem)

        try:
            test = generate_test(
                target,
                source,
                existing_tests,
                repo_path=repo_path,
                gemini_client=gemini_client,
            )
            budget.charge(1)

            try:
                ast.parse(test.source_code)
            except SyntaxError:
                # Invalid Python from the LLM — treat as a run failure so the
                # repair path gets a chance to fix it rather than silently
                # discarding the target.
                result = RunResult(
                    passed=False,
                    traceback="SyntaxError: generated code is not valid Python",
                    duration=0.0,
                )
            else:
                if not passes_assertion_gate(test):
                    test.test_file_path.unlink(missing_ok=True)
                    target.status = TargetStatus.DISCARDED
                    report.tests_discarded += 1
                    iter_count += 1
                    stall += 1
                    logger.info("  discarded: failed assertion gate")
                    continue
                result = run_test_isolated(test.test_file_path, repo_path)

            if not result.passed:
                if budget.exhausted():
                    test.test_file_path.unlink(missing_ok=True)
                    target.status = TargetStatus.FAILED
                    iter_count += 1
                    report.stop_reason = "budget"
                    break

                try:
                    repaired, attempts = repair_test(
                        test,
                        result,
                        source,
                        repo_path=repo_path,
                        max_repairs=max_repairs,
                        groq_client=groq_client,
                    )
                except BudgetExhausted:
                    # Repair provider hit its daily cap — mark this target failed
                    # and continue. Only a generation-side BudgetExhausted stops
                    # the entire loop.
                    logger.warning(
                        "repair provider exhausted on %s; skipping target",
                        target.qualified_name,
                    )
                    test.test_file_path.unlink(missing_ok=True)
                    target.status = TargetStatus.FAILED
                    iter_count += 1
                    stall += 1
                    continue
                report.repair_attempts_used += len(attempts)
                budget.charge(len(attempts))

                if repaired is None:
                    if escalate:
                        logger.info(
                            "  repair exhausted — escalating to Claude (%d iters)",
                            max_claude_iters,
                        )
                        # Imported lazily: escalation pulls in httpx, an opt-in
                        # `reflecta[escalation]` dependency. The core run/clean/
                        # report path must not require it.
                        from reflecta.escalate import escalate_target

                        report.escalations_attempted += 1
                        repaired = escalate_target(
                            test,
                            result,
                            source,
                            repo_path=repo_path,
                            max_iters=max_claude_iters,
                            claude_client=claude_client,
                        )
                        if repaired is None:
                            target.status = TargetStatus.ESCALATED
                            iter_count += 1
                            stall += 1
                            logger.info("  escalation failed: target marked ESCALATED")
                            continue
                        logger.info("  escalation succeeded")
                        report.escalations_succeeded += 1
                    else:
                        target.status = TargetStatus.FAILED
                        iter_count += 1
                        stall += 1
                        logger.info(
                            "  failed: repair exhausted after %d attempt(s)",
                            len(attempts),
                        )
                        continue

                # repair succeeded — treat repaired test as the passing test
                logger.info("  repaired after %d attempt(s)", len(attempts))
                test = repaired

            coverage_after, suite_passed = measure_coverage_isolated(repo_path)

            # H2: a test can pass alone yet break the suite (fixture/ordering/
            # state collisions). Never keep a test that turns a green suite red.
            # If the baseline suite was already red we don't blame the
            # candidate — it already passed in isolation, so fall through to the
            # delta gate.
            if baseline_suite_passed and not suite_passed:
                test.test_file_path.unlink(missing_ok=True)
                test.target.status = TargetStatus.DISCARDED
                report.tests_discarded += 1
                stall += 1
                iter_count += 1
                logger.info("  discarded: test passes alone but breaks the full suite")
                continue

            outcome = process_test(
                test, coverage_before=coverage_before, coverage_after=coverage_after
            )
            if outcome == "kept":
                logger.info(
                    "  kept %s (coverage %.2f -> %.2f)",
                    test.test_file_path.name,
                    coverage_before,
                    coverage_after,
                )
                coverage_before = coverage_after
                report.tests_kept += 1
                stall = 0
            else:
                logger.info("  discarded: coverage did not rise (%.2f)", coverage_after)
                report.tests_discarded += 1
                stall += 1
        except BudgetExhausted:
            # Free tier is exhausted (429 ceiling). Stop cleanly so the report
            # is still written.
            logger.warning(
                "provider budget exhausted on target %s", target.qualified_name
            )
            target.status = TargetStatus.FAILED
            report.stop_reason = "budget"
            break
        except Exception:
            # One bad target must not abort the whole run.
            logger.exception("target %s failed unexpectedly", target.qualified_name)
            target.status = TargetStatus.FAILED
            iter_count += 1
            stall += 1
            continue

        iter_count += 1
    else:
        report.stop_reason = "exhausted"

    report.coverage_after = coverage_before
    report.budget = f"{budget.used}/{max_llm_calls}"
    logger.info(
        "done: %s | kept=%d discarded=%d repairs=%d | coverage %.2f -> %.2f",
        report.stop_reason,
        report.tests_kept,
        report.tests_discarded,
        report.repair_attempts_used,
        report.coverage_before,
        report.coverage_after,
    )
    return report
