import ast
from pathlib import Path

import pytest

from reflecta.generate import generate_test
from reflecta.models import CoverageTarget, TargetStatus

SIMPLE_TEST_SOURCE = "def test_x():\n    assert 1 + 1 == 2\n"


def _make_target(file_path: Path, missing_lines: list[int]) -> CoverageTarget:
    return CoverageTarget(
        file_path=file_path,
        qualified_name="add",
        missing_lines=missing_lines,
        priority=float(len(missing_lines)),
        status=TargetStatus.PENDING,
    )


def test_prompt_contains_missed_lines(monkeypatch, tmp_path):
    captured = {}

    def mock_generate(prompt, *, client=None):
        captured["prompt"] = prompt
        return SIMPLE_TEST_SOURCE

    monkeypatch.setattr("reflecta.generate.gemini.generate", mock_generate)

    target = _make_target(tmp_path / "calc.py", [5, 6, 7])
    generate_test(target, "def add(a, b): return a + b", "", repo_path=tmp_path)

    assert "5" in captured["prompt"]
    assert "6" in captured["prompt"]
    assert "7" in captured["prompt"]


def test_file_written_to_correct_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "reflecta.generate.gemini.generate",
        lambda *a, **kw: SIMPLE_TEST_SOURCE,
    )

    target = _make_target(tmp_path / "calc.py", [2])
    result = generate_test(
        target, "def add(a, b): return a + b", "", repo_path=tmp_path
    )

    expected = tmp_path / "tests" / "_reflecta" / "test_reflecta_calc_0.py"
    assert result.test_file_path == expected
    assert expected.read_text() == SIMPLE_TEST_SOURCE


def test_counter_increments(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "reflecta.generate.gemini.generate",
        lambda *a, **kw: SIMPLE_TEST_SOURCE,
    )

    reflecta_dir = tmp_path / "tests" / "_reflecta"
    reflecta_dir.mkdir(parents=True)
    (reflecta_dir / "test_reflecta_calc_0.py").write_text("# existing")

    target = _make_target(tmp_path / "calc.py", [2])
    result = generate_test(
        target, "def add(a, b): return a + b", "", repo_path=tmp_path
    )

    expected = tmp_path / "tests" / "_reflecta" / "test_reflecta_calc_1.py"
    assert result.test_file_path == expected
    assert expected.exists()


def test_module_import_path_flat_module(tmp_path):
    from reflecta.generate import module_import_path

    f = tmp_path / "calc.py"
    f.write_text("x = 1\n")
    assert module_import_path(f, tmp_path) == "calc"


def test_module_import_path_packaged_module(tmp_path):
    from reflecta.generate import module_import_path

    pkg = tmp_path / "pkg" / "sub"
    pkg.mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    mod = pkg / "mod.py"
    mod.write_text("y = 2\n")
    assert module_import_path(mod, tmp_path) == "pkg.sub.mod"


def test_method_target_imports_class_not_method(monkeypatch, tmp_path):
    """Regression (HARDENING-0-9 §1.3): a Class.method target must import the
    class, never `from Class import method`."""
    captured = {}

    def mock_generate(prompt, *, client=None):
        captured["prompt"] = prompt
        return SIMPLE_TEST_SOURCE

    monkeypatch.setattr("reflecta.generate.gemini.generate", mock_generate)

    target = CoverageTarget(
        file_path=tmp_path / "calc.py",
        qualified_name="Calculator.add",
        missing_lines=[5],
        priority=1.0,
        status=TargetStatus.PENDING,
    )
    generate_test(target, "class Calculator:\n    def add(self): ...", "", repo_path=tmp_path)

    assert "from calc import Calculator" in captured["prompt"]
    assert "from Calculator import add" not in captured["prompt"]


@pytest.mark.live
def test_live_ast_valid():
    calc_path = Path("examples/sample_project/calc.py")
    source = calc_path.read_text()
    repo_path = Path(".")

    shapes = [
        CoverageTarget(
            file_path=calc_path,
            qualified_name="add",
            missing_lines=[2],
            priority=1.0,
            status=TargetStatus.PENDING,
        ),
        CoverageTarget(
            file_path=calc_path,
            qualified_name="subtract",
            missing_lines=[6],
            priority=1.0,
            status=TargetStatus.PENDING,
        ),
        CoverageTarget(
            file_path=calc_path,
            qualified_name="multiply",
            missing_lines=[10],
            priority=1.0,
            status=TargetStatus.PENDING,
        ),
    ]

    for target in shapes:
        result = generate_test(target, source, "", repo_path=repo_path)
        ast.parse(result.source_code)
