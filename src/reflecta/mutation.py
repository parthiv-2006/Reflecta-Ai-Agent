"""mutation.py — the honesty gate.

Line coverage proves a test *executed* the target's lines; it does not prove
the test would *notice* if those lines were wrong. Mutation testing is the
strong signal reflecta's thesis demands: perturb the target function's code
(flip a comparison, swap ``+``/``-``, negate a boolean) and re-run the
already-generated test. If the test still passes against the broken code, it
covered the lines without verifying their behaviour — a coverage-padding test,
exactly what the assertion and delta gates can let slip. A high mutation score
means the assertions actually pin behaviour down.

Pure AST, zero quota: mutants are produced by transforming a single node and
re-emitting the whole module with ``ast.unparse`` (always valid Python). No
LLM is involved. Scoring runs the test against each mutated copy in one reused
temp tree, so cost is bounded to (kept candidates × max_mutants) subprocess
runs and only ever fires *after* a test has already cleared the coverage gate.
"""

import ast
import copy
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from reflecta.coverage_report import _build_class_map
from reflecta.models import GeneratedTest, MutationResult
from reflecta.runner import run_test

# Operator swaps — each maps an AST op type to the type we replace it with.
# Symmetric so applying twice would be a no-op; the registry stays small and
# every swap changes behaviour in the common case.
_BINOP_SWAP = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Mult,
    ast.Mod: ast.Mult,
}
_CMP_SWAP = {
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}
_BOOL_SWAP = {ast.And: ast.Or, ast.Or: ast.And}


@dataclass
class Mutant:
    """A single-operator perturbation of the source, with a human description."""

    description: str
    source: str


class _OneMutation(ast.NodeTransformer):
    """Applies exactly the ``target_index``-th mutation within ``[lo, hi]``.

    Every visitor increments a single shared counter at each *applicable* site,
    so site indices are stable: counting (``target_index=-1``, matches nothing)
    and applying (``target_index=N``) traverse an identical tree in identical
    order. Set ``target_index`` out of range to only count sites.
    """

    def __init__(self, target_index: int, lo: int, hi: int) -> None:
        self.target_index = target_index
        self.lo = lo
        self.hi = hi
        self.i = -1
        self.applied: str | None = None

    def _in_span(self, node: ast.AST) -> bool:
        ln = getattr(node, "lineno", None)
        return ln is not None and self.lo <= ln <= self.hi

    def _maybe(self, node: ast.AST, apply, label: str):
        """Register one mutation site; apply it iff it is the targeted index."""
        self.i += 1
        if self.i == self.target_index:
            apply()
            self.applied = f"line {getattr(node, 'lineno', '?')}: {label}"

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        op = type(node.op)
        if self._in_span(node) and op in _BINOP_SWAP:
            new = _BINOP_SWAP[op]
            self._maybe(
                node,
                lambda: setattr(node, "op", new()),
                f"{op.__name__} → {new.__name__}",
            )
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if self._in_span(node):
            for idx, cmp_op in enumerate(node.ops):
                op = type(cmp_op)
                if op in _CMP_SWAP:
                    new = _CMP_SWAP[op]
                    self._maybe(
                        node,
                        lambda i=idx, n=new: node.ops.__setitem__(i, n()),
                        f"{op.__name__} → {new.__name__}",
                    )
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        op = type(node.op)
        if self._in_span(node) and op in _BOOL_SWAP:
            new = _BOOL_SWAP[op]
            self._maybe(
                node,
                lambda: setattr(node, "op", new()),
                f"{op.__name__} → {new.__name__}",
            )
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        self.generic_visit(node)
        if self._in_span(node) and isinstance(node.op, ast.Not):
            # Drop the ``not``: ``not x`` → ``x``.
            self._maybe(
                node,
                lambda: setattr(node, "_drop_not", True),
                "remove 'not'",
            )
            if getattr(node, "_drop_not", False):
                return node.operand
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        self.generic_visit(node)
        if not self._in_span(node):
            return node
        v = node.value
        if isinstance(v, bool):
            self._maybe(
                node,
                lambda: setattr(node, "value", not v),
                f"{v} → {not v}",
            )
        elif isinstance(v, (int, float)):
            self._maybe(
                node,
                lambda: setattr(node, "value", v + 1),
                f"{v!r} → {v + 1!r}",
            )
        return node


def _find_function_span(
    tree: ast.Module, qualified_name: str
) -> tuple[int, int] | None:
    """Return (first_line, last_line) of the function named by ``qualified_name``.

    Honours the ``ClassName.method`` convention from coverage_report so a method
    is matched inside its own class rather than a same-named free function.
    """
    parts = qualified_name.split(".")
    name = parts[-1]
    want_class = parts[-2] if len(parts) >= 2 else None
    class_map = _build_class_map(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            this_class = class_map.get(node.lineno)
            if want_class is None or this_class == want_class:
                return node.lineno, node.end_lineno or node.lineno
    return None


def generate_mutants(
    source: str, lo: int, hi: int, max_mutants: int = 30
) -> list[Mutant]:
    """Produce up to ``max_mutants`` single-operator mutants of ``source``.

    Only nodes whose line falls in ``[lo, hi]`` (the target function's span) are
    mutated, so we never alter — and never have to re-run — unrelated code. The
    whole module is re-emitted with ``ast.unparse``; a mutation that produces
    text identical to the original (a no-op) is dropped.
    """
    try:
        base = ast.parse(source)
        base_text = ast.unparse(base)
    except (SyntaxError, ValueError):
        return []

    counter = _OneMutation(-1, lo, hi)
    counter.visit(copy.deepcopy(base))
    total_sites = counter.i + 1

    mutants: list[Mutant] = []
    for idx in range(total_sites):
        if len(mutants) >= max_mutants:
            break
        tree = copy.deepcopy(base)
        transformer = _OneMutation(idx, lo, hi)
        mutated_tree = transformer.visit(tree)
        if transformer.applied is None:
            continue
        try:
            mutated = ast.unparse(ast.fix_missing_locations(mutated_tree))
        except (ValueError, AttributeError):
            continue
        if mutated == base_text:
            continue  # mutation had no textual effect — equivalent mutant
        mutants.append(Mutant(description=transformer.applied, source=mutated))
    return mutants


_IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    "*.pyc",
    ".venv",
    "venv",
    ".reflecta",
    ".pytest_cache",
    "node_modules",
    "build",
    "dist",
    ".omc",
)


def score_test(
    test: GeneratedTest,
    source: str,
    repo_path: Path,
    interpreter: str,
    *,
    max_mutants: int = 30,
    timeout_s: int = 15,
) -> MutationResult:
    """Run ``test`` against each mutant of its target and tally kills.

    One disposable copy of the repo is made and reused across all mutants: the
    target source file is rewritten in place per mutant and the (already
    written) test file is run against it. A mutant is killed when the test fails
    or errors; a passing test means the mutant survived. The real working tree
    is never touched. Returns an empty result (score 1.0) when the function has
    no mutable surface.
    """
    repo_path = Path(repo_path).resolve()
    span = _find_function_span(ast.parse(source), test.target.qualified_name)
    if span is None:
        return MutationResult(killed=0, total=0)
    mutants = generate_mutants(source, span[0], span[1], max_mutants=max_mutants)
    if not mutants:
        return MutationResult(killed=0, total=0)

    target_rel = test.target.file_path.resolve().relative_to(repo_path)
    test_rel = test.test_file_path.resolve().relative_to(repo_path)

    tmp_root = Path(tempfile.mkdtemp(prefix="reflecta_mut_"))
    try:
        tmp_repo = tmp_root / "repo"
        shutil.copytree(repo_path, tmp_repo, symlinks=True, ignore=_IGNORE)
        target_file = tmp_repo / target_rel
        test_file = tmp_repo / test_rel

        killed = 0
        survivors: list[str] = []
        for mutant in mutants:
            target_file.write_text(mutant.source, encoding="utf-8")
            result = run_test(
                test_file, tmp_repo, timeout_s=timeout_s, python_exe=interpreter
            )
            # A killed mutant makes the test fail/error/timeout. A test that
            # still passes (or whose tests all skip) did NOT catch the change.
            if not result.passed:
                killed += 1
            else:
                survivors.append(mutant.description)
        return MutationResult(killed=killed, total=len(mutants), survivors=survivors)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
