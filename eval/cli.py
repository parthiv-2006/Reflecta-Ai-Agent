"""
cli.py — typer command group for ``reflecta eval``.

Commands
--------
reflecta eval run [--fixture TEXT] [--verbose] [--python PATH]
    Run harness against one or all fixtures.  Exits 0 if all pass, 1 if any fail.

reflecta eval update-baseline [--fixture TEXT]
    Run live (real LLM calls) and write new baseline.json.  Prompts for
    confirmation before overwriting.

reflecta eval cache [--fixture TEXT]
    Run live and populate eval/recordings/ cache.  Subsequent runs are quota-free.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

eval_app = typer.Typer(help="Eval harness — benchmark reflecta against fixture repos.")

# Path to the eval/ directory (this file lives inside it).
_EVAL_DIR = Path(__file__).parent
_REPO_ROOT = _EVAL_DIR.parent
_BASELINES_PATH = _EVAL_DIR / "baselines" / "baseline.json"
_RECORDINGS_DIR = _EVAL_DIR / "recordings"
_FIXTURES_DIR = _EVAL_DIR / "fixtures"

# Load .env from the repo root so API keys are in os.environ before any
# subprocess is spawned.  The subprocess inherits the parent's env, so this
# is the correct place to ensure GEMINI_API_KEY / GROQ_API_KEY are set.
try:
    from reflecta.config import load_dotenv as _load_dotenv

    _load_dotenv(_REPO_ROOT)
except ImportError:
    pass  # reflecta not on path yet — will fail at command time with a clear message


def _load_baseline() -> dict:
    if not _BASELINES_PATH.exists():
        typer.echo(f"Baseline file not found: {_BASELINES_PATH}", err=True)
        raise typer.Exit(code=1)
    return json.loads(_BASELINES_PATH.read_text(encoding="utf-8"))


def _fixture_names() -> list[str]:
    if not _FIXTURES_DIR.exists():
        return []
    return sorted(p.name for p in _FIXTURES_DIR.iterdir() if p.is_dir())


@eval_app.command()
def run(
    fixture: str = typer.Option(
        None, "--fixture", help="Run a single fixture by name (default: all)."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show per-metric detail."),
    python: str = typer.Option(
        None, "--python", help="Override interpreter for fixture subprocess."
    ),
) -> None:
    """Run harness against one or all fixtures.  Exits 0 if all pass, 1 if any fail."""
    from eval.compare import compare_to_baseline
    from eval.metrics import EvalReport
    from eval.report import format_eval_report
    from eval.runner import run_fixture

    baseline_all = _load_baseline()
    fixtures_to_run = [fixture] if fixture else _fixture_names()

    if not fixtures_to_run:
        typer.echo("No fixtures found under eval/fixtures/.", err=True)
        raise typer.Exit(code=1)

    any_failed = False
    for name in fixtures_to_run:
        if name not in baseline_all:
            typer.echo(
                f"  [{name}] No baseline entry — skipping (run 'reflecta eval update-baseline')",
                err=True,
            )
            continue

        typer.echo(f"\nRunning fixture: {name} …")
        cache_dir = _RECORDINGS_DIR / name
        try:
            metrics = run_fixture(name, cache_dir=cache_dir, python=python, verbose=verbose)
        except Exception as exc:
            typer.echo(f"  [{name}] ERROR: {exc}", err=True)
            any_failed = True
            continue

        results = compare_to_baseline(metrics, baseline_all[name])
        overall = all(r.passed for r in results)
        report = EvalReport(
            fixture_name=name,
            metrics=metrics,
            results=results,
            overall_passed=overall,
        )
        typer.echo(format_eval_report(report))
        if not overall:
            any_failed = True

    raise typer.Exit(code=1 if any_failed else 0)


@eval_app.command(name="update-baseline")
def update_baseline(
    fixture: str = typer.Option(
        None, "--fixture", help="Update baseline for a single fixture only."
    ),
) -> None:
    """Run live and write new baseline.json.  Prompts before overwriting."""
    from eval.runner import run_fixture

    fixtures_to_run = [fixture] if fixture else _fixture_names()
    if not fixtures_to_run:
        typer.echo("No fixtures found.", err=True)
        raise typer.Exit(code=1)

    typer.echo(
        "This will run reflecta with REAL LLM calls (quota will be spent) "
        "and overwrite eval/baselines/baseline.json."
    )
    confirmed = typer.confirm("Proceed?", default=False)
    if not confirmed:
        typer.echo("Aborted.")
        raise typer.Exit(code=0)

    # Load existing baseline (or start empty)
    existing: dict = {}
    if _BASELINES_PATH.exists():
        existing = json.loads(_BASELINES_PATH.read_text(encoding="utf-8"))

    for name in fixtures_to_run:
        typer.echo(f"\nCapturing live metrics for: {name} …")
        try:
            metrics = run_fixture(name, verbose=True)
        except Exception as exc:
            typer.echo(f"  [{name}] ERROR: {exc}", err=True)
            continue

        # Build a loose baseline from the captured metrics.
        entry: dict = {}
        if metrics.coverage_delta > 0:
            entry["coverage_delta"] = {"min": round(metrics.coverage_delta * 0.8, 4)}
        if metrics.tests_accepted > 0:
            entry["tests_accepted"] = {"min": max(1, metrics.tests_accepted - 1)}
        entry["tests_discarded"] = {"max": metrics.tests_discarded + 1}
        entry["repair_attempts_used"] = {"max": metrics.repair_attempts_used + 2}
        if metrics.llm_calls_gemini > 0:
            entry["llm_calls_gemini"] = {
                "min": 1,
                "max": metrics.llm_calls_gemini + 3,
            }
        entry["targets_skipped_blocked"] = {"exact": metrics.targets_skipped_blocked}
        entry["targets_skipped_risky"] = {
            "exact" if metrics.targets_skipped_risky == 0 else "min": (
                metrics.targets_skipped_risky
            )
        }
        existing[name] = entry
        typer.echo(f"  [{name}] captured: {metrics}")

    _BASELINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BASELINES_PATH.write_text(
        json.dumps(existing, indent=2), encoding="utf-8"
    )
    typer.echo(f"\nBaseline written to {_BASELINES_PATH}")


@eval_app.command()
def cache(
    fixture: str = typer.Option(
        None, "--fixture", help="Cache a single fixture only."
    ),
) -> None:
    """Run live and populate eval/recordings/ cache.  Subsequent runs are quota-free."""
    from eval.runner import run_fixture

    fixtures_to_run = [fixture] if fixture else _fixture_names()
    if not fixtures_to_run:
        typer.echo("No fixtures found.", err=True)
        raise typer.Exit(code=1)

    typer.echo(
        "This will run reflecta with REAL LLM calls to warm up the cache.\n"
        "Commit eval/recordings/ afterwards so CI runs are quota-free."
    )

    for name in fixtures_to_run:
        cache_dir = _RECORDINGS_DIR / name
        cache_dir.mkdir(parents=True, exist_ok=True)
        typer.echo(f"\nWarming cache for: {name} …")
        try:
            metrics = run_fixture(name, cache_dir=cache_dir, verbose=True)
            typer.echo(f"  [{name}] done — {metrics.stop_reason}, {metrics.tests_accepted} accepted")
        except Exception as exc:
            typer.echo(f"  [{name}] ERROR: {exc}", err=True)
