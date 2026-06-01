import ast
import json
import logging
import subprocess
import sys
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


def coverage_paths(repo_path: Path) -> tuple[Path, Path]:
    """Reflecta-owned coverage data-file and json paths inside ``repo_path``.

    Kept under ``.reflecta/`` so reflecta never clobbers the target repo's own
    ``.coverage`` / ``coverage.json``.
    """
    d = Path(repo_path).resolve() / COVERAGE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / ".coverage", d / "coverage.json"


def measure_coverage(repo_path: Path) -> float:
    """Run the full test suite under coverage and return percent_covered.

    Coverage data and the json report are written to ``.reflecta/`` via an
    explicit ``--data-file`` so the repo's own coverage artifacts are untouched.
    """
    repo_path = Path(repo_path).resolve()
    data_file, json_file = coverage_paths(repo_path)
    env = child_env()
    subprocess.run(
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
        cwd=repo_path,
        capture_output=True,
        env=env,
    )
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
        cwd=repo_path,
        capture_output=True,
        env=env,
    )
    if not json_file.exists():
        return 0.0
    data = json.loads(json_file.read_text(encoding="utf-8"))
    return data.get("totals", {}).get("percent_covered", 0.0)


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
    gemini_client=None,
    groq_client=None,
) -> RunReport:
    """Main orchestration loop.

    extract → select → generate → assertion gate → run → [repair] → delta gate
    → keep/discard. Repeats until one of the four SPEC stop conditions fires:
    target coverage reached, max_iters hit, coverage stalled across ``stall_k``
    consecutive targets, or the LLM budget is depleted.
    """
    repo_path = Path(repo_path).resolve()
    budget = BudgetTracker(max_llm_calls=max_llm_calls)

    coverage_before = measure_coverage(repo_path)

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
                    target.status = TargetStatus.FAILED
                    iter_count += 1
                    stall += 1
                    logger.info(
                        "  failed: repair exhausted after %d attempt(s)", len(attempts)
                    )
                    continue

                # repair succeeded — treat repaired test as the passing test
                logger.info("  repaired after %d attempt(s)", len(attempts))
                test = repaired

            coverage_after = measure_coverage(repo_path)
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
