import ast
import contextlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
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


def _missing_module_name(traceback: str) -> str | None:
    """Pull the missing module name out of a ModuleNotFoundError/ImportError
    traceback so the loop can name the exact dependency that needs installing."""
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", traceback)
    if m:
        return m.group(1)
    m = re.search(r"cannot import name ['\"]([^'\"]+)['\"]", traceback)
    return m.group(1) if m else None


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


def measure_coverage(
    repo_path: Path, test_file: Path | None = None, python_exe: str | None = None
) -> float:
    """Run the test suite under coverage and return percent_covered.

    Coverage data and the json report are written to ``.reflecta/`` via an
    explicit ``--data-file`` so the repo's own coverage artifacts are untouched.

    When ``test_file`` is provided, runs coverage *only* on that test file and
    appends to the existing coverage data-file. Otherwise, runs a full suite baseline.
    """
    from reflecta.environment import detect_interpreter

    repo_path = Path(repo_path).resolve()
    data_file, json_file = coverage_paths(repo_path)
    env = child_env(repo_path)
    sys_executable = python_exe or detect_interpreter(repo_path)

    sources = _source_dirs(repo_path)
    source_flags = [f"--source={s}" for s in sources]

    if test_file is not None:
        # Incremental run: run coverage only on the new test file and append to the existing data_file.
        subprocess.run(
            [
                sys_executable,
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
                sys_executable,
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
                sys_executable,
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
                sys_executable,
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
                sys_executable,
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


def measure_coverage_real(
    repo_path: Path, python_exe: str | None = None
) -> tuple[float, bool]:
    """Baseline measurement, run in-place in the real tree.

    Returns (percent_covered, suite_passed). Running in-place so the resulting
    coverage.json carries real file paths that extract_targets can resolve.
    Coverage artifacts land under .reflecta/ so the repo's own artifacts are
    untouched.
    """
    from reflecta.environment import detect_interpreter

    repo_path = Path(repo_path).resolve()
    data_file, json_file = coverage_paths(repo_path)
    env = child_env(repo_path)
    sys_executable = python_exe or detect_interpreter(repo_path)
    sources = _source_dirs(repo_path)
    source_flags = [f"--source={s}" for s in sources]

    seed_file = repo_path / "_reflecta_seed.py"
    seed_file.write_text("pass\n", encoding="utf-8")
    try:
        subprocess.run(
            [sys_executable, "-m", "coverage", "run", f"--data-file={data_file}",
             *source_flags, str(seed_file)],
            cwd=repo_path, capture_output=True, env=env,
        )
        proc = subprocess.run(
            [sys_executable, "-m", "coverage", "run", f"--data-file={data_file}",
             *source_flags, "--append", "-m", "pytest", "--tb=no", "-q"],
            cwd=repo_path, capture_output=True, env=env,
        )
        subprocess.run(
            [sys_executable, "-m", "coverage", "json", f"--data-file={data_file}",
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
    repo_path: Path, *, timeout_s: int = 300, python_exe: str | None = None
) -> tuple[float, bool]:
    """Run the full suite in a disposable copy; return (percent_covered, passed).

    The generated test executes only against the throwaway copy so a destructive
    or hanging test cannot corrupt the real working tree. Returns (0.0, False)
    on timeout so a hung suite is never mistaken for a coverage gain.
    """
    from reflecta.environment import detect_interpreter

    repo_path = Path(repo_path).resolve()
    # Detect on the real repo before copying — the copy omits the virtualenv.
    sys_executable = python_exe or detect_interpreter(repo_path)
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
                [sys_executable, "-m", "coverage", "run", f"--data-file={data_file}",
                 "-m", "pytest", "--tb=no", "-q"],
                cwd=tmp_repo, capture_output=True, env=env, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return 0.0, False
        passed = proc.returncode == 0
        subprocess.run(
            [sys_executable, "-m", "coverage", "json", f"--data-file={data_file}",
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


@dataclass
class TriagePlan:
    """A no-LLM preview of what a run would attempt. Built by ``triage_repo``."""

    interpreter: str
    coverage_before: float
    targets: list  # list[CoverageTarget] with .status pre-marked
    missing_deps: list[str] = field(default_factory=list)

    @property
    def attempt(self) -> list:
        return [t for t in self.targets if t.status == TargetStatus.PENDING]

    def count(self, level: str) -> int:
        return sum(1 for t in self.targets if t.testability == level)

    @property
    def n_entrypoints(self) -> int:
        return sum(1 for t in self.targets if t.is_entrypoint)


def triage_repo(
    repo_path: Path,
    *,
    python_exe: str | None = None,
    skip_entrypoints: bool = True,
    attempt_risky: bool = False,
) -> TriagePlan:
    """Plan a run WITHOUT calling any LLM: measure baseline coverage, extract +
    statically classify targets, preflight imports, and mark which targets would
    be attempted vs skipped. Used by ``reflecta triage`` and ``run --dry-run``.

    Running coverage executes the repo's *own* test suite (no quota), never the
    generated tests, and never a provider.
    """
    from reflecta.environment import (
        collect_third_party_roots,
        detect_interpreter,
        preflight_imports,
    )

    repo_path = Path(repo_path).resolve()
    interpreter = python_exe or detect_interpreter(repo_path)
    coverage_before, _ = measure_coverage_real(repo_path, python_exe=interpreter)

    _, coverage_json_path = coverage_paths(repo_path)
    coverage_json = (
        json.loads(coverage_json_path.read_text(encoding="utf-8"))
        if coverage_json_path.exists()
        else {}
    )
    targets = extract_targets(coverage_json, repo_path)

    missing_deps: list[str] = []
    if targets:
        target_files = sorted({t.file_path for t in targets})
        missing_deps = preflight_imports(
            interpreter, collect_third_party_roots(target_files, repo_path)
        )

    # Mark what a run would skip — same rules as run_loop, but no mutation of any
    # report and no LLM calls.
    for t in targets:
        if t.is_entrypoint and skip_entrypoints:
            t.status = TargetStatus.SKIPPED
        elif t.testability == "blocked":
            t.status = TargetStatus.SKIPPED
        elif t.testability == "risky" and not attempt_risky:
            t.status = TargetStatus.SKIPPED
        else:
            t.status = TargetStatus.PENDING

    return TriagePlan(
        interpreter=interpreter,
        coverage_before=coverage_before,
        targets=targets,
        missing_deps=missing_deps,
    )


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
    python_exe: str | None = None,
    skip_entrypoints: bool = True,
    attempt_risky: bool = False,
    ui: "ReflectaUI | None" = None,
) -> RunReport:
    """Main orchestration loop.

    extract → select → generate → assertion gate → run → [repair] → delta gate
    → keep/discard. Repeats until one of the four SPEC stop conditions fires:
    target coverage reached, max_iters hit, coverage stalled across ``stall_k``
    consecutive targets, or the LLM budget is depleted.

    ``python_exe`` overrides the interpreter used to run generated tests; when
    None it is auto-detected from the target repo's virtualenv (falling back to
    Reflecta's own interpreter).
    """
    from reflecta.environment import (
        collect_third_party_roots,
        detect_interpreter,
        preflight_imports,
    )

    repo_path = Path(repo_path).resolve()
    budget = BudgetTracker(max_llm_calls=max_llm_calls)
    interpreter = python_exe or detect_interpreter(repo_path)

    with (ui.spin("Measuring baseline coverage") if ui else contextlib.nullcontext()):
        coverage_before, baseline_suite_passed = measure_coverage_real(
            repo_path, python_exe=interpreter
        )

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

    # Preflight: warn if the target's third-party imports are not resolvable
    # under the chosen interpreter. find_spec never executes module code, so
    # this is safe even for modules that do I/O at import. A missing dependency
    # here means every test importing that module would fail with
    # ModuleNotFoundError, so we surface it once, clearly, instead of letting it
    # silently sink every target.
    if targets:
        target_files = sorted({t.file_path for t in targets})
        missing = preflight_imports(
            interpreter, collect_third_party_roots(target_files, repo_path)
        )
        if missing:
            msg = (
                f"Missing dependencies under {interpreter}: "
                f"{', '.join(missing)}. Install them in that environment "
                f"(or pass --python <path-to-venv-python>); tests importing "
                f"these modules will be skipped."
            )
            logger.warning(msg)
            if ui:
                ui.print_preflight_warning(missing, interpreter)

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

    # Skip module entrypoints up front (default). They drive the whole program
    # from argv and cannot be unit-tested, so attempting them only wastes the
    # LLM budget that should go to ordinary functions.
    if skip_entrypoints:
        n_skipped = 0
        for t in targets:
            if t.is_entrypoint and t.status == TargetStatus.PENDING:
                t.status = TargetStatus.SKIPPED
                report.tests_skipped += 1
                n_skipped += 1
        if n_skipped:
            logger.info("skipped %d entrypoint target(s)", n_skipped)
            if ui:
                ui.print_entrypoints_skipped(n_skipped)

    # Static testability triage (no LLM, no execution): "blocked" targets live
    # in modules that can't even be imported in a test (live creds / I/O at
    # import); "risky" targets directly do network/DB/IO and are a poor quota
    # bet. Both are skipped before any provider call so quota goes to functions
    # that can actually yield a kept test.
    n_blocked = 0
    n_risky_skipped = 0
    for t in targets:
        if t.status != TargetStatus.PENDING:
            continue
        if t.testability == "blocked":
            t.status = TargetStatus.SKIPPED
            report.tests_skipped += 1
            n_blocked += 1
        elif t.testability == "risky" and not attempt_risky:
            t.status = TargetStatus.SKIPPED
            report.tests_skipped += 1
            n_risky_skipped += 1

    n_testable = sum(1 for t in targets if t.status == TargetStatus.PENDING)
    if ui:
        ui.print_testability_summary(
            testable=n_testable,
            risky=n_risky_skipped,
            blocked=n_blocked,
            attempt_risky=attempt_risky,
        )

    # Nothing attemptable → stop before spending a single LLM call.
    if n_testable == 0:
        report.stop_reason = "no_testable_targets"
        logger.info("no unit-testable targets; nothing sent to the LLM")
        if ui:
            ui.print_no_testable_targets(targets)
        return report

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
                        result = run_test_isolated(
                            test.test_file_path, repo_path, python_exe=interpreter
                        )
                    if ui:
                        ui.step("Run", ok=result.passed, note="passing" if result.passed else "failed")

                if not result.passed:
                    # Some failures can never be fixed by feeding a traceback to
                    # the repair model: no tests were collected (nothing to fix),
                    # or the target module imports a dependency that is missing
                    # from this environment. Skip them instead of burning budget.
                    if result.failure_kind in ("no_tests", "import_error"):
                        if result.failure_kind == "no_tests":
                            note = "no tests were collected from the generated file"
                        else:
                            missing_mod = _missing_module_name(result.traceback)
                            if missing_mod:
                                note = (
                                    f"target needs '{missing_mod}', which is not "
                                    f"installed under {interpreter}. Install it there "
                                    f"or pass --python <venv-python>."
                                )
                            else:
                                note = (
                                    f"a dependency is missing under {interpreter}; "
                                    f"install the target's deps or pass --python."
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
                                python_exe=interpreter,
                            )
                    except BudgetExhausted as exc:
                        logger.warning(
                            "repair provider exhausted on %s: %s",
                            target.qualified_name,
                            exc,
                        )
                        if ui:
                            ui.print_budget_exhausted(str(exc), stage="repair")
                        # A repair-stage rate limit means the next target would
                        # almost certainly hit it too. Stop cleanly (like the
                        # generation-stage handler) instead of failing target by
                        # target, so the report is written and the user can
                        # re-run once the limit resets.
                        test.test_file_path.unlink(missing_ok=True)
                        target.status = TargetStatus.FAILED
                        iter_count += 1
                        report.stop_reason = "budget"
                        break

                    # Print per-attempt results now that we have the full list
                    if ui:
                        for att in attempts:
                            ok = att.result == RepairResult.PASS
                            model_label = "8B" if att.model_used == MODEL_FAST else "70B"
                            if ok:
                                note = "passing"
                            elif att.traceback.startswith("request too large"):
                                note = "request too large for model TPM, even after trimming"
                            else:
                                note = "still failing"
                            ui.step(
                                f"Repair {att.attempt_number}/{max_repairs}  ({model_label})",
                                ok=ok,
                                note=note,
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
                    coverage_after, suite_passed = measure_coverage_isolated(
                        repo_path, python_exe=interpreter
                    )

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
            except BudgetExhausted as exc:
                # Free tier is exhausted (429 ceiling). Stop cleanly so the report
                # is still written.
                logger.warning(
                    "LLM quota/rate limit exhausted on target %s: %s",
                    target.qualified_name,
                    exc,
                )
                if ui:
                    ui.print_budget_exhausted(str(exc), stage="generation")
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
