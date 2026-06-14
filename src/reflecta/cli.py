import logging
import shutil
from pathlib import Path
from typing import Optional

import typer

from reflecta.config import load_dotenv, require_credentials
from reflecta.loop import CoverageMeasurementError, run_loop
from reflecta.report import read_report, write_report
from reflecta.ui import ReflectaUI

app = typer.Typer(help="reflecta — auto-generate coverage-raising pytest tests.")

# Register the eval command group when the eval/ package is present.
try:
    from eval.cli import eval_app  # noqa: E402

    app.add_typer(eval_app, name="eval")
except ImportError:
    pass  # eval/ not installed — eval commands simply absent


@app.command()
def run(
    path: Path = typer.Option(..., help="Path to the repository to analyse."),
    max_iters: Optional[int] = typer.Option(
        None, help="Maximum targets to attempt per run. [default: 20]"
    ),
    max_repairs: Optional[int] = typer.Option(
        None, help="Maximum repair attempts per target. [default: 2]"
    ),
    max_llm_calls: Optional[int] = typer.Option(
        None,
        help=(
            "Free-tier budget: stop before exceeding this many Gemini/Groq calls. "
            "Claude escalation is a separate quota bounded by --max-claude-iters "
            "and is NOT counted here. [default: 50]"
        ),
    ),
    target_coverage: Optional[float] = typer.Option(
        None, help="Stop once total coverage reaches this percent."
    ),
    stall_k: Optional[int] = typer.Option(
        None,
        help=(
            "Stop after this many consecutive targets that do not raise coverage. "
            "[default: 7]"
        ),
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
    python: Optional[str] = typer.Option(
        None,
        "--python",
        help=(
            "Interpreter used to run generated tests (e.g. a target venv's "
            "python). Defaults to auto-detecting the repo's .venv/venv, then "
            "falling back to reflecta's own interpreter."
        ),
    ),
    skip_entrypoints: Optional[bool] = typer.Option(
        None,
        "--skip-entrypoints/--no-skip-entrypoints",
        help=(
            "Skip module entrypoints (main / functions under "
            "if __name__=='__main__'); they are not unit-testable and waste "
            "budget. Pass --no-skip-entrypoints to attempt them anyway. "
            "[default: skip]"
        ),
    ),
    attempt_risky: Optional[bool] = typer.Option(
        None,
        "--attempt-risky/--no-attempt-risky",
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
    cache_dir: Path = typer.Option(
        None,
        "--cache-dir",
        help=(
            "Override the LLM generation cache directory. Defaults to "
            "{repo}/.reflecta/gen_cache/. Pass a committed directory "
            "(e.g. eval/recordings/<fixture>/) to replay cached responses "
            "without spending quota."
        ),
    ),
    mutation: Optional[bool] = typer.Option(
        None,
        "--mutation/--no-mutation",
        help=(
            "Enable the mutation (honesty) gate: after a test raises coverage, "
            "plant single-operator mutants in its target and keep the test only "
            "if it kills enough of them (see --min-mutation-score). Catches "
            "coverage-padding tests that run the code but assert nothing real. "
            "No LLM quota, but adds subprocess runs per kept test."
        ),
    ),
    min_mutation_score: Optional[float] = typer.Option(
        None,
        "--min-mutation-score",
        help=(
            "Minimum fraction of mutants a kept test must kill (0.0–1.0). Only "
            "used with --mutation. A function with no mutable surface scores 1.0. "
            "[default: 0.5]"
        ),
    ),
    max_mutants: Optional[int] = typer.Option(
        None,
        "--max-mutants",
        help="Cap on mutants generated per target under --mutation. [default: 30]",
    ),
) -> None:
    """Generate coverage-raising tests for the repository at PATH.

    Reads defaults from ``reflecta.toml`` (``[tool.reflecta]``) so a project can
    pin its preferences once; explicit CLI flags always override the file.
    """
    from reflecta import settings as settings_mod

    path = path.resolve()
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s", force=True)
    else:
        # Suppress the default last-resort handler so raw tracebacks don't
        # bleed into user-facing output when a target fails unexpectedly.
        logging.basicConfig(level=logging.WARNING, format="%(message)s", force=True)
    load_dotenv()

    try:
        cfg = settings_mod.load_settings(path)
    except settings_mod.SettingsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    def r(cli_value, key, default):
        return settings_mod.resolve(cli_value, key, cfg, default)

    # Resolve every toml-overridable option once (CLI flag > reflecta.toml >
    # built-in default) so both the --dry-run and real paths see the same values.
    python_exe = r(python, "python", None)
    skip_entrypoints_v = r(skip_entrypoints, "skip_entrypoints", True)
    attempt_risky_v = r(attempt_risky, "attempt_risky", False)

    # --dry-run: static triage + preflight only. No LLM, so no credentials
    # required. Prints the plan and exits.
    if dry_run:
        from reflecta.loop import triage_repo

        ui = ReflectaUI()
        ui.banner()
        try:
            plan = triage_repo(
                path,
                python_exe=python_exe,
                skip_entrypoints=skip_entrypoints_v,
                attempt_risky=attempt_risky_v,
            )
        except (EnvironmentError, CoverageMeasurementError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        ui.print_triage(plan, attempt_risky=attempt_risky_v)
        return

    try:
        require_credentials(escalate=escalate)
    except EnvironmentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    ui = ReflectaUI()
    ui.banner()
    try:
        report = run_loop(
            path,
            max_iters=r(max_iters, "max_iters", 20),
            max_repairs=r(max_repairs, "max_repairs", 2),
            max_llm_calls=r(max_llm_calls, "max_llm_calls", 50),
            target_coverage=r(target_coverage, "target_coverage", None),
            stall_k=r(stall_k, "stall_k", 7),
            escalate=escalate,
            max_claude_iters=max_claude_iters,
            python_exe=python_exe,
            skip_entrypoints=skip_entrypoints_v,
            attempt_risky=attempt_risky_v,
            cache_dir=cache_dir,
            mutation=r(mutation, "mutation", False),
            min_mutation_score=r(min_mutation_score, "min_mutation_score", 0.5),
            max_mutants=r(max_mutants, "max_mutants", 30),
            ui=ui,
        )
    except (EnvironmentError, CoverageMeasurementError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    report_path = path / "reflecta-report.json"
    write_report(report, report_path)
    ui.summary(report, report_path)


@app.command()
def ci(
    path: Path = typer.Option(..., help="Path to the repository to analyse."),
    base: Optional[str] = typer.Option(
        None,
        "--base",
        help="Base branch for the PR (default: the remote's default branch).",
    ),
    head: Optional[str] = typer.Option(
        None,
        "--head",
        help="Branch reflecta pushes its tests to (default: reflecta/auto-tests).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Run the loop and print the pull request that WOULD be opened "
            "(branch, commit, title, body) without committing, pushing, or "
            "calling GitHub. No GITHUB_TOKEN required."
        ),
    ),
    max_iters: Optional[int] = typer.Option(None, help="Max targets to attempt."),
    target_coverage: Optional[float] = typer.Option(
        None, help="Stop once total coverage reaches this percent."
    ),
    mutation: Optional[bool] = typer.Option(
        None, "--mutation/--no-mutation", help="Enable the mutation (honesty) gate."
    ),
    min_mutation_score: Optional[float] = typer.Option(
        None, help="Min mutant-kill fraction a kept test must reach (with --mutation)."
    ),
    max_mutants: Optional[int] = typer.Option(
        None, help="Cap on mutants generated per target (with --mutation)."
    ),
    attempt_risky: Optional[bool] = typer.Option(
        None, "--attempt-risky/--no-attempt-risky", help="Also attempt risky targets."
    ),
    escalate: bool = typer.Option(
        False, "--escalate", help="Escalate stuck targets to Claude after repair."
    ),
    python: Optional[str] = typer.Option(
        None, "--python", help="Interpreter used to run generated tests."
    ),
    cache_dir: Optional[Path] = typer.Option(
        None, "--cache-dir", help="Override the LLM generation cache directory."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log per-target decisions to stderr."
    ),
) -> None:
    """Run reflecta and open a pull request containing the accepted tests.

    Reads defaults from ``reflecta.toml`` (``[tool.reflecta]``) so a CI workflow
    can stay to ``reflecta ci --path .``; explicit flags override the file. The
    loop runs exactly as ``reflecta run``; afterwards the KEPT tests are committed
    to ``--head`` and a PR into ``--base`` is opened (or updated if one is already
    open). Set ``GITHUB_TOKEN`` for the PR step (not needed for ``--dry-run``).
    """
    from reflecta import ci as ci_mod
    from reflecta import settings as settings_mod
    from reflecta.forge import ForgeError
    from reflecta.git_ops import GitError

    path = path.resolve()
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s", force=True)
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s", force=True)
    load_dotenv()

    try:
        cfg = settings_mod.load_settings(path)
    except settings_mod.SettingsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    def r(cli_value, key, default):
        return settings_mod.resolve(cli_value, key, cfg, default)

    # PR-step credentials are only needed on the real path; --dry-run skips them.
    try:
        require_credentials(escalate=escalate)
    except EnvironmentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    ui = ReflectaUI()
    ui.banner()
    try:
        report = run_loop(
            path,
            max_iters=r(max_iters, "max_iters", 20),
            max_repairs=r(None, "max_repairs", 2),
            max_llm_calls=r(None, "max_llm_calls", 50),
            target_coverage=r(target_coverage, "target_coverage", None),
            stall_k=r(None, "stall_k", 7),
            escalate=escalate,
            python_exe=r(python, "python", None),
            skip_entrypoints=r(None, "skip_entrypoints", True),
            attempt_risky=r(attempt_risky, "attempt_risky", False),
            cache_dir=cache_dir,
            mutation=r(mutation, "mutation", False),
            min_mutation_score=r(min_mutation_score, "min_mutation_score", 0.5),
            max_mutants=r(max_mutants, "max_mutants", 30),
            ui=ui,
        )
    except (EnvironmentError, CoverageMeasurementError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    report_path = path / "reflecta-report.json"
    write_report(report, report_path)
    ui.summary(report, report_path)

    head_branch = r(head, "head_branch", ci_mod.DEFAULT_HEAD_BRANCH)
    base_branch = r(base, "base_branch", None)
    try:
        result = ci_mod.submit(
            path,
            report,
            head_branch=head_branch,
            base_branch=base_branch,
            dry_run=dry_run,
        )
    except (ForgeError, GitError) as exc:
        # Both carry actionable, secret-free messages.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    if result.status == "dry_run":
        ui.print_ci_dry_run(result.plan)
    else:
        ui.print_ci_result(result)


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
    try:
        plan = triage_repo(
            path,
            python_exe=python,
            skip_entrypoints=skip_entrypoints,
            attempt_risky=attempt_risky,
        )
    except (EnvironmentError, CoverageMeasurementError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
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
