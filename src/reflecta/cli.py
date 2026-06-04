import logging
import shutil
from pathlib import Path

import typer

from reflecta.config import load_dotenv, require_credentials
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
        50,
        help=(
            "Free-tier budget: stop before exceeding this many Gemini/Groq calls. "
            "Claude escalation is a separate quota bounded by --max-claude-iters "
            "and is NOT counted here."
        ),
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
        False,
        "--escalate",
        help="Escalate stuck targets to Claude Agent SDK after repair exhaustion.",
    ),
    max_claude_iters: int = typer.Option(
        3, help="Maximum Claude tool-use iterations per escalated target."
    ),
    python: str = typer.Option(
        None,
        "--python",
        help=(
            "Interpreter used to run generated tests (e.g. a target venv's "
            "python). Defaults to auto-detecting the repo's .venv/venv, then "
            "falling back to reflecta's own interpreter."
        ),
    ),
    skip_entrypoints: bool = typer.Option(
        True,
        "--skip-entrypoints/--no-skip-entrypoints",
        help=(
            "Skip module entrypoints (main / functions under "
            "if __name__=='__main__'); they are not unit-testable and waste "
            "budget. Pass --no-skip-entrypoints to attempt them anyway."
        ),
    ),
    attempt_risky: bool = typer.Option(
        False,
        "--attempt-risky",
        help=(
            "Also attempt 'risky' targets (functions that directly do "
            "network/DB/browser/subprocess I/O). Off by default to save LLM "
            "quota, since the free models rarely repair these."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Preview what would be attempted vs skipped (static triage + "
            "preflight) WITHOUT calling any LLM. No tests are generated."
        ),
    ),
) -> None:
    """Generate coverage-raising tests for the repository at PATH."""
    path = path.resolve()
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s", force=True)
    else:
        # Suppress the default last-resort handler so raw tracebacks don't
        # bleed into user-facing output when a target fails unexpectedly.
        logging.basicConfig(level=logging.WARNING, format="%(message)s", force=True)
    load_dotenv()

    # --dry-run: static triage + preflight only. No LLM, so no credentials
    # required. Prints the plan and exits.
    if dry_run:
        from reflecta.loop import triage_repo

        ui = ReflectaUI()
        ui.banner()
        plan = triage_repo(
            path,
            python_exe=python,
            skip_entrypoints=skip_entrypoints,
            attempt_risky=attempt_risky,
        )
        ui.print_triage(plan, attempt_risky=attempt_risky)
        return

    try:
        require_credentials(escalate=escalate)
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
        python_exe=python,
        skip_entrypoints=skip_entrypoints,
        attempt_risky=attempt_risky,
        ui=ui,
    )
    report_path = path / "reflecta-report.json"
    write_report(report, report_path)
    ui.summary(report, report_path)


@app.command()
def triage(
    path: Path = typer.Option(..., help="Path to the repository to analyse."),
    attempt_risky: bool = typer.Option(
        False,
        "--attempt-risky",
        help="Count risky (network/DB/IO) targets as attemptable in the preview.",
    ),
    skip_entrypoints: bool = typer.Option(
        True,
        "--skip-entrypoints/--no-skip-entrypoints",
        help="Treat module entrypoints as skipped in the preview.",
    ),
    python: str = typer.Option(
        None, "--python", help="Interpreter to plan against (defaults to auto-detect)."
    ),
) -> None:
    """Preview what reflecta would attempt vs skip — WITHOUT calling any LLM.

    Runs the repo's own test suite under coverage, statically classifies every
    target's testability, and preflights imports. No tests are generated and no
    provider quota is spent. Use this to check whether a repo/file is a good fit
    before running for real.
    """
    from reflecta.loop import triage_repo

    path = path.resolve()
    logging.basicConfig(level=logging.WARNING, format="%(message)s", force=True)
    ui = ReflectaUI()
    ui.banner()
    plan = triage_repo(
        path,
        python_exe=python,
        skip_entrypoints=skip_entrypoints,
        attempt_risky=attempt_risky,
    )
    ui.print_triage(plan, attempt_risky=attempt_risky)


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

    # Also remove the reflecta-owned coverage workspace. Capture whether it
    # existed *before* removing it — checking .exists() afterwards is always
    # False and would misreport a workspace-only clean.
    coverage_dir = path / ".reflecta"
    workspace_removed = coverage_dir.exists()
    if workspace_removed:
        shutil.rmtree(coverage_dir, ignore_errors=True)

    if removed == 0 and not workspace_removed:
        typer.echo("Nothing to clean.")
        return

    parts = [f"Removed {removed} generated test file(s)."]
    if workspace_removed:
        parts.append("Removed .reflecta/ workspace.")
    typer.echo(" ".join(parts))


@app.command()
def report(
    path: Path = typer.Option(..., help="Path to the repository."),
    last: bool = typer.Option(
        False, "--last", help="Reprint the most recent run report (see SPEC)."
    ),
) -> None:
    """Print the run report from the last reflecta run.

    Pass ``--last`` to reprint the most recent report. Without it we show a
    hint rather than guessing, so the flag drives real behaviour instead of
    being decorative.
    """
    if not last:
        typer.echo("Pass --last to reprint the most recent run report.")
        return
    report_path = path / "reflecta-report.json"
    try:
        r = read_report(report_path)
    except FileNotFoundError:
        typer.echo(f"No report found at {report_path}", err=True)
        raise typer.Exit(code=1)
    ReflectaUI().summary(r, report_path)


@app.command()
def login(
    token: str = typer.Option(
        None, "--token", help="reflecta API token. Omit to be prompted (hidden)."
    ),
    proxy_url: str = typer.Option(
        None, help="Override the proxy URL (advanced; defaults to the baked-in one)."
    ),
) -> None:
    """Save a reflecta token so runs use the hosted proxy (no provider keys needed).

    Stores credentials in ``~/.reflecta/credentials`` (0600). Once logged in,
    ``reflecta run`` brokers all Gemini/Groq calls through the proxy on the
    operator's keys — your code never leaves your machine.
    """
    from reflecta.llm import remote

    if not token:
        token = typer.prompt("reflecta token", hide_input=True)
    token = token.strip()
    if not token:
        typer.echo("No token provided.", err=True)
        raise typer.Exit(code=1)
    path = remote.save_credentials(token, proxy_url=proxy_url)
    typer.echo(f"Logged in. Credentials saved to {path}")
    typer.echo(f"Proxy: {remote.get_proxy_url()}")


@app.command()
def logout() -> None:
    """Remove stored reflecta credentials."""
    from reflecta.llm import remote

    if remote.clear_credentials():
        typer.echo("Logged out.")
    else:
        typer.echo("Not logged in.")
