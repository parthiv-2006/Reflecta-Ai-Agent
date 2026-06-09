"""
Tests for src/reflecta/coverage_report.py — Task 1.

All tests use in-memory fixtures (tmp_path + synthetic dicts).
No real coverage.json files needed.
"""

from pathlib import Path

from reflecta.coverage_report import extract_targets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_source(tmp_path: Path, filename: str, source: str) -> Path:
    p = tmp_path / filename
    p.write_text(source)
    return p


def _coverage_json(filename: str, missing: list[int]) -> dict:
    return {
        "files": {
            filename: {
                "summary": {"percent_covered": 50.0},
                "missing_lines": missing,
                "missing_branches": [],
            }
        },
        "totals": {"percent_covered": 50.0},
    }


# ---------------------------------------------------------------------------
# Case 1 — module-level function with missing lines
# ---------------------------------------------------------------------------


def test_top_level_function(tmp_path: Path) -> None:
    source = """\
def add(a, b):
    return a + b
"""
    _write_source(tmp_path, "calc.py", source)
    cov = _coverage_json("calc.py", missing=[2])

    targets = extract_targets(cov, tmp_path)

    assert len(targets) == 1
    assert targets[0].qualified_name == "add"
    assert targets[0].missing_lines == [2]
    assert targets[0].file_path == tmp_path / "calc.py"


# ---------------------------------------------------------------------------
# Case 2 — method inside a class → qualified as ClassName.method_name
# ---------------------------------------------------------------------------


def test_class_method(tmp_path: Path) -> None:
    source = """\
class MyCalc:
    def multiply(self, a, b):
        return a * b
"""
    _write_source(tmp_path, "calc.py", source)
    cov = _coverage_json("calc.py", missing=[3])

    targets = extract_targets(cov, tmp_path)

    assert len(targets) == 1
    assert targets[0].qualified_name == "MyCalc.multiply"
    assert targets[0].missing_lines == [3]


# ---------------------------------------------------------------------------
# Case 3 — two functions, only one has missing lines → 1 target returned
# ---------------------------------------------------------------------------


def test_only_missing_function_returned(tmp_path: Path) -> None:
    source = """\
def covered(x):
    return x

def uncovered(x):
    return x * 2
"""
    _write_source(tmp_path, "calc.py", source)
    cov = _coverage_json("calc.py", missing=[5])

    targets = extract_targets(cov, tmp_path)

    assert len(targets) == 1
    assert targets[0].qualified_name == "uncovered"


# ---------------------------------------------------------------------------
# Case 4 — missing lines outside any function → 0 targets
# ---------------------------------------------------------------------------


def test_module_level_missing_lines_ignored(tmp_path: Path) -> None:
    source = """\
X = 42

def covered(x):
    return x
"""
    _write_source(tmp_path, "calc.py", source)
    # line 1 is the module-level assignment — not inside any function
    cov = _coverage_json("calc.py", missing=[1])

    targets = extract_targets(cov, tmp_path)

    assert targets == []


# ---------------------------------------------------------------------------
# Case 5 — file listed in JSON but not on disk → skipped gracefully
# ---------------------------------------------------------------------------


def test_missing_file_skipped(tmp_path: Path) -> None:
    cov = _coverage_json("nonexistent.py", missing=[5, 6])

    # should not raise
    targets = extract_targets(cov, tmp_path)

    assert targets == []


# ---------------------------------------------------------------------------
# Case 6 — file with zero missing lines → not included
# ---------------------------------------------------------------------------


def test_fully_covered_file_excluded(tmp_path: Path) -> None:
    source = """\
def covered(x):
    return x
"""
    _write_source(tmp_path, "calc.py", source)
    cov = _coverage_json("calc.py", missing=[])

    targets = extract_targets(cov, tmp_path)

    assert targets == []


# ---------------------------------------------------------------------------
# Case 7 — a syntactically broken file does not abort extraction (AUDIT H1)
# ---------------------------------------------------------------------------


def test_broken_file_skipped_others_still_extracted(tmp_path: Path) -> None:
    """One unparseable source file must be skipped, not crash the whole run.

    extract_targets runs before the loop's per-target error isolation, so an
    unguarded SyntaxError here would abort the entire run before any target is
    attempted.
    """
    _write_source(tmp_path, "broken.py", "def oops(:\n    pass\n")  # invalid syntax
    _write_source(tmp_path, "good.py", "def add(a, b):\n    return a + b\n")

    cov = {
        "files": {
            "broken.py": {"missing_lines": [1, 2], "missing_branches": []},
            "good.py": {"missing_lines": [2], "missing_branches": []},
        },
        "totals": {"percent_covered": 50.0},
    }

    targets = extract_targets(cov, tmp_path)

    # The broken file is skipped; the good file's target still comes through.
    names = [t.qualified_name for t in targets]
    assert names == ["add"]


# ---------------------------------------------------------------------------
# Bonus — priority equals number of missing lines
# ---------------------------------------------------------------------------


def test_priority_equals_missing_line_count(tmp_path: Path) -> None:
    source = """\
def add(a, b):
    if a > 0:
        return a + b
    return b
"""
    _write_source(tmp_path, "calc.py", source)
    cov = _coverage_json("calc.py", missing=[3, 4])

    targets = extract_targets(cov, tmp_path)

    assert len(targets) == 1
    assert targets[0].priority == 2.0


# ---------------------------------------------------------------------------
# Entrypoint detection
# ---------------------------------------------------------------------------


def test_main_function_flagged_as_entrypoint(tmp_path: Path) -> None:
    source = """def helper(x):
    return x + 1


def main():
    helper(1)
    print("done")
"""
    _write_source(tmp_path, "app.py", source)
    cov = _coverage_json("app.py", missing=[2, 6, 7])

    targets = {t.qualified_name: t for t in extract_targets(cov, tmp_path)}

    assert targets["main"].is_entrypoint is True
    assert targets["helper"].is_entrypoint is False


def test_function_called_under_main_guard_is_entrypoint(tmp_path: Path) -> None:
    source = """def run():
    return 42


def pure(x):
    return x * 2


if __name__ == "__main__":
    run()
"""
    _write_source(tmp_path, "cli.py", source)
    cov = _coverage_json("cli.py", missing=[2, 6])

    targets = {t.qualified_name: t for t in extract_targets(cov, tmp_path)}

    assert targets["run"].is_entrypoint is True
    assert targets["pure"].is_entrypoint is False


# ---------------------------------------------------------------------------
# Test files must never become coverage targets. When coverage runs with
# --source=. (flat repos), tests/ and tests/_reflecta/ land in coverage.json;
# without this filter reflecta generates tests *for its own generated tests*,
# inflating every subsequent run and burning Gemini RPD on garbage targets.
# ---------------------------------------------------------------------------


def _multi_coverage_json(entries: dict[str, list[int]]) -> dict:
    return {
        "files": {
            name: {
                "summary": {"percent_covered": 50.0},
                "missing_lines": missing,
                "missing_branches": [],
            }
            for name, missing in entries.items()
        },
        "totals": {"percent_covered": 50.0},
    }


def test_test_files_and_conftest_excluded(tmp_path: Path) -> None:
    func = "def f(x):\n    return x\n"
    _write_source(tmp_path, "calc.py", func)
    _write_source(tmp_path, "conftest.py", func)
    (tmp_path / "tests" / "_reflecta").mkdir(parents=True)
    (tmp_path / "tests" / "test_calc.py").write_text(func)
    (tmp_path / "tests" / "helpers.py").write_text(func)
    (tmp_path / "tests" / "_reflecta" / "test_reflecta_calc_0.py").write_text(func)
    _write_source(tmp_path, "calc_test.py", func)

    cov = _multi_coverage_json(
        {
            "calc.py": [2],
            "conftest.py": [2],
            "tests/test_calc.py": [2],
            "tests/helpers.py": [2],
            "tests/_reflecta/test_reflecta_calc_0.py": [2],
            "calc_test.py": [2],
        }
    )

    targets = extract_targets(cov, tmp_path)

    # Only the real source module survives — anything under a test dir, any
    # test_*.py / *_test.py, and conftest.py are filtered out.
    assert [str(t.file_path) for t in targets] == [str(tmp_path / "calc.py")]
