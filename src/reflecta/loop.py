import json
import subprocess
from datetime import datetime
from pathlib import Path

from reflecta.budget import BudgetTracker
from reflecta.coverage_report import extract_targets
from reflecta.gates import passes_assertion_gate, passes_delta_gate
from reflecta.generate import generate_test
from reflecta.models import GeneratedTest, RunReport, TargetStatus
from reflecta.repair import repair_test
from reflecta.runner import run_test
from reflecta.selection import select_next


def measure_coverage(repo_path: Path) -> float:
    """Run the full test suite under coverage and return percent_covered."""
    repo_path = Path(repo_path)
    subprocess.run(
        ["python", "-m", "coverage", "run", "-m", "pytest", "--tb=no", "-q"],
        cwd=repo_path,
        capture_output=True,
    )
    subprocess.run(
        ["python", "-m", "coverage", "json", "-o", "coverage.json"],
        cwd=repo_path,
        capture_output=True,
    )
    coverage_json_path = repo_path / "coverage.json"
    if not coverage_json_path.exists():
        return 0.0
    data = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    return data.get("totals", {}).get("percent_covered", 0.0)


def process_test(test: GeneratedTest, *, coverage_before: float, coverage_after: float) -> str:
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
    gemini_client=None,
    groq_client=None,
) -> RunReport:
    """Main orchestration loop.

    extract → select → generate → assertion gate → run → [repair] → delta gate
    → keep/discard. Repeats until all targets exhausted, max_iters reached,
    or the LLM budget is depleted.
    """
    repo_path = Path(repo_path)
    budget = BudgetTracker(max_llm_calls=max_llm_calls)

    coverage_before = measure_coverage(repo_path)

    coverage_json_path = repo_path / "coverage.json"
    if coverage_json_path.exists():
        coverage_json = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    else:
        coverage_json = {}
    targets = extract_targets(coverage_json, repo_path)

    report = RunReport(
        repo_path=repo_path,
        started_at=datetime.now(),
        coverage_before=coverage_before,
        coverage_after=coverage_before,
        targets=targets,
    )

    iter_count = 0
    while (target := select_next(targets)) is not None:
        if iter_count >= max_iters:
            report.stop_reason = "max_iters"
            break

        if budget.exhausted():
            report.stop_reason = "budget"
            break

        target.status = TargetStatus.GENERATING

        source = target.file_path.read_text(encoding="utf-8") if target.file_path.exists() else ""
        existing_tests = ""

        test = generate_test(
            target,
            source,
            existing_tests,
            repo_path=repo_path,
            gemini_client=gemini_client,
        )
        budget.charge(1)

        if not passes_assertion_gate(test):
            test.test_file_path.unlink(missing_ok=True)
            target.status = TargetStatus.DISCARDED
            report.tests_discarded += 1
            iter_count += 1
            continue

        result = run_test(test.test_file_path, repo_path)

        if not result.passed:
            if budget.exhausted():
                test.test_file_path.unlink(missing_ok=True)
                target.status = TargetStatus.FAILED
                iter_count += 1
                report.stop_reason = "budget"
                break

            repaired, attempts = repair_test(
                test,
                result,
                source,
                max_repairs=max_repairs,
                groq_client=groq_client,
            )
            report.repair_attempts_used += len(attempts)
            budget.charge(len(attempts))

            if repaired is None:
                target.status = TargetStatus.FAILED
                iter_count += 1
                continue

            # repair succeeded — treat repaired test as the passing test
            test = repaired

        coverage_after = measure_coverage(repo_path)
        outcome = process_test(test, coverage_before=coverage_before, coverage_after=coverage_after)
        if outcome == "kept":
            coverage_before = coverage_after
            report.tests_kept += 1
        else:
            report.tests_discarded += 1

        iter_count += 1
    else:
        report.stop_reason = "exhausted"

    report.coverage_after = coverage_before
    return report
