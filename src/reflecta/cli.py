import logging
import shutil
from pathlib import Path

import typer

from reflecta.config import load_dotenv, require_api_keys
from reflecta.loop import run_loop
from reflecta.report import read_report, write_report
from reflecta.ui import ReflectaUI

app = typer.Typer(help="reflecta — auto-generate coverage-raising pytest tests.")


@app.command()
def run(
    path: Path = typer.Option(..., help="Path to the repository to analyse."),
    max_iters: int = typer.Option(10, help="Maximum targets to attempt per run."),
    max_repairs: int = typer.Option(2, help="Maximum repair attempts per target."),
    max_llm_calls: int = typer.Option(
        50, help="Stop before exceeding this many LLM calls (free-tier budget)."
    ),
    target_coverage: float = typer.Option(
        None, help="Stop once total coverage reaches this percent."
    ),
    stall_k: int = typer.Option(
        3, help="Stop after this many consecutive targets that do not raise coverage."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log per-target decisions to stderr."
    ),
    escalate: bool = typer.Option(
        False, "--escalate", help="Escalate stuck targets to Claude Agent SDK after repair exhaustion."
    ),
    max_claude_iters: int = typer.Option(
        3, help="Maximum Claude tool-use iterations per escalated target."
    ),
) -> None:
    """Generate coverage-raising tests for the repository at PATH."""
    path = path.resolve()
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    load_dotenv()
    try:
        require_api_keys(escalate=escalate)
    except EnvironmentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    ui = ReflectaUI()
    ui.banner()
    report = run_loop(
        path,
        max_iters=max_iters,
        max_repairs=max_repairs,
        max_llm_calls=max_llm_calls,
        target_coverage=target_coverage,
        stall_k=stall_k,
        escalate=escalate,
        max_claude_iters=max_claude_iters,
        ui=ui,
    )
    report_path = path / "reflecta-report.json"
    write_report(report, report_path)
    ui.summary(report, report_path)


@app.command()
def clean(
    path: Path = typer.Option(..., help="Path to the repository to clean."),
) -> None:
    """Remove all reflecta-generated test files (tests/_reflecta/test_reflecta_*.py)."""
    reflecta_dir = path / "tests" / "_reflecta"
    removed = 0
    if reflecta_dir.exists():
        # Only generated tests — never the package __init__.py or anything else.
        for f in reflecta_dir.glob("test_reflecta_*.py"):
            f.unlink()
            removed += 1

    # Also remove the reflecta-owned coverage workspace.
    coverage_dir = path / ".reflecta"
    if coverage_dir.exists():
        shutil.rmtree(coverage_dir, ignore_errors=True)

    if removed == 0 and not coverage_dir.exists():
        typer.echo("Nothing to clean.")
        return
    typer.echo(f"Removed {removed} generated test file(s).")


@app.command()
def report(
    path: Path = typer.Option(..., help="Path to the repository."),
    last: bool = typer.Option(False, "--last", help="Print the last run report."),
) -> None:
    """Print the run report from the last reflecta run."""
    report_path = path / "reflecta-report.json"
    try:
        r = read_report(report_path)
    except FileNotFoundError:
        typer.echo(f"No report found at {report_path}", err=True)
        raise typer.Exit(code=1)
    ReflectaUI().summary(r, report_path)
