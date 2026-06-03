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
            "        [yellow]Repair budget exhausted[/] — [dim]SKIPPED[/]"
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
            "exhausted": "exhausted — all targets processed.",
        }
        self._c.print(
            f"  [bold]Stop reason[/]  "
            f"{stop_explain.get(report.stop_reason, report.stop_reason)}"
        )
        self._c.print(f"  [bold]Report[/]       [dim]{report_path}[/]")
        self._c.print()
