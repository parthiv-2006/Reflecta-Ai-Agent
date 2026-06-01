"""
coverage_report.py — parses coverage.json and maps missing lines back to
the enclosing function or method via the source AST.

Qualified-name conventions:
  - Top-level function  → "func_name"
  - Method in a class   → "ClassName.method_name"
  - Nested / closures   → treated as top-level
"""

import ast
from pathlib import Path

from reflecta.models import CoverageTarget


def _build_class_map(tree: ast.Module) -> dict[int, str]:
    """Return a mapping of line_number → class_name for every line inside a ClassDef."""
    class_map: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for lineno in range(node.lineno, node.end_lineno + 1):
                class_map[lineno] = node.name
    return class_map


def extract_targets(coverage_json: dict, repo_path: Path) -> list[CoverageTarget]:
    """Parse a coverage.json dict into CoverageTarget objects.

    Args:
        coverage_json: Parsed dict from ``coverage json`` output.
        repo_path: Root of the repository; file paths in the JSON are
                   resolved relative to this directory.

    Returns:
        One CoverageTarget per (function, gap) pair.  Files not found on
        disk and files with no missing lines are silently skipped.
    """
    targets: list[CoverageTarget] = []

    for file_str, file_data in coverage_json.get("files", {}).items():
        missing: list[int] = file_data.get("missing_lines", [])
        if not missing:
            continue

        abs_path = (repo_path / file_str).resolve()
        if not abs_path.exists():
            continue

        source = abs_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        class_map = _build_class_map(tree)

        missing_set = set(missing)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            func_lines = set(range(node.lineno, node.end_lineno + 1))
            func_missing = sorted(missing_set & func_lines)
            if not func_missing:
                continue

            # Qualify with the enclosing class name if present
            class_name = class_map.get(node.lineno)
            qualified_name = f"{class_name}.{node.name}" if class_name else node.name

            targets.append(
                CoverageTarget(
                    file_path=abs_path,
                    qualified_name=qualified_name,
                    missing_lines=func_missing,
                    priority=float(len(func_missing)),
                )
            )

    return targets
