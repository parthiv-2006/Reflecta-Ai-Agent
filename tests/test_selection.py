"""
Tests for src/reflecta/selection.py — Task 2.

All tests use in-memory CoverageTarget fixtures; no filesystem access needed.
"""

from pathlib import Path


from reflecta.models import CoverageTarget, TargetStatus
from reflecta.selection import select_next


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _target(
    name: str,
    missing: list[int],
    status: TargetStatus = TargetStatus.PENDING,
) -> CoverageTarget:
    return CoverageTarget(
        file_path=Path("fake.py"),
        qualified_name=name,
        missing_lines=missing,
        priority=float(len(missing)),
        status=status,
    )


# ---------------------------------------------------------------------------
# Case 1 — ordering: highest priority (most missing lines) returned first
# ---------------------------------------------------------------------------


def test_returns_highest_priority_first() -> None:
    targets = [
        _target("low", [1]),
        _target("high", [1, 2, 3]),
        _target("mid", [1, 2]),
    ]
    result = select_next(targets)
    assert result is not None
    assert result.qualified_name == "high"


# ---------------------------------------------------------------------------
# Case 2 — all non-pending → None
# ---------------------------------------------------------------------------


def test_all_non_pending_returns_none() -> None:
    targets = [
        _target("a", [1], TargetStatus.KEPT),
        _target("b", [2], TargetStatus.FAILED),
        _target("c", [3], TargetStatus.DISCARDED),
    ]
    assert select_next(targets) is None


# ---------------------------------------------------------------------------
# Case 3 — empty list → None
# ---------------------------------------------------------------------------


def test_empty_list_returns_none() -> None:
    assert select_next([]) is None


# ---------------------------------------------------------------------------
# Case 4 — mixed statuses: only pending are candidates
# ---------------------------------------------------------------------------


def test_only_pending_are_candidates() -> None:
    targets = [
        _target("non_pending_high", [1, 2, 3], TargetStatus.KEPT),
        _target("pending_low", [1], TargetStatus.PENDING),
    ]
    result = select_next(targets)
    assert result is not None
    assert result.qualified_name == "pending_low"


# ---------------------------------------------------------------------------
# Case 5 — tiebreak: simpler signature (fewer dots) wins
# ---------------------------------------------------------------------------


def test_tiebreak_simpler_signature_wins() -> None:
    targets = [
        _target("Cls.method", [1, 2]),
        _target("func", [1, 2]),
    ]
    result = select_next(targets)
    assert result is not None
    assert result.qualified_name == "func"


# ---------------------------------------------------------------------------
# Case 6 — entrypoints are deprioritized even with higher raw priority
# ---------------------------------------------------------------------------


def test_entrypoint_deprioritized_below_ordinary_target() -> None:
    main = _target("main", [1, 2, 3, 4, 5])
    main.is_entrypoint = True
    ordinary = _target("parse", [1])
    result = select_next([main, ordinary])
    assert result is not None
    assert result.qualified_name == "parse"


def test_entrypoint_selected_when_only_candidate() -> None:
    main = _target("main", [1, 2])
    main.is_entrypoint = True
    result = select_next([main])
    assert result is not None
    assert result.qualified_name == "main"
