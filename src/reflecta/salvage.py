"""
salvage.py — keep the passing half of a partially-failing generated test file.

When the repair budget is exhausted, the whole generated file used to be
deleted — even when most of its test functions passed. On real repos that
threw away the majority of the value: a draft with 2 passing and 2 failing
tests scores zero. The salvage pass strips exactly the failing test functions
(by AST line spans, so the surviving code is byte-identical) and gives the
trimmed file one more run through the normal gates.

Salvage never invents code: it only deletes top-level test functions or test
methods named in pytest's FAILED/ERROR summary lines. Fixtures, imports and
helpers are kept. If nothing failing can be identified, or no test would
survive the cut, salvage declines (returns None) and the target fails as
before.
"""

import ast
import re

# pytest -q summary lines:  FAILED path::name - msg   /   ERROR path::Class::name
_SUMMARY_RE = re.compile(r"^(?:FAILED|ERROR)\s+\S+?\.py(?:::([\w:\[\]\-]+))?", re.M)


def failing_test_names(pytest_output: str) -> set[str]:
    """Test function/method names pytest reported as FAILED or ERROR.

    Parametrized ids (``test_x[case]``) collapse to the function name; class
    paths (``TestX::test_y``) yield the method name. A FAILED/ERROR line with
    no ``::`` part (whole-file collection error) contributes nothing — salvage
    cannot help there.
    """
    names: set[str] = set()
    for m in _SUMMARY_RE.finditer(pytest_output):
        node_id = m.group(1)
        if not node_id:
            continue
        leaf = node_id.split("::")[-1]
        leaf = leaf.split("[", 1)[0]
        if leaf:
            names.add(leaf)
    return names


def _span(node: ast.AST) -> tuple[int, int]:
    """1-based inclusive line span of a def, including its decorators."""
    start = node.lineno
    for dec in getattr(node, "decorator_list", []):
        start = min(start, dec.lineno)
    return start, node.end_lineno


def strip_failing_tests(source: str, failing: set[str]) -> str | None:
    """Remove the named test functions/methods from ``source`` by line span.

    Returns the trimmed source, or None when salvage is not applicable:
    unparseable source, no named function actually found, or no ``test_*``
    function/method would remain. Non-test code (fixtures, helpers, imports)
    is always preserved verbatim.
    """
    if not failing:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    doomed_spans: list[tuple[int, int]] = []
    survivors = 0

    def visit_body(body: list[ast.stmt]) -> None:
        nonlocal survivors
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    if node.name in failing:
                        doomed_spans.append(_span(node))
                    else:
                        survivors += 1
            elif isinstance(node, ast.ClassDef):
                class_tests = [
                    n
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name.startswith("test_")
                ]
                doomed_here = [n for n in class_tests if n.name in failing]
                if class_tests and len(doomed_here) == len(class_tests):
                    # Every test in the class failed — drop the whole class.
                    doomed_spans.append(_span(node))
                else:
                    doomed_spans.extend(_span(n) for n in doomed_here)
                    survivors += len(class_tests) - len(doomed_here)

    visit_body(tree.body)

    if not doomed_spans or survivors == 0:
        return None

    doomed_lines = {
        line for start, end in doomed_spans for line in range(start, end + 1)
    }
    kept = [
        line
        for i, line in enumerate(source.splitlines(keepends=True), start=1)
        if i not in doomed_lines
    ]
    return "".join(kept)
