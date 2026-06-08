"""
report.py — human-readable eval report formatter.

format_eval_report() produces a plain-text table showing metric name,
actual value, baseline reference, and pass/FAIL status for every
MetricResult in an EvalReport.  The final line is either "PASSED" or
"FAILED (N regressions)".
"""

from __future__ import annotations

from eval.metrics import EvalReport


def format_eval_report(report: EvalReport) -> str:
    """Return a formatted plain-text report for one fixture's eval run.

    Example output::

        Fixture: calc
        ┌──────────────────────────────┬──────────┬──────────┬────────┐
        │ Metric                       │  Actual  │ Baseline │ Status │
        ├──────────────────────────────┼──────────┼──────────┼────────┤
        │ coverage_delta               │   0.2500 │   0.1800 │  PASS  │
        │ tests_accepted               │   2.0000 │   2.0000 │  PASS  │
        │ tests_discarded              │   1.0000 │   2.0000 │  PASS  │
        ...
        └──────────────────────────────┴──────────┴──────────┴────────┘
        PASSED
    """
    lines: list[str] = []
    lines.append(f"Fixture: {report.fixture_name}")

    col_metric = 30
    col_actual = 10
    col_base = 10
    col_status = 8

    sep = (
        "┌"
        + "─" * (col_metric + 2)
        + "┬"
        + "─" * (col_actual + 2)
        + "┬"
        + "─" * (col_base + 2)
        + "┬"
        + "─" * (col_status + 2)
        + "┐"
    )
    mid = (
        "├"
        + "─" * (col_metric + 2)
        + "┼"
        + "─" * (col_actual + 2)
        + "┼"
        + "─" * (col_base + 2)
        + "┼"
        + "─" * (col_status + 2)
        + "┤"
    )
    bot = (
        "└"
        + "─" * (col_metric + 2)
        + "┴"
        + "─" * (col_actual + 2)
        + "┴"
        + "─" * (col_base + 2)
        + "┴"
        + "─" * (col_status + 2)
        + "┘"
    )

    lines.append(sep)
    header = (
        f"│ {'Metric':<{col_metric}} │ {'Actual':>{col_actual}} │"
        f" {'Baseline':>{col_base}} │ {'Status':^{col_status}} │"
    )
    lines.append(header)
    lines.append(mid)

    n_fail = 0
    for r in report.results:
        status_str = "PASS" if r.passed else "FAIL"
        if not r.passed:
            n_fail += 1

        actual_str = _fmt_num(r.actual)
        base_str = _fmt_num(r.baseline)
        name_str = r.name[:col_metric]

        row = (
            f"│ {name_str:<{col_metric}} │ {actual_str:>{col_actual}} │"
            f" {base_str:>{col_base}} │ {status_str:^{col_status}} │"
        )
        lines.append(row)

    lines.append(bot)

    if report.overall_passed:
        lines.append("PASSED")
    else:
        lines.append(f"FAILED ({n_fail} regression{'s' if n_fail != 1 else ''})")

    return "\n".join(lines)


def _fmt_num(v: float) -> str:
    """Format a number: integers without decimals, floats with 4 d.p."""
    if isinstance(v, float) and v == int(v) and 0 <= v < 1e9:
        return str(int(v))
    try:
        return f"{v:.4f}"
    except (TypeError, ValueError):
        return str(v)
