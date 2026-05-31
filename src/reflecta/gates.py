import ast

from reflecta.models import GeneratedTest


def passes_delta_gate(before: float, after: float) -> bool:
    return after > before


def _is_trivial(node: ast.Assert) -> bool:
    test = node.test
    if isinstance(test, ast.Constant):
        return True
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Constant):
        return all(
            isinstance(c, ast.Constant) and c.value == test.left.value
            for c in test.comparators
        )
    return False


def passes_assertion_gate(test: GeneratedTest) -> bool:
    try:
        tree = ast.parse(test.source_code)
    except SyntaxError:
        return False
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if not asserts:
        return False
    return not all(_is_trivial(a) for a in asserts)
