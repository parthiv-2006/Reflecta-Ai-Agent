"""HARDENING-0-9 §3.1 — measure_coverage must not clobber the repo's own
coverage artifacts."""

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
