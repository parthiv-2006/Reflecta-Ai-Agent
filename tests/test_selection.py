"""
Tests for src/reflecta/selection.py — Task 2.

All tests use in-memory CoverageTarget fixtures; no filesystem access needed.
"""

from pathlib import Path

import pytest

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
