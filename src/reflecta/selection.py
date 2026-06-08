from reflecta.models import CoverageTarget, TargetStatus


def _size_bucket(t: CoverageTarget) -> int:
    n = len(t.missing_lines)
    if n <= 15:
        return 0
    if n <= 50:
        return 1
    return 2


def select_next(targets: list[CoverageTarget]) -> CoverageTarget | None:
    """Return the highest-priority pending target, or None if none remain.

    Ranking (primary to secondary):
      1. Non-entrypoints before entrypoints.
      2. testable < risky < blocked.
      3. Size bucket 0 (≤15 missing lines) before 1 (≤50) before 2 (51+).
         Smaller functions are more likely to be pure utilities that generate
         passing tests immediately; attempting them first maximises early
         coverage gains and avoids wasting repair budget on orchestrators.
      4. Within each bucket, descending by priority (most missing lines first).
      5. Top-level functions before class methods on ties.
    """
    pending = [t for t in targets if t.status == TargetStatus.PENDING]
    if not pending:
        return None
    _risk_rank = {"testable": 0, "risky": 1, "blocked": 2}
    return min(
        pending,
        key=lambda t: (
            t.is_entrypoint,
            _risk_rank.get(t.testability, 0),
            _size_bucket(t),
            -t.priority,
            t.qualified_name.count("."),
        ),
    )
