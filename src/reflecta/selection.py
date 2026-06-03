from reflecta.models import CoverageTarget, TargetStatus


def select_next(targets: list[CoverageTarget]) -> CoverageTarget | None:
    """Return the highest-priority pending target, or None if none remain.

    Ranking: non-entrypoints first (entrypoints like ``main`` are near-impossible
    to unit-test, so they go last), then descending by priority
    (len(missing_lines)), then ascending by qualified_name dot-count so top-level
    functions beat class methods on ties.
    """
    pending = [t for t in targets if t.status == TargetStatus.PENDING]
    if not pending:
        return None
    return min(
        pending,
        key=lambda t: (t.is_entrypoint, -t.priority, t.qualified_name.count(".")),
    )
