import ast

from reflecta.models import GeneratedTest, MutationResult


def passes_delta_gate(before: float, after: float) -> bool:
    return after > before


def passes_mutation_gate(result: MutationResult, min_score: float) -> bool:
    """The honesty gate: a kept test must kill at least ``min_score`` of the
    mutants planted in its target. A function with no mutable surface (total
    == 0) scores 1.0 and passes — a test cannot be faulted for code it cannot
    meaningfully break."""
    return result.score >= min_score


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
