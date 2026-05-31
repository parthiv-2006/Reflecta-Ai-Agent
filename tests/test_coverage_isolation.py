"""HARDENING-0-9 §3.1 — measure_coverage must not clobber the repo's own
coverage artifacts."""

import os

from reflecta.loop import coverage_paths, measure_coverage


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
