"""
validation.py — structural validation of LLM-generated test files.

A generated file can be *syntactically* valid Python yet still be unrunnable:
an empty module parses fine, and a fragment like ``@mock.patch(...)`` with no
``import`` above it parses fine too (the ``NameError`` only surfaces at pytest
collection). Catching these here — before the file is written and before the
repair budget is spent — lets the loop regenerate or discard instead of
shipping garbage that can never pass.

This is a deliberately conservative heuristic: it flags only what is almost
certainly broken (empty, unparseable, no test, placeholder stubs, or a
module-level/decorator name that is never bound anywhere in the file). When in
doubt it returns valid and lets real execution be the judge.
"""

import ast
import builtins

# Lazy/partial answers the model emits instead of a complete file. Their
# presence means the file is a sketch, not a runnable test.
_PLACEHOLDER_MARKERS = (
    "rest of the function remains the same",
    "rest of the code remains the same",
    "rest of the test remains the same",
    "your code here",
    "your test here",
    "implementation goes here",
    "fill in the rest",
    "... (rest",
    "# todo: implement",
)

_BUILTIN_NAMES = frozenset(dir(builtins)) | {"__file__", "__name__", "__doc__"}


def _has_test_callable(tree: ast.Module) -> bool:
    """True if the module defines something pytest would collect as a test."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    sub.name.startswith("test")
                ):
                    return True
    return False


def _bound_names(tree: ast.Module) -> set[str]:
    """Over-approximate every name bound anywhere in the module.

    Gathering names module-wide (imports, defs, assignments, comprehension and
    loop targets, function/lambda args) keeps the undefined-name check
    conservative: a name bound in *any* scope is treated as defined, so we only
    flag names that appear nowhere as a binding (the real ``mock``-was-never-
    imported bug).
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.alias):
            # ``import a.b.c`` binds ``a``; ``import a.b as c`` binds ``c``.
            name = node.asname or node.name.split(".")[0]
            bound.add(name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def _undefined_decorator_name(tree: ast.Module, bound: set[str]) -> str | None:
    """Return the first decorator base-name that is never bound, else None.

    Decorators are evaluated at import time, so an undefined name here is a
    guaranteed collection-time ``NameError`` — exactly the failure that the
    truncated ``@mock.patch`` fragments produced.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for dec in node.decorator_list:
            for sub in ast.walk(dec):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id not in bound and sub.id not in _BUILTIN_NAMES:
                        return sub.id
    return None


def validate_test_source(source: str) -> tuple[bool, str]:
    """Return ``(is_valid, reason)`` for a generated test file.

    ``reason`` is a short human string when invalid, empty when valid. The loop
    uses the verdict to decide whether to write/run the file or regenerate it.
    """
    if not source or not source.strip():
        return False, "empty file"

    lowered = source.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            return False, f"contains placeholder text ({marker!r})"

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"syntax error: {exc.msg}"

    if not _has_test_callable(tree):
        return False, "no test_* function defined"

    bound = _bound_names(tree)
    missing = _undefined_decorator_name(tree, bound)
    if missing is not None:
        return (
            False,
            f"decorator references undefined name {missing!r} (missing import)",
        )

    return True, ""
