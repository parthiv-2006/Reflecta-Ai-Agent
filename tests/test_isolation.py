"""Task 12 — subprocess isolation tests.

run_test_isolated copies the repo to a temp dir before running the test,
so a destructive generated test cannot corrupt the working tree.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from reflecta.runner import run_test_isolated


def _write_test(directory: Path, name: str, source: str) -> Path:
    tests_dir = directory / "tests" / "_reflecta"
    tests_dir.mkdir(parents=True, exist_ok=True)
    p = tests_dir / name
    p.write_text(source)
    return p


def test_isolation_returns_passing_result(tmp_path):
    """A passing test run through the isolated runner returns RunResult(passed=True)."""
    test_file = _write_test(
        tmp_path, "test_pass.py", "def test_ok():\n    assert 1 + 1 == 2\n"
    )
    result = run_test_isolated(test_file, tmp_path)
    assert result.passed is True
    assert result.traceback == ""


def test_isolation_returns_failing_result(tmp_path):
    """A failing test returns RunResult(passed=False) with non-empty traceback."""
    test_file = _write_test(
        tmp_path, "test_fail.py", "def test_bad():\n    assert 1 == 2\n"
    )
    result = run_test_isolated(test_file, tmp_path)
    assert result.passed is False
    assert result.traceback != ""


def test_isolation_protects_source_file(tmp_path):
    """A generated test that deletes a file via __file__ navigation cannot
    corrupt the original working tree — it deletes from the temp copy only."""
    sentinel = tmp_path / "important.py"
    sentinel.write_text("x = 1\n")

    # The malicious test navigates from its __file__ up to the repo root
    # and tries to delete important.py.
    evil_src = (
        "from pathlib import Path\n"
        "def test_destructive():\n"
        "    p = Path(__file__).parent.parent.parent / 'important.py'\n"
        "    p.unlink(missing_ok=True)\n"
        "    assert 1 == 1\n"  # trivially true but enough to run
    )
    test_file = _write_test(tmp_path, "test_evil.py", evil_src)
    run_test_isolated(test_file, tmp_path)

    assert sentinel.exists(), (
        "run_test_isolated must not allow a generated test to delete files in the original repo"
    )


def test_isolation_cleans_up_temp_dir(tmp_path):
    """The temp directory created for isolation is removed after the run."""
    created_dirs: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(**kwargs):
        d = real_mkdtemp(**kwargs)
        created_dirs.append(d)
        return d

    test_file = _write_test(tmp_path, "test_ok.py", "def test_ok():\n    assert True\n")

    with patch("reflecta.runner.tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        run_test_isolated(test_file, tmp_path)

    for d in created_dirs:
        assert not Path(d).exists(), f"Temp dir {d} was not cleaned up"


def test_isolation_original_file_survives_on_failure(tmp_path):
    """Even when the test fails, the original generated test file is not removed
    by run_test_isolated (removal is loop.py's responsibility)."""
    test_file = _write_test(
        tmp_path, "test_fail.py", "def test_bad():\n    assert False\n"
    )
    run_test_isolated(test_file, tmp_path)
    assert test_file.exists(), (
        "run_test_isolated must not delete the original test file on failure"
    )
