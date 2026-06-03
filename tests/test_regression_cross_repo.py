"""
Regression tests for the cross-repo failure that made reflecta fail on every
external codebase: Gemini returned truncated/empty test files that parsed but
could never run, and the loop spent its whole repair budget on them.

These lock in the fix: such drafts are rejected at generation and the target is
SKIPPED without ever entering the repair path.
"""

from pathlib import Path
from unittest.mock import patch

from reflecta.models import (
    CoverageTarget,
    GeneratedTest,
    RunResult,
    TargetStatus,
)

# The actual broken file reflecta wrote into leaseguard: a fragment that begins
# with a decorator and never imports `mock`.
LEASEGUARD_FRAGMENT = (
    "@mock.patch('datetime.datetime')\n"
    "def test_seed_clause_type_successful_run(mock_datetime):\n"
    "    # rest of the function remains the same\n"
    "    assert mock_datetime is not None\n"
)


def _target(tmp_path: Path) -> CoverageTarget:
    return CoverageTarget(
        file_path=tmp_path / "seed_decisions.py",
        qualified_name="seed_clause_type",
        missing_lines=[1, 2, 3],
        priority=3.0,
        status=TargetStatus.PENDING,
    )


def test_broken_draft_is_skipped_not_repaired(tmp_path):
    """A structurally broken draft must be SKIPPED without calling repair_test."""
    from reflecta.loop import run_loop

    target = _target(tmp_path)
    reflecta_dir = tmp_path / "tests" / "_reflecta"
    reflecta_dir.mkdir(parents=True)
    broken_path = reflecta_dir / "test_reflecta_seed_decisions_0.py"
    broken_path.write_text(LEASEGUARD_FRAGMENT)

    broken_gen = GeneratedTest(
        target=target,
        test_file_path=broken_path,
        source_code=LEASEGUARD_FRAGMENT,
        model_used="gemini-2.5-flash",
        generation_calls=2,
        structural_error="decorator references undefined name 'mock'",
    )

    repair_calls = {"n": 0}

    def fake_repair(*a, **k):
        repair_calls["n"] += 1
        return (None, [])

    with (
        patch("reflecta.loop.extract_targets", return_value=[target]),
        patch("reflecta.loop.generate_test", return_value=broken_gen),
        patch("reflecta.loop.repair_test", side_effect=fake_repair),
        patch("reflecta.loop.measure_coverage_real", return_value=(0.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(0.0, True)),
    ):
        report = run_loop(tmp_path, max_iters=5)

    assert repair_calls["n"] == 0, "broken draft must never reach repair"
    assert target.status == TargetStatus.SKIPPED
    assert report.tests_skipped == 1
    assert report.tests_kept == 0
    # The garbage file must be cleaned up, not left in the target repo.
    assert not broken_path.exists()


def test_missing_dependency_run_failure_is_skipped(tmp_path):
    """A valid test whose target import fails (ModuleNotFoundError) is an env
    problem → SKIPPED, not routed to repair."""
    from reflecta.loop import run_loop

    target = _target(tmp_path)
    reflecta_dir = tmp_path / "tests" / "_reflecta"
    reflecta_dir.mkdir(parents=True)
    test_path = reflecta_dir / "test_reflecta_seed_decisions_0.py"
    good_src = (
        "from seed_decisions import seed_clause_type\n\n"
        "def test_it():\n    assert seed_clause_type is not None\n"
    )
    test_path.write_text(good_src)

    gen = GeneratedTest(
        target=target,
        test_file_path=test_path,
        source_code=good_src,
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )

    repair_calls = {"n": 0}

    def fake_repair(*a, **k):
        repair_calls["n"] += 1
        return (None, [])

    with (
        patch("reflecta.loop.extract_targets", return_value=[target]),
        patch("reflecta.loop.generate_test", return_value=gen),
        patch("reflecta.loop.repair_test", side_effect=fake_repair),
        patch(
            "reflecta.loop.run_test_isolated",
            return_value=RunResult(
                passed=False,
                traceback="ModuleNotFoundError: No module named 'truststore'",
                duration=0.1,
                failure_kind="import_error",
            ),
        ),
        patch("reflecta.loop.measure_coverage_real", return_value=(0.0, True)),
        patch("reflecta.loop.measure_coverage_isolated", return_value=(0.0, True)),
    ):
        report = run_loop(tmp_path, max_iters=5)

    assert repair_calls["n"] == 0
    assert target.status == TargetStatus.SKIPPED
    assert report.tests_skipped == 1
