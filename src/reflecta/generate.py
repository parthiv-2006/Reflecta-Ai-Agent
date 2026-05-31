import ast
import re
from pathlib import Path

from reflecta.llm import gemini
from reflecta.models import CoverageTarget, GeneratedTest
from reflecta.prompts import build_generation_prompt


def module_import_path(file_path: Path, repo_path: Path) -> str:
    """Return the dotted import path pytest would use for ``file_path``.

    Walks up from the file collecting package components (directories that
    contain ``__init__.py``), stopping at the first non-package ancestor or at
    ``repo_path``. This handles flat modules (``calc``), packaged modules
    (``pkg.sub.mod``), and src-layout (``reflecta.cli`` for ``src/reflecta/cli.py``)
    without hardcoding the bare stem. HARDENING-0-9 §1.3.
    """
    file_path = Path(file_path).resolve()
    repo_path = Path(repo_path).resolve()
    parts = [file_path.stem]
    parent = file_path.parent
    while (
        (parent / "__init__.py").exists()
        and parent != parent.parent
        and parent != repo_path
    ):
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts))


def collect_existing_tests(
    repo_path: Path, module_name: str, max_chars: int = 4000
) -> str:
    """Return concatenated human test sources relevant to ``module_name``.

    Feeds the generation prompt's "do NOT duplicate" context so Gemini stops
    regenerating surface a human test already covers (which only wastes the
    scarce LLM budget). Generated ``_reflecta`` tests are excluded, output is
    size-capped to protect the context window. HARDENING-0-9 §2.3.
    """
    tests_root = Path(repo_path) / "tests"
    if not tests_root.exists():
        return ""
    chunks: list[str] = []
    for f in sorted(tests_root.rglob("test_*.py")):
        if "_reflecta" in f.parts:
            continue
        if module_name not in f.name:
            continue
        try:
            chunks.append(f.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n\n".join(chunks)[:max_chars]


def _next_counter(reflecta_dir: Path, module_name: str) -> int:
    if not reflecta_dir.exists():
        return 0
    pattern = re.compile(rf"test_reflecta_{re.escape(module_name)}_(\d+)\.py$")
    indices = [
        int(m.group(1)) for f in reflecta_dir.iterdir() if (m := pattern.match(f.name))
    ]
    return max(indices) + 1 if indices else 0


def generate_test(
    target: CoverageTarget,
    source: str,
    existing_tests: str,
    *,
    repo_path: Path,
    gemini_client=None,
) -> GeneratedTest:
    import_path = module_import_path(target.file_path, repo_path)
    prompt = build_generation_prompt(
        source=source,
        qualified_name=target.qualified_name,
        module_path=import_path,
        missing_lines=target.missing_lines,
        existing_tests=existing_tests,
    )

    source_code = gemini.generate(prompt, client=gemini_client)

    module_name = Path(target.file_path).stem
    reflecta_dir = Path(repo_path) / "tests" / "_reflecta"
    counter = _next_counter(reflecta_dir, module_name)
    test_file_path = reflecta_dir / f"test_reflecta_{module_name}_{counter}.py"

    reflecta_dir.mkdir(parents=True, exist_ok=True)
    test_file_path.write_text(source_code)

    return GeneratedTest(
        target=target,
        test_file_path=test_file_path,
        source_code=source_code,
        model_used="gemini-2.5-flash",
        assertion_count=_count_assertions(source_code),
    )


def _count_assertions(source_code: str) -> int:
    """Number of ``assert`` statements in the generated test (0 if unparseable)."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return 0
    return sum(isinstance(n, ast.Assert) for n in ast.walk(tree))
