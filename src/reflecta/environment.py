"""
environment.py — pick the right Python interpreter for the target repo and
preflight its third-party imports.

Reflecta runs generated tests in a subprocess. Historically that subprocess was
always Reflecta's own interpreter (``sys.executable``), so a test only ran
correctly when the target project's dependencies happened to be installed in
Reflecta's environment. On any repo with its own virtualenv that meant every
``import <target>`` failed with ``ModuleNotFoundError`` and every target was
unfixable.

``detect_interpreter`` finds the target's own virtualenv so tests run against
the dependencies the code actually expects. ``preflight_imports`` checks — using
``importlib.util.find_spec``, which never executes module code — whether the
target's third-party imports are resolvable under that interpreter, so a missing
dependency is reported clearly up front instead of failing every target one by
one.
"""

import ast
import subprocess
import sys
from pathlib import Path

# Relative locations of the interpreter inside a virtualenv, Windows first.
_VENV_INTERPRETERS = (
    Path("Scripts") / "python.exe",
    Path("bin") / "python",
    Path("bin") / "python3",
)
# Directory names that conventionally hold a project virtualenv.
_VENV_DIRS = (".venv", "venv", "env", ".env")


def detect_interpreter(repo_path: Path) -> str:
    """Return the interpreter to run the target's tests with.

    Prefers a virtualenv living inside ``repo_path`` (``.venv``/``venv``/…), so
    generated tests see the project's installed dependencies. Falls back to the
    interpreter running Reflecta when no project venv is found.
    """
    repo_path = Path(repo_path).resolve()
    for venv_dir in _VENV_DIRS:
        base = repo_path / venv_dir
        if not base.is_dir():
            continue
        for rel in _VENV_INTERPRETERS:
            candidate = base / rel
            if candidate.exists():
                return str(candidate)
    return sys.executable


def _local_module_roots(repo_path: Path) -> set[str]:
    """Top-level importable names the repo itself provides (so they aren't
    mistaken for missing third-party deps)."""
    roots: set[str] = set()
    for child in repo_path.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            roots.add(child.name)
        elif child.is_file() and child.suffix == ".py":
            roots.add(child.stem)
    # src-layout: packages one level down under src/
    src = repo_path / "src"
    if src.is_dir():
        for child in src.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                roots.add(child.name)
    return roots


def collect_third_party_roots(source_files: list[Path], repo_path: Path) -> set[str]:
    """Root names of third-party modules imported by ``source_files``.

    Excludes the standard library and the repo's own top-level modules. Used to
    decide what to preflight.
    """
    stdlib = set(getattr(sys, "stdlib_module_names", frozenset()))
    local = _local_module_roots(Path(repo_path).resolve())
    roots: set[str] = set()
    for path in source_files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import → local
                    continue
                if node.module:
                    roots.add(node.module.split(".")[0])
    return {r for r in roots if r and r not in stdlib and r not in local}


def preflight_imports(interpreter: str, module_roots: set[str]) -> list[str]:
    """Return the subset of ``module_roots`` not importable under ``interpreter``.

    Uses ``importlib.util.find_spec`` in the target interpreter, which resolves
    a module *without executing it* — safe even when the module does network or
    filesystem work at import time.
    """
    if not module_roots:
        return []
    names = sorted(module_roots)
    check = (
        "import importlib.util, sys\n"
        f"names = {names!r}\n"
        "missing = []\n"
        "for n in names:\n"
        "    try:\n"
        "        if importlib.util.find_spec(n) is None:\n"
        "            missing.append(n)\n"
        "    except Exception:\n"
        "        missing.append(n)\n"
        "print('\\n'.join(missing))\n"
    )
    try:
        proc = subprocess.run(
            [interpreter, "-c", check],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
