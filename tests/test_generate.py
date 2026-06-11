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

    def mock_generate(prompt, *, client=None, **kwargs):
        captured["prompt"] = prompt
        return SIMPLE_TEST_SOURCE

    monkeypatch.setattr("reflecta.llm.router.generate", mock_generate)

    target = _make_target(tmp_path / "calc.py", [5, 6, 7])
    generate_test(target, "def add(a, b): return a + b", "", repo_path=tmp_path)

    assert "5" in captured["prompt"]
    assert "6" in captured["prompt"]
    assert "7" in captured["prompt"]


def test_file_written_to_correct_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "reflecta.llm.router.generate",
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
        "reflecta.llm.router.generate",
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


def test_collect_existing_tests_includes_human_excludes_reflecta(tmp_path):
    from reflecta.generate import collect_existing_tests

    tests_dir = tmp_path / "tests"
    (tests_dir).mkdir()
    (tests_dir / "test_calc_partial.py").write_text(
        "def test_human():\n    assert True\n"
    )
    reflecta_dir = tests_dir / "_reflecta"
    reflecta_dir.mkdir()
    (reflecta_dir / "test_reflecta_calc_0.py").write_text(
        "# generated, must be excluded\n"
    )
    # unrelated module's tests are not pulled in
    (tests_dir / "test_other.py").write_text("def test_other(): assert True\n")

    result = collect_existing_tests(tmp_path, "calc")

    assert "test_human" in result
    assert "generated, must be excluded" not in result
    assert "test_other" not in result


def test_collect_existing_tests_caps_size(tmp_path):
    from reflecta.generate import collect_existing_tests

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_calc_big.py").write_text("x = 1\n" * 5000)

    result = collect_existing_tests(tmp_path, "calc", max_chars=100)

    assert len(result) <= 100


def test_collect_existing_tests_no_tests_dir(tmp_path):
    from reflecta.generate import collect_existing_tests

    assert collect_existing_tests(tmp_path, "calc") == ""


def test_generated_test_has_assertion_count(monkeypatch, tmp_path):
    """HARDENING-0-9 §4.5: assertion_count reflects the real number of asserts."""
    monkeypatch.setattr(
        "reflecta.llm.router.generate",
        lambda *a, **kw: (
            "from calc import add\n\ndef test_a():\n    assert add(1, 1) == 2\n    assert add(2, 2) == 4\n"
        ),
    )
    target = _make_target(tmp_path / "calc.py", [2])
    result = generate_test(
        target, "def add(a, b): return a + b", "", repo_path=tmp_path
    )
    assert result.assertion_count == 2


BROKEN_FRAGMENT = "@mock.patch('datetime.datetime')\ndef test_x(m):\n    assert m\n"
FIXED_TEST = (
    "from unittest import mock\n\n"
    "@mock.patch('calc.helper')\n"
    "def test_x(m):\n"
    "    assert m is not None\n"
)


def test_regenerates_once_when_first_draft_is_broken(monkeypatch, tmp_path):
    """A broken first draft (missing import) triggers exactly one regeneration;
    the valid second draft is kept with no structural_error."""
    drafts = iter([BROKEN_FRAGMENT, FIXED_TEST])
    prompts = []

    def mock_generate(prompt, *, client=None, **kwargs):
        prompts.append(prompt)
        return next(drafts)

    monkeypatch.setattr("reflecta.llm.router.generate", mock_generate)

    target = _make_target(tmp_path / "calc.py", [2])
    result = generate_test(
        target, "def helper(): ...", "", repo_path=tmp_path
    )

    assert len(prompts) == 2
    assert result.generation_calls == 2
    assert result.structural_error is None
    # The retry prompt must tell the model why the first draft was rejected.
    assert "REJECTED" in prompts[1]
    ast.parse(result.test_file_path.read_text())


def test_structural_error_set_when_all_drafts_broken(monkeypatch, tmp_path):
    """If every draft is broken, the target carries a structural_error so the
    loop can skip it instead of paying the repair budget."""
    monkeypatch.setattr(
        "reflecta.llm.router.generate", lambda *a, **kw: BROKEN_FRAGMENT
    )

    target = _make_target(tmp_path / "calc.py", [2])
    result = generate_test(
        target, "def helper(): ...", "", repo_path=tmp_path, max_attempts=2
    )

    assert result.generation_calls == 2
    assert result.structural_error is not None
    assert "mock" in result.structural_error


def test_generated_file_written_as_utf8(monkeypatch, tmp_path):
    """Regression: generated tests often contain non-ASCII (sample strings for
    text-processing code). The file must be written as utf-8, not the platform
    default (cp1252 on Windows raises UnicodeEncodeError)."""
    non_ascii = (
        "from calc import add\n\n"
        "def test_unicode():\n"
        "    # café — naïve — 你好 — ‘smart quotes’\n"
        "    assert add(1, 2) == 3\n"
    )
    monkeypatch.setattr(
        "reflecta.llm.router.generate", lambda *a, **kw: non_ascii
    )
    target = _make_target(tmp_path / "calc.py", [2])
    result = generate_test(
        target, "def add(a, b): return a + b", "", repo_path=tmp_path
    )
    assert result.structural_error is None
    assert result.test_file_path.read_text(encoding="utf-8") == non_ascii


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

    def mock_generate(prompt, *, client=None, **kwargs):
        captured["prompt"] = prompt
        return SIMPLE_TEST_SOURCE

    monkeypatch.setattr("reflecta.llm.router.generate", mock_generate)

    target = CoverageTarget(
        file_path=tmp_path / "calc.py",
        qualified_name="Calculator.add",
        missing_lines=[5],
        priority=1.0,
        status=TargetStatus.PENDING,
    )
    generate_test(
        target, "class Calculator:\n    def add(self): ...", "", repo_path=tmp_path
    )

    assert "from calc import Calculator" in captured["prompt"]
    assert "from Calculator import add" not in captured["prompt"]


def test_sanitize_module_name_strips_path_separators():
    """Security: module_name derived from file stem must have path separators and
    dots stripped so a crafted coverage.json cannot escape tests/_reflecta/."""
    from reflecta.generate import _sanitize_module_name

    # Path separators must be removed
    assert _sanitize_module_name("../../../evil") == "evil"
    assert _sanitize_module_name("..\\..\\evil") == "evil"
    # Dots and dashes stripped
    assert _sanitize_module_name("my.module-name") == "mymodulename"
    # Normal names pass through unchanged
    assert _sanitize_module_name("calc") == "calc"
    assert _sanitize_module_name("my_module_123") == "my_module_123"
    # Empty result gets a safe placeholder
    assert _sanitize_module_name("...") == "module"
    assert _sanitize_module_name("") == "module"


def test_generate_test_path_traversal_blocked(monkeypatch, tmp_path):
    """Security: even if a file stem looks like a path traversal attempt, the
    generated test file must remain inside tests/_reflecta/."""
    monkeypatch.setattr(
        "reflecta.llm.router.generate",
        lambda *a, **kw: SIMPLE_TEST_SOURCE,
    )

    # Simulate a target whose file stem contains traversal characters.
    # After sanitization this should write to tests/_reflecta/test_reflecta_evil_0.py
    target = _make_target(tmp_path / "../../../evil.py", [2])
    # Override file_path to a safe path that exists, but use a stem that looks malicious
    target = CoverageTarget(
        file_path=tmp_path / "evil.py",
        qualified_name="evil",
        missing_lines=[2],
        priority=1.0,
        status=TargetStatus.PENDING,
    )
    # Monkeypatch the stem to simulate a crafted coverage.json entry
    import reflecta.generate as gen_module
    original_sanitize = gen_module._sanitize_module_name

    call_count = {"n": 0}

    def patched_sanitize(name):
        call_count["n"] += 1
        return original_sanitize(name)

    monkeypatch.setattr(gen_module, "_sanitize_module_name", patched_sanitize)

    result = generate_test(target, "def evil(): pass", "", repo_path=tmp_path)

    # Sanitizer must have been called
    assert call_count["n"] >= 1
    # Written path must be inside _reflecta/
    reflecta_dir = (tmp_path / "tests" / "_reflecta").resolve()
    assert result.test_file_path.resolve().is_relative_to(reflecta_dir)


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
