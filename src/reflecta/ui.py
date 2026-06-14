"""ui.py — Rich-based progress output for the Reflecta run loop.

All display logic lives here so loop.py stays free of formatting concerns.
Pass ``ui=None`` (the default) anywhere a ReflectaUI is expected to silence
all output — existing tests do this automatically.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Generator

from rich.console import Console
from rich.rule import Rule


_OK = "[green]✓[/]"
_FAIL = "[red]✗[/]"
_SKIP = "[yellow]—[/]"
_COL = 24  # fixed width for the step-label column


class ReflectaUI:
    """Structured, coloured progress output using Rich."""

    def __init__(self, quiet: bool = False) -> None:
        # On Windows, stdout defaults to cp1252 which can't encode box-drawing
        # chars (Rule ─, ✓, ✗, →). Reconfigure to UTF-8 so Rich output never
        # crashes. legacy_windows=False disables the Win32 legacy renderer.
        if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        self._c = Console(highlight=False, quiet=quiet, legacy_windows=False)
        self._max_iters: int = 0

    # ── top-level phases ────────────────────────────────────────────────────

    def banner(self) -> None:
        self._c.print()
        self._c.print(
            "[bold cyan]Reflecta[/]  "
            "[dim]auto-generate coverage-raising pytest tests[/]"
        )
        self._c.print(Rule(style="dim"))

    def print_baseline(self, pct: float, n_files: int, n_lines: int) -> None:
        self._c.print(
            f"  Baseline  [bold]{pct:.1f}%[/]"
            f"  [dim]·[/]  {n_files} file{'s' if n_files != 1 else ''}"
            f"  [dim]·[/]  {n_lines:,} uncovered line{'s' if n_lines != 1 else ''}"
        )

    def print_targets_found(self, n: int, n_files: int) -> None:
        self._c.print()
        if n == 0:
            self._c.print(
                "  [yellow]No coverage gaps found in named functions — nothing to do.[/]"
            )
        else:
            self._c.print(
                f"  Found [bold]{n}[/] target{'s' if n != 1 else ''}"
                f" across {n_files} file{'s' if n_files != 1 else ''}"
            )

    def print_preflight_warning(self, missing: list[str], interpreter: str) -> None:
        self._c.print()
        self._c.print(
            f"  [yellow]⚠ Missing dependencies[/] under [dim]{interpreter}[/]:"
            f"  [bold]{', '.join(missing)}[/]"
        )
        self._c.print(
            "  [dim]Install them in that environment, or pass "
            "--python <venv-python>. Tests importing these will be skipped.[/]"
        )

    def print_testability_summary(
        self, *, testable: int, risky: int, blocked: int, attempt_risky: bool
    ) -> None:
        bits = [f"[green]{testable}[/] testable"]
        if risky:
            verb = "attempting" if attempt_risky else "skipped"
            bits.append(f"{risky} risky ({verb})")
        if blocked:
            bits.append(f"{blocked} blocked")
        self._c.print("  [bold]Testability[/]  " + "  [dim]·[/]  ".join(bits))
        if blocked or risky:
            self._c.print(
                "  [dim]risky = direct network/DB/IO · blocked = needs creds/IO "
                "at import. Skipped before any LLM call (no quota used).[/]"
            )

    def print_no_testable_targets(self, targets) -> None:
        blocked = sum(1 for t in targets if t.testability == "blocked")
        risky = sum(1 for t in targets if t.testability == "risky")
        entry = sum(1 for t in targets if t.is_entrypoint)
        self._c.print()
        self._c.print(
            "  [yellow]No unit-testable targets — nothing sent to the LLM.[/]"
        )
        self._c.print(
            f"  [dim]{len(targets)} target(s): {blocked} blocked, {risky} risky, "
            f"{entry} entrypoint(s). These are dominated by network/DB/IO and "
            f"aren't unit-testable without heavy mocking.[/]"
        )
        self._c.print(
            "  [dim]reflecta is best on pure/logic functions. Try --attempt-risky "
            "to force the risky ones, or point it at a more unit-testable repo.[/]"
        )

    def print_triage(self, plan, *, attempt_risky: bool) -> None:
        """Render a no-LLM triage/dry-run report."""
        from reflecta.models import TargetStatus

        self._c.print()
        self._c.print(Rule(style="dim"))
        self._c.print("  [bold cyan]Triage[/] [dim](no LLM calls — preview only)[/]")
        self._c.print(
            f"  Baseline [bold]{plan.coverage_before:.1f}%[/]"
            f"  [dim]·[/]  interpreter [dim]{plan.interpreter}[/]"
        )
        if plan.missing_deps:
            self._c.print(
                f"  [yellow]Missing deps[/]: {', '.join(plan.missing_deps)} "
                f"[dim](install or use --python; affected targets will be skipped)[/]"
            )
        self._c.print(
            f"  [bold]{len(plan.attempt)}[/] would be attempted  [dim]·[/]  "
            f"[green]{plan.count('testable')}[/] testable  [dim]·[/]  "
            f"{plan.count('risky')} risky  [dim]·[/]  "
            f"{plan.count('blocked')} blocked  [dim]·[/]  "
            f"{plan.n_entrypoints} entrypoint(s)"
        )
        self._c.print(Rule(style="dim"))

        attempt = plan.attempt
        if attempt:
            self._c.print("  [bold green]Would attempt:[/]")
            for t in attempt[:40]:
                self._c.print(
                    f"    [green]✓[/] {t.file_path.name} [dim]::[/] {t.qualified_name}"
                    f"  [dim]({len(t.missing_lines)} lines)[/]"
                )
            if len(attempt) > 40:
                self._c.print(f"    [dim]… and {len(attempt) - 40} more[/]")

        skipped = [t for t in plan.targets if t.status == TargetStatus.SKIPPED]
        if skipped:
            self._c.print()
            self._c.print("  [bold yellow]Would skip (no quota spent):[/]")
            for t in skipped[:40]:
                if t.is_entrypoint:
                    why = "entrypoint (main/__main__)"
                else:
                    why = t.testability_reason or t.testability
                self._c.print(
                    f"    [yellow]–[/] {t.file_path.name} [dim]::[/] {t.qualified_name}"
                    f"  [dim]{why}[/]"
                )
            if len(skipped) > 40:
                self._c.print(f"    [dim]… and {len(skipped) - 40} more[/]")
        self._c.print()

    def print_entrypoints_skipped(self, n: int) -> None:
        self._c.print(
            f"  [dim]Skipped {n} entrypoint target{'s' if n != 1 else ''} "
            f"(main / __main__ — not unit-testable). Use --no-skip-entrypoints "
            f"to attempt them.[/]"
        )

    def print_loop_header(self, max_iters: int) -> None:
        self._max_iters = max_iters
        self._c.print()
        self._c.print(
            f"  [bold]Running[/]  [dim][max {max_iters} iteration{'s' if max_iters != 1 else ''}][/]"
        )
        self._c.print(Rule(style="dim"))

    # ── per-target ───────────────────────────────────────────────────────────

    def print_target_header(self, i: int, target) -> None:
        n = len(target.missing_lines)
        self._c.print()
        self._c.print(
            f"  [bold cyan][{i}/{self._max_iters}][/]"
            f"  [bold]{target.file_path.name}[/]"
            f"  [dim]::[/]  {target.qualified_name}"
            f"  [dim]({n} line{'s' if n != 1 else ''})[/]"
        )

    @contextlib.contextmanager
    def spin(self, label: str) -> Generator[None, None, None]:
        """Show a spinner with *label* while the body executes."""
        with self._c.status(f"        [dim]{label}…[/]"):
            yield

    def step(self, label: str, ok: bool, note: str = "") -> None:
        """Print a single fixed-width result line."""
        icon = _OK if ok else _FAIL
        note_part = f"  [dim]{note}[/]" if note else ""
        self._c.print(f"        [dim]{label:<{_COL}}[/] {icon}{note_part}")

    def print_gate_failed(self) -> None:
        self._c.print(
            f"        [dim]{'Assert gate':<{_COL}}[/] {_FAIL}"
            "  [dim]no real assertions — discarded[/]"
        )

    def print_repair_exhausted(self) -> None:
        self._c.print(
            "        [yellow]Repair attempts exhausted[/] — [dim]FAILED "
            "(test ran but couldn't be fixed in the allotted attempts)[/]"
        )

    def print_budget_exhausted(self, detail: str, *, stage: str = "") -> None:
        """Explain a 429/quota stop in plain English with a remedy.

        ``detail`` is the BudgetExhausted message (provider + raw API text +
        per-minute-vs-daily hint); ``stage`` is "generation" or "repair".
        """
        where = f" during {stage}" if stage else ""
        self._c.print()
        self._c.print(f"  [red]✗ LLM quota / rate limit hit{where}[/]")
        self._c.print(f"  [dim]{detail}[/]")
        self._c.print(
            "  [dim]Nothing is broken in reflecta — this is the provider's free-tier "
            "limit. Re-run later (smaller --max-iters), or use --python / a paid key.[/]"
        )

    def print_escalating(self, max_iters: int) -> None:
        self._c.print(
            f"        [dim]{'Escalating':<{_COL}}[/]"
            f" [dim]Claude Sonnet ({max_iters} iter{'s' if max_iters != 1 else ''} max)…[/]"
        )

    def print_mutation_passed(self, result) -> None:
        self._c.print(
            f"        [dim]{'Mutation gate':<{_COL}}[/] {_OK}"
            f"  [green]killed {result.killed}/{result.total}"
            f"  ({result.score * 100:.0f}%)[/]"
        )

    def print_mutation_failed(self, result, min_score: float) -> None:
        self._c.print(
            f"        [dim]{'Mutation gate':<{_COL}}[/] {_FAIL}"
            f"  [dim]killed {result.killed}/{result.total} "
            f"({result.score * 100:.0f}%) < {min_score * 100:.0f}% — DISCARDED[/]"
        )

    def print_target_kept(self, before: float, after: float) -> None:
        delta = after - before
        self._c.print(
            f"        [dim]{'Coverage':<{_COL}}[/]"
            f" {before:.1f}% [dim]→[/] {after:.1f}%"
            f"  [green]+{delta:.1f} pp  KEPT ✓[/]"
        )

    def print_target_discarded(self, before: float, after: float) -> None:
        self._c.print(
            f"        [dim]{'Coverage':<{_COL}}[/]"
            f" {before:.1f}% [dim]→[/] {after:.1f}%"
            f"  [dim]no delta — DISCARDED[/]"
        )

    # ── final summary ────────────────────────────────────────────────────────

    def summary(self, report, report_path: Path) -> None:
        delta = report.coverage_after - report.coverage_before
        sign = "+" if delta >= 0 else ""
        colour = "green" if delta > 0 else "dim"
        self._c.print()
        self._c.print(Rule(style="dim"))
        self._c.print(
            f"  [bold]Coverage[/]     "
            f"{report.coverage_before:.1f}% [dim]→[/] [bold]{report.coverage_after:.1f}%[/]"
            f"  [{colour}]{sign}{delta:.1f} pp[/]"
        )
        skipped_part = (
            f"  [dim]·[/]  skipped {report.tests_skipped}"
            if getattr(report, "tests_skipped", 0)
            else ""
        )
        self._c.print(
            f"  [bold]Tests[/]        "
            f"kept [green]{report.tests_kept}[/]"
            f"  [dim]·[/]  discarded {report.tests_discarded}"
            f"{skipped_part}"
            f"  [dim]·[/]  repairs {report.repair_attempts_used}"
        )
        if getattr(report, "mutants_total", 0) or getattr(
            report, "tests_failed_mutation", 0
        ):
            killed = report.mutants_killed
            total = report.mutants_total
            pct = f"{killed / total * 100:.0f}%" if total else "n/a"
            self._c.print(
                f"  [bold]Mutation[/]     "
                f"killed [green]{killed}/{total}[/] ({pct})"
                f"  [dim]·[/]  passed gate {report.tests_mutation_tested}"
                f"  [dim]·[/]  failed gate {report.tests_failed_mutation}"
            )
        if report.escalations_attempted:
            self._c.print(
                f"  [bold]Escalations[/]  "
                f"attempted {report.escalations_attempted}"
                f"  [dim]·[/]  succeeded [green]{report.escalations_succeeded}[/]"
            )
        stop_explain = {
            "budget": "budget — LLM quota / rate limit hit (free tier). Wait and re-run.",
            "max_iters": "max_iters — hit the per-run target cap (raise --max-iters for more).",
            "stalled": "stalled — several targets in a row did not raise coverage.",
            "target_reached": "target_reached — requested coverage met.",
            "no_targets": "no_targets — no coverage gaps found in named functions.",
            "no_testable_targets": (
                "no_testable_targets — every gap is in network/DB/IO code that "
                "isn't unit-testable. Nothing sent to the LLM (no quota used)."
            ),
            "exhausted": "exhausted — all targets processed.",
        }
        self._c.print(
            f"  [bold]Stop reason[/]  "
            f"{stop_explain.get(report.stop_reason, report.stop_reason)}"
        )
        self._c.print(f"  [bold]Report[/]       [dim]{report_path}[/]")
        self._c.print()

    # ── ci (PR) output ────────────────────────────────────────────────────────

    def print_ci_dry_run(self, plan) -> None:
        self._c.print()
        self._c.print(Rule(style="dim"))
        self._c.print(
            "  [bold cyan]CI dry-run[/] [dim](nothing committed, pushed, or opened)[/]"
        )
        self._c.print(
            f"  Branch  [bold]{plan.head_branch}[/]  [dim]→ base[/] {plan.base_branch}"
        )
        self._c.print(f"  Commit  [dim]{plan.commit_message}[/]")
        self._c.print(f"  PR      [bold]{plan.pr_title}[/]")
        self._c.print(
            f"  Files   {len(plan.test_files)} generated test file"
            f"{'s' if len(plan.test_files) != 1 else ''}"
        )
        self._c.print(Rule(style="dim"))
        self._c.print(plan.pr_body)
        self._c.print()

    def print_ci_result(self, result) -> None:
        self._c.print()
        if result.status == "no_tests":
            self._c.print("  [yellow]No tests were kept — no pull request opened.[/]")
        elif result.status == "opened":
            self._c.print(f"  [green]✓ Opened pull request[/]  [dim]{result.pr.url}[/]")
        elif result.status == "updated":
            self._c.print(
                f"  [green]✓ Updated existing pull request[/]  [dim]{result.pr.url}[/]"
            )
        self._c.print()
