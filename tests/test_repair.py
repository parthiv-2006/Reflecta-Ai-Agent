from pathlib import Path
from unittest.mock import MagicMock, patch


from reflecta.models import (
    CoverageTarget,
    GeneratedTest,
    RepairResult,
    RunResult,
    TargetStatus,
)
from reflecta.repair import repair_test


def _make_target(tmp_path: Path) -> CoverageTarget:
    return CoverageTarget(
        file_path=tmp_path / "calc.py",
        qualified_name="calc.add",
        missing_lines=[10, 11],
    )


def _make_test(tmp_path: Path, target: CoverageTarget) -> GeneratedTest:
    test_file = tmp_path / "test_reflecta_calc_0.py"
    source = "def test_add():\n    assert add(1, 2) == 3\n"
    test_file.write_text(source)
    return GeneratedTest(
        target=target,
        test_file_path=test_file,
        source_code=source,
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )


def _failing_result(tb: str = "AssertionError") -> RunResult:
    return RunResult(passed=False, traceback=tb, duration=0.1)


def _passing_result() -> RunResult:
    return RunResult(passed=True, traceback="", duration=0.1)


# ---------------------------------------------------------------------------
# Test 1: repair fixes on attempt 1
# ---------------------------------------------------------------------------


def test_repair_fixes_on_attempt_1(tmp_path):
    target = _make_target(tmp_path)
    test = _make_test(tmp_path, target)
    failing = _failing_result()

    fixed_source = "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"

    mock_groq = MagicMock()
    mock_groq.repair.return_value = fixed_source

    with patch("reflecta.repair.run_test_isolated", return_value=_passing_result()):
        repaired, attempts = repair_test(
            test,
            failing,
            "def add(a, b): return a + b",
            repo_path=tmp_path,
            max_repairs=2,
            groq_client=mock_groq,
        )

    assert repaired is not None
    assert len(attempts) == 1
    assert attempts[0].result == RepairResult.PASS
    assert target.status == TargetStatus.KEPT


# ---------------------------------------------------------------------------
# Test 2: repair fixes on attempt 2 (first attempt fails)
# ---------------------------------------------------------------------------


def test_repair_fixes_on_attempt_2(tmp_path):
    target = _make_target(tmp_path)
    test = _make_test(tmp_path, target)
    failing = _failing_result()

    bad_source = "def test_add():\n    assert add(1, 2) == 99\n"
    good_source = "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"

    mock_groq = MagicMock()
    mock_groq.repair.side_effect = [bad_source, good_source]

    run_results = [_failing_result("still wrong"), _passing_result()]

    with patch("reflecta.repair.run_test_isolated", side_effect=run_results):
        repaired, attempts = repair_test(
            test,
            failing,
            "def add(a, b): return a + b",
            repo_path=tmp_path,
            max_repairs=2,
            groq_client=mock_groq,
        )

    assert repaired is not None
    assert len(attempts) == 2
    assert attempts[0].result == RepairResult.FAIL
    assert attempts[1].result == RepairResult.PASS
    assert target.status == TargetStatus.KEPT


# ---------------------------------------------------------------------------
# Test 3: ceiling exhausted — target marked FAILED, no infinite loop
# ---------------------------------------------------------------------------


def test_repair_exhausts_ceiling(tmp_path):
    target = _make_target(tmp_path)
    test = _make_test(tmp_path, target)
    failing = _failing_result()

    bad_source = "def test_add():\n    assert False\n"
    mock_groq = MagicMock()
    mock_groq.repair.return_value = bad_source

    with patch(
        "reflecta.repair.run_test_isolated", return_value=_failing_result("still broken")
    ):
        repaired, attempts = repair_test(
            test,
            failing,
            "def add(a, b): return a + b",
            repo_path=tmp_path,
            max_repairs=2,
            groq_client=mock_groq,
        )

    assert repaired is None
    assert len(attempts) == 2
    assert all(a.result == RepairResult.FAIL for a in attempts)
    assert target.status == TargetStatus.FAILED
    # Groq should have been called exactly max_repairs times
    assert mock_groq.repair.call_count == 2


# ---------------------------------------------------------------------------
# Test 4: fast model used on first attempt
# ---------------------------------------------------------------------------


def test_repair_uses_fast_model_first(tmp_path):
    from reflecta.llm.groq import MODEL_FAST

    target = _make_target(tmp_path)
    test = _make_test(tmp_path, target)
    failing = _failing_result()

    fixed_source = "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    mock_groq = MagicMock()
    mock_groq.repair.return_value = fixed_source

    with patch("reflecta.repair.run_test_isolated", return_value=_passing_result()):
        repair_test(
            test,
            failing,
            "def add(a, b): return a + b",
            repo_path=tmp_path,
            max_repairs=2,
            groq_client=mock_groq,
        )

    first_call_kwargs = mock_groq.repair.call_args_list[0]
    assert first_call_kwargs.kwargs.get("model") == MODEL_FAST


def test_repair_uses_isolated_runner(tmp_path):
    """repair_test must call run_test_isolated (not run_test) for isolation parity with loop.py."""
    target = _make_target(tmp_path)
    test = _make_test(tmp_path, target)
    failing = _failing_result()

    fixed_source = "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    mock_groq = MagicMock()
    mock_groq.repair.return_value = fixed_source

    with patch(
        "reflecta.repair.run_test_isolated", return_value=_passing_result()
    ) as mock_iso:
        repair_test(
            test,
            failing,
            "def add(a, b): return a + b",
            repo_path=tmp_path,
            max_repairs=2,
            groq_client=mock_groq,
        )

    mock_iso.assert_called_once()


def test_repair_runs_test_with_repo_path_cwd(tmp_path):
    """Repaired tests must run with cwd=repo_path, not the test file's parent directory."""
    target = _make_target(tmp_path)
    # Place the generated test in tests/_reflecta/ so its parent differs from repo_path,
    # exactly as in a real run.
    reflecta_dir = tmp_path / "tests" / "_reflecta"
    reflecta_dir.mkdir(parents=True)
    test_file = reflecta_dir / "test_reflecta_calc_0.py"
    test_file.write_text("def test_add():\n    assert add(1, 2) == 3\n")
    test = GeneratedTest(
        target=target,
        test_file_path=test_file,
        source_code=test_file.read_text(),
        model_used="gemini-2.5-flash",
        assertion_count=1,
    )
    failing = _failing_result()

    fixed_source = "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    mock_groq = MagicMock()
    mock_groq.repair.return_value = fixed_source

    with patch("reflecta.repair.run_test_isolated", return_value=_passing_result()) as mock_run:
        repair_test(
            test,
            failing,
            "def add(a, b): return a + b",
            repo_path=tmp_path,
            max_repairs=2,
            groq_client=mock_groq,
        )

    # run_test(test_file, repo_path) — second positional arg must be repo_path
    called_repo_path = mock_run.call_args.args[1]
    assert called_repo_path == tmp_path
    assert called_repo_path != test.test_file_path.parent


def test_extract_relevant_source_trims_large_files():
    from reflecta.repair import extract_relevant_source

    short_source = "def add(a, b):\n    return a + b\n"
    assert extract_relevant_source(short_source, "add", max_chars=100) == short_source

    large_source = "import os\n" * 10
    large_source += "#\n" * 150
    large_source += "class Calculator:\n    def add(self, a, b):\n        return a + b\n"
    large_source += "    def sub(self, a, b):\n        return a - b\n"

    extracted = extract_relevant_source(large_source, "Calculator.add", max_chars=400)
    assert "... [truncated for context size] ..." in extracted
    assert "def add(self, a, b):" in extracted
    assert "return a + b" in extracted
    assert "def sub" not in extracted


