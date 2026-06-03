import ast
import contextlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from reflecta.budget import BudgetTracker
from reflecta.coverage_report import extract_targets
from reflecta.gates import passes_assertion_gate, passes_delta_gate
from reflecta.generate import collect_existing_tests, generate_test
from reflecta.llm.groq import MODEL_FAST
from reflecta.llm.provider import BudgetExhausted
from reflecta.models import GeneratedTest, RepairResult, RunReport, RunResult, TargetStatus
from reflecta.repair import repair_test
from reflecta.runner import child_env, run_test_isolated
from reflecta.selection import select_next

if TYPE_CHECKING:
    from reflecta.ui import ReflectaUI

logger = logging.getLogger("reflecta")


COVERAGE_DIR = ".reflecta"


_SKIP_DIRS = frozenset(
    {
        "tests",
        "test",
        "_tests",
        "__pycache__",
        "node_modules",
        "venv",
        ".venv",
        "env",
        "dist",
        "build",
        ".tox",
        ".reflecta",
        "_reflecta",
    }
)


def _source_dirs(repo_path: Path) -> list[str]:
    """Return top-level directory names (relative to repo_path) that contain
    Python source files, skipping hidden dirs, generated dirs, and test dirs.

    Falls back to ["."] only when no subdirectory qualifies — callers must
    treat "." with caution because it can match hidden worktree paths.
    """
    found = []
    for child in sorted(repo_path.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in _SKIP_DIRS:
            continue
        if any(child.rglob("*.py")):
            found.append(child.name)
    # Also include root-level .py files (not the seed) as a plain "." source
    # only when there are no qualifying subdirs, to avoid scanning hidden dirs.
    if not found and any(repo_path.glob("*.py")):
        found.append(".")
    return found or ["."]


def coverage_paths(repo_path: Path) -> tuple[Path, Path]:
    """Reflecta-owned coverage data-file and json paths inside ``repo_path``.

    Kept under ``.reflecta/`` so reflecta never clobbers the target repo's own
    ``.coverage`` / ``coverage.json``.
    """
    d = Path(repo_path).resolve() / COVERAGE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / ".coverage", d / "coverage.json"


def measure_coverage(repo_path: Path, test_file: Path | None = None) -> float:
    """Run the test suite under coverage and return percent_covered.

    Coverage data and the json report are written to ``.reflecta/`` via an
    explicit ``--data-file`` so the repo's own coverage artifacts are untouched.

    When ``test_file`` is provided, runs coverage *only* on that test file and
    appends to the existing coverage data-file. Otherwise, runs a full suite baseline.
    """
    repo_path = Path(repo_path).resolve()
    data_file, json_file = coverage_paths(repo_path)
    env = child_env(repo_path)

    sources = _source_dirs(repo_path)
    source_flags = [f"--source={s}" for s in sources]

    if test_file is not None:
        # Incremental run: run coverage only on the new test file and append to the existing data_file.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--data-file={data_file}",
                *source_flags,
                "--append",
                "-m",
                "pytest",
                "--tb=no",
                "-q",
                str(test_file),
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
                "--ignore-errors",
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

    # Seed run: executes a no-op so coverage discovers all source files as 0%
    # covered even when the repo has zero tests.  The seed file must stay on
    # disk until after ``coverage json`` (coverage resolves line numbers from
    # it); it is omitted from the report and deleted in the finally block.
    seed_file = repo_path / "_reflecta_seed.py"
    seed_file.write_text("pass\n", encoding="utf-8")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--data-file={data_file}",
                *source_flags,
                str(seed_file),
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
                "run",
                f"--data-file={data_file}",
                *source_flags,
                "--append",
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
                f"--omit={seed_file.name}",
                "-o",
                str(json_file),
            ],
            cwd=repo_path,
            capture_output=True,
            env=env,
        )
    finally:
        seed_file.unlink(missing_ok=True)
    if not json_file.exists():
        return 0.0
    data = json.loads(json_file.read_text(encoding="utf-8"))
    return data.get("totals", {}).get("percent_covered", 0.0)


def _safe_measure_coverage(repo_path: Path, test_file: Path | None = None) -> float:
    """Invokes measure_coverage safely, handling test mocks that only take 1 arg."""
    import inspect
    try:
        sig = inspect.signature(measure_coverage)
        params = list(sig.parameters.values())
        accepts_test_file = any(p.name == "test_file" for p in params)
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        if accepts_test_file or accepts_kwargs:
            return measure_coverage(repo_path, test_file=test_file)

        accepts_var_positional = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
        if accepts_var_positional and len(params) == 1:
            return measure_coverage(repo_path, test_file)

        return measure_coverage(repo_path)
    except Exception:
        try:
            return measure_coverage(repo_path, test_file=test_file)
        except TypeError:
            return measure_coverage(repo_path)


def measure_coverage_real(repo_path: Path) -> tuple[float, bool]:
    """Baseline measurement, run in-place in the real tree.

    Returns (percent_covered, suite_passed). Running in-place so the resulting
    coverage.json carries real file paths that extract_targets can resolve.
    Coverage artifacts land under .reflecta/ so the repo's own artifacts are
    untouched.
    """
    repo_path = Path(repo_path).resolve()
    data_file, json_file = coverage_paths(repo_path)
    env = child_env(repo_path)
    sources = _source_dirs(repo_path)
    source_flags = [f"--source={s}" for s in sources]

    seed_file = repo_path / "_reflecta_seed.py"
    seed_file.write_text("pass\n", encoding="utf-8")
    try:
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"--data-file={data_file}",
             *source_flags, str(seed_file)],
            cwd=repo_path, capture_output=True, env=env,
        )
        proc = subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"--data-file={data_file}",
             *source_flags, "--append", "-m", "pytest", "--tb=no", "-q"],
            cwd=repo_path, capture_output=True, env=env,
        )
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", f"--data-file={data_file}",
             "--ignore-errors", f"--omit={seed_file.name}", "-o", str(json_file)],
            cwd=repo_path, capture_output=True, env=env,
        )
    finally:
        seed_file.unlink(missing_ok=True)
    passed = proc.returncode == 0
    if not json_file.exists():
        return 0.0, passed
    data = json.loads(json_file.read_text(encoding="utf-8"))
    return data.get("totals", {}).get("percent_covered", 0.0), passed


def measure_coverage_isolated(
    repo_path: Path, *, timeout_s: int = 300
) -> tuple[float, bool]:
    """Run the full suite in a disposable copy; return (percent_covered, passed).

    The generated test executes only against the throwaway copy so a destructive
    or hanging test cannot corrupt the real working tree. Returns (0.0, False)
    on timeout so a hung suite is never mistaken for a coverage gain.
    """
    repo_path = Path(repo_path).resolve()
    tmp_root = Path(tempfile.mkdtemp(prefix="reflecta_cov_"))
    try:
        tmp_repo = tmp_root / "repo"
        shutil.copytree(
            repo_path,
            tmp_repo,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".venv", "venv",
                ".reflecta", ".pytest_cache", "node_modules", "build", "dist", ".omc",
            ),
        )
        data_file = tmp_root / ".coverage"
        json_file = tmp_root / "coverage.json"
        env = child_env(tmp_repo)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "coverage", "run", f"--data-file={data_file}",
                 "-m", "pytest", "--tb=no", "-q"],
                cwd=tmp_repo, capture_output=True, env=env, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return 0.0, False
        passed = proc.returncode == 0
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", f"--data-file={data_file}",
             "--ignore-errors", "-o", str(json_file)],
            cwd=tmp_repo, capture_output=True, env=env, timeout=timeout_s,
        )
        if not json_file.exists():
            return 0.0, passed
        data = json.loads(json_file.read_text(encoding="utf-8"))
        return data.get("totals", {}).get("percent_covered", 0.0), passed
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
    ui: "ReflectaUI | None" = None,
) -> RunReport:
    """Main orchestration loop.

    extract → select → generate → assertion gate → run → [repair] → delta gate
    → keep/discard. Repeats until one of the four SPEC stop conditions fires:
    target coverage reached, max_iters hit, coverage stalled across ``stall_k``
    consecutive targets, or the LLM budget is depleted.
    """
    repo_path = Path(repo_path).resolve()
    budget = BudgetTracker(max_llm_calls=max_llm_calls)

    with (ui.spin("Measuring baseline coverage") if ui else contextlib.nullcontext()):
        coverage_before, baseline_suite_passed = measure_coverage_real(repo_path)

    _, coverage_json_path = coverage_paths(repo_path)
    if coverage_json_path.exists():
        coverage_json = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    else:
        coverage_json = {}

    if ui:
        files = coverage_json.get("files", {})
        n_lines = sum(
            v.get("summary", {}).get("missing_lines", 0) for v in files.values()
        )
        ui.print_baseline(coverage_before, len(files), n_lines)

    targets = extract_targets(coverage_json, repo_path)

    if ui:
        n_target_files = len({t.file_path for t in targets})
        ui.print_targets_found(len(targets), n_target_files)

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

    if ui:
        ui.print_loop_header(max_iters)

    iter_count = 0
    stall = 0  # consecutive targets that did not raise coverage

    try:
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
            if ui:
                ui.print_target_header(iter_count + 1, target)
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
                with (ui.spin("Generate") if ui else contextlib.nullcontext()):
                    test = generate_test(
                        target,
                        source,
                        existing_tests,
                        repo_path=repo_path,
                        gemini_client=gemini_client,
                    )
                budget.charge(test.generation_calls)

                # Structurally unrunnable draft (empty, no test, missing import).
                # Repair cannot rescue these — it would feed garbage to Groq and
                # burn budget. Skip straight to SKIPPED and move on.
                if test.structural_error is not None:
                    if ui:
                        ui.step(
                            "Generate", ok=False, note=test.structural_error
                        )
                    test.test_file_path.unlink(missing_ok=True)
                    target.status = TargetStatus.SKIPPED
                    report.tests_skipped += 1
                    iter_count += 1
                    stall += 1
                    logger.info("  skipped: %s", test.structural_error)
                    continue

                if ui:
                    ui.step("Generate", ok=True)

                try:
                    ast.parse(test.source_code)
                except SyntaxError:
                    # Invalid Python from the LLM — treat as a run failure so the
                    # repair path gets a chance to fix it rather than silently
                    # discarding the target.
                    if ui:
                        ui.step("Run", ok=False, note="syntax error in generated code")
                    result = RunResult(
                        passed=False,
                        traceback="SyntaxError: generated code is not valid Python",
                        duration=0.0,
                    )
                else:
                    if not passes_assertion_gate(test):
                        if ui:
                            ui.print_gate_failed()
                        test.test_file_path.unlink(missing_ok=True)
                        target.status = TargetStatus.DISCARDED
                        report.tests_discarded += 1
                        iter_count += 1
                        stall += 1
                        logger.info("  discarded: failed assertion gate")
                        continue
                    with (ui.spin("Run") if ui else contextlib.run_in_executor if False else contextlib.nullcontext()):
                        result = run_test_isolated(test.test_file_path, repo_path)
                    if ui:
                        ui.step("Run", ok=result.passed, note="passing" if result.passed else "failed")

                if not result.passed:
                    # Some failures can never be fixed by feeding a traceback to
                    # the repair model: no tests were collected (nothing to fix),
                    # or the target module imports a dependency that is missing
                    # from this environment. Skip them instead of burning budget.
                    if result.failure_kind in ("no_tests", "import_error"):
                        note = (
                            "no tests collected"
                            if result.failure_kind == "no_tests"
                            else "missing dependency in target environment"
                        )
                        test.test_file_path.unlink(missing_ok=True)
                        target.status = TargetStatus.SKIPPED
                        report.tests_skipped += 1
                        iter_count += 1
                        stall += 1
                        logger.info("  skipped: %s", note)
                        if ui:
                            ui.step("Skipped", ok=False, note=note)
                        continue

                    if budget.exhausted():
                        test.test_file_path.unlink(missing_ok=True)
                        target.status = TargetStatus.FAILED
                        iter_count += 1
                        report.stop_reason = "budget"
                        break

                    try:
                        with (ui.spin("Repair") if ui else contextlib.nullcontext()):
                            repaired, attempts = repair_test(
                                test,
                                result,
                                source,
                                repo_path=repo_path,
                                max_repairs=max_repairs,
                                groq_client=groq_client,
                            )
                    except BudgetExhausted:
                        logger.warning(
                            "repair provider exhausted on %s; skipping target",
                            target.qualified_name,
                        )
                        if ui:
                            ui.print_repair_exhausted()
                        test.test_file_path.unlink(missing_ok=True)
                        target.status = TargetStatus.FAILED
                        iter_count += 1
                        stall += 1
                        continue

                    # Print per-attempt results now that we have the full list
                    if ui:
                        for att in attempts:
                            ok = att.result == RepairResult.PASS
                            model_label = "8B" if att.model_used == MODEL_FAST else "70B"
                            ui.step(
                                f"Repair {att.attempt_number}/{max_repairs}  ({model_label})",
                                ok=ok,
                                note="passing" if ok else "still failing",
                            )

                    report.repair_attempts_used += len(attempts)
                    budget.charge(len(attempts))

                    if repaired is None:
                        if escalate:
                            from reflecta.escalate import escalate_target  # lazy: opt-in dep
                            logger.info(
                                "  repair exhausted — escalating to Claude (%d iters)",
                                max_claude_iters,
                            )
                            if ui:
                                ui.print_escalating(max_claude_iters)
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
                                if ui:
                                    ui.step("Escalation", ok=False, note="Claude could not fix it")
                                continue
                            logger.info("  escalation succeeded")
                            if ui:
                                ui.step("Escalation", ok=True, note="Claude fixed it")
                            report.escalations_succeeded += 1
                        else:
                            target.status = TargetStatus.FAILED
                            iter_count += 1
                            stall += 1
                            logger.info(
                                "  failed: repair exhausted after %d attempt(s)", len(attempts)
                            )
                            if ui:
                                ui.print_repair_exhausted()
                            continue

                    # repair succeeded — treat repaired test as the passing test
                    logger.info("  repaired after %d attempt(s)", len(attempts))
                    test = repaired

                with (ui.spin("Measuring delta") if ui else contextlib.nullcontext()):
                    coverage_after, suite_passed = measure_coverage_isolated(repo_path)

                # H2: a test that passes in isolation but breaks the full suite
                # indicates a fixture/ordering/state collision. Discard it unless
                # the baseline suite was already red (then we cannot blame the candidate).
                if baseline_suite_passed and not suite_passed:
                    test.test_file_path.unlink(missing_ok=True)
                    test.target.status = TargetStatus.DISCARDED
                    report.tests_discarded += 1
                    stall += 1
                    iter_count += 1
                    logger.info("  discarded: test passes alone but breaks the full suite")
                    if ui:
                        ui.print_target_discarded(coverage_before, coverage_after)
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
                    if ui:
                        ui.print_target_kept(coverage_before, coverage_after)
                    coverage_before = coverage_after
                    report.tests_kept += 1
                    stall = 0
                else:
                    logger.info("  discarded: coverage did not rise (%.2f)", coverage_after)
                    if ui:
                        ui.print_target_discarded(coverage_before, coverage_after)
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
            except Exception as exc:
                # One bad target must not abort the whole run.
                logger.debug("target %s failed unexpectedly", target.qualified_name, exc_info=True)
                if ui:
                    ui.step("Error", ok=False, note=f"{type(exc).__name__}: {exc}")
                target.status = TargetStatus.FAILED
                iter_count += 1
                stall += 1
                continue

            iter_count += 1
        else:
            report.stop_reason = "exhausted"
    finally:
        pass

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
