"""HARDENING-0-9 §3.1 — measure_coverage must not clobber the repo's own
coverage artifacts.

AUDIT C1/H2 — measure_coverage_isolated runs the full suite in a disposable copy
(so a destructive generated test cannot touch the real tree) and reports the
suite pass/fail status alongside the coverage percent.
"""

import os

from reflecta.loop import (
    coverage_paths,
    measure_coverage,
    measure_coverage_isolated,
)


def _make_repo(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_mod.py").write_text(
        "from mod import f\n\ndef test_f():\n    assert f() == 1\n"
    )


def test_measure_coverage_does_not_touch_repo_coverage_files(tmp_path):
    _make_repo(tmp_path)
    # Pre-existing user artifacts that must survive.
    sentinel_json = tmp_path / "coverage.json"
    sentinel_json.write_text('{"user": "data"}')
    sentinel_data = tmp_path / ".coverage"
    sentinel_data.write_text("user-coverage-data")

    pct = measure_coverage(tmp_path)

    assert pct > 0.0
    assert sentinel_json.read_text() == '{"user": "data"}'
    assert sentinel_data.read_text() == "user-coverage-data"
    # reflecta wrote into its own workspace instead
    _, owned_json = coverage_paths(tmp_path)
    assert owned_json.exists()


def test_measure_coverage_works_with_relative_path(tmp_path, monkeypatch):
    """Regression: a relative --path must not double the cwd and report 0% / no
    targets (the nested examples/examples bug)."""
    _make_repo(tmp_path)
    # Run from the parent so the repo is reachable by a relative name.
    monkeypatch.chdir(tmp_path.parent)
    rel = tmp_path.name

    pct = measure_coverage(rel)

    assert pct > 0.0
    # No doubled directory created under the repo.
    assert not (tmp_path / rel).exists()
    # Artifact landed in the single, resolved workspace.
    _, owned_json = coverage_paths(tmp_path)
    assert owned_json.exists()
    assert os.path.isabs(str(owned_json))


def _make_repo_with_reflecta_test(tmp_path, body: str) -> None:
    """Repo whose only reflecta-generated test contains ``body`` (indented)."""
    (tmp_path / "mod.py").write_text("def f(x):\n    return x + 1\n")
    reflecta_dir = tmp_path / "tests" / "_reflecta"
    reflecta_dir.mkdir(parents=True)
    (reflecta_dir / "test_reflecta_mod_0.py").write_text(body)


def test_measure_coverage_isolated_protects_real_tree(tmp_path):
    """AUDIT C1: a destructive generated test executed during coverage
    measurement must not corrupt the real working tree. Before the fix this ran
    in-place and deleted the sentinel; the isolated copy makes it a no-op."""
    sentinel = tmp_path / "SECRET.txt"
    sentinel.write_text("real-working-tree-file")

    _make_repo_with_reflecta_test(
        tmp_path,
        "from pathlib import Path\n"
        "from mod import f\n"
        "def test_destructive():\n"
        "    (Path(__file__).resolve().parent.parent.parent / 'SECRET.txt').unlink(missing_ok=True)\n"
        "    assert f(2) == 3\n",
    )

    pct, passed = measure_coverage_isolated(tmp_path)

    assert sentinel.exists(), (
        "measure_coverage_isolated must not let a generated test delete real files"
    )
    assert passed is True
    assert pct > 0.0


def test_measure_coverage_isolated_reports_suite_failure(tmp_path):
    """AUDIT H2: a failing test is surfaced via passed=False so the loop can
    refuse to keep a test that does not actually pass."""
    _make_repo_with_reflecta_test(
        tmp_path,
        "from mod import f\ndef test_broken():\n    assert f(1) == 999\n",
    )

    _pct, passed = measure_coverage_isolated(tmp_path)

    assert passed is False
