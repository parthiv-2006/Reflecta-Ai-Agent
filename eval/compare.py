"""
compare.py — tolerance-based metric comparison for the eval harness.

compare_to_baseline() walks a baseline spec dict and produces a MetricResult
for each entry, supporting four constraint shapes:

    { "exact": N }                 actual must equal N
    { "min": N }                   actual must be >= N
    { "max": N }                   actual must be <= N
    { "min": lo, "max": hi }       actual must be in [lo, hi]

Unknown metric names in the baseline (i.e. not present on EvalMetrics) raise
a clear AttributeError so stale baselines are caught at runtime.
"""

from __future__ import annotations

from eval.metrics import EvalMetrics, MetricResult


def _verdict(name: str, actual: float, spec: dict, passed: bool) -> str:
    """Build a one-line human-readable verdict string."""
    icon = "✓" if passed else "✗"
    if "exact" in spec:
        return f"{name}={actual} == {spec['exact']} {icon}"
    if "min" in spec and "max" in spec:
        return f"{name}={actual} in [{spec['min']}, {spec['max']}] {icon}"
    if "min" in spec:
        return f"{name}={actual} >= min {spec['min']} {icon}"
    if "max" in spec:
        return f"{name}={actual} <= max {spec['max']} {icon}"
    return f"{name}={actual} {icon}"


def compare_to_baseline(
    metrics: EvalMetrics, baseline: dict
) -> list[MetricResult]:
    """Compare metrics against a baseline spec dict.

    Parameters
    ----------
    metrics:
        The EvalMetrics collected from one fixture run.
    baseline:
        A dict mapping metric names → constraint dicts. Example::

            {
                "tests_accepted": {"min": 2},
                "llm_calls_gemini": {"min": 1, "max": 6},
                "targets_skipped_risky": {"exact": 0},
            }

    Returns
    -------
    list[MetricResult]
        One result per entry in ``baseline``, in iteration order.

    Raises
    ------
    AttributeError
        If a metric name in ``baseline`` is not a field of ``EvalMetrics``.
        This catches typos and stale baseline files early.
    """
    results: list[MetricResult] = []

    for metric_name, spec in baseline.items():
        # AttributeError propagates: stale/misspelt metric names are caught here.
        try:
            actual = getattr(metrics, metric_name)
        except AttributeError:
            raise AttributeError(
                f"Baseline references unknown metric '{metric_name}'. "
                f"Valid fields are: {sorted(metrics.__dataclass_fields__.keys())}"
            )

        if "exact" in spec:
            passed = actual == spec["exact"]
        elif "min" in spec and "max" in spec:
            passed = spec["min"] <= actual <= spec["max"]
        elif "min" in spec:
            passed = actual >= spec["min"]
        elif "max" in spec:
            passed = actual <= spec["max"]
        else:
            # Empty spec — vacuously passes; treat note-only entries as pass.
            passed = True

        # baseline reference value: prefer min for lower-bound checks, else max/exact.
        reference = spec.get("exact", spec.get("min", spec.get("max", float("nan"))))

        results.append(
            MetricResult(
                name=metric_name,
                actual=float(actual),
                baseline=float(reference),
                tolerance=0.0,
                passed=passed,
                message=_verdict(metric_name, actual, spec, passed),
            )
        )

    return results
