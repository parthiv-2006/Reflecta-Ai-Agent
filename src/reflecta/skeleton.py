"""
Walking skeleton — task 0.
Prove that one real Gemini Flash call can raise coverage on a fixture.
Run: python -m reflecta.skeleton
"""
import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from reflecta.llm.gemini import generate_test_source

REPO_ROOT = Path(__file__).parent.parent.parent
SAMPLE_DIR = REPO_ROOT / "examples" / "sample_project"
COVERAGE_JSON = SAMPLE_DIR / "coverage.json"
OUT_DIR = SAMPLE_DIR / "tests" / "_reflecta"


# Minimal stand-in — replaced by models.py in task 0.5
@dataclass
class CoverageTarget:
    file_path: Path
    qualified_name: str
    missing_lines: list[int]


def _load_dotenv(repo_root: Path) -> None:
    env_file = repo_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _run_coverage(sample_dir: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", "-m", "pytest", "tests/", "-q", "--tb=short"],
        cwd=sample_dir,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "coverage.json", "--include=calc.py"],
        cwd=sample_dir,
        check=True,
    )


def _read_total(coverage_json: Path) -> float:
    return json.loads(coverage_json.read_text())["totals"]["percent_covered"]


def extract_targets(coverage_json: Path, source_dir: Path) -> list[CoverageTarget]:
    """Parse coverage.json and map missing lines to enclosing functions via AST."""
    data = json.loads(coverage_json.read_text())
    targets: list[CoverageTarget] = []
    for file_str, file_data in data.get("files", {}).items():
        missing = file_data.get("missing_lines", [])
        if not missing:
            continue
        abs_path = (source_dir / file_str).resolve()
        if not abs_path.exists():
            continue
        tree = ast.parse(abs_path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_lines = set(range(node.lineno, node.end_lineno + 1))
            func_missing = [ln for ln in missing if ln in func_lines]
            if func_missing:
                targets.append(
                    CoverageTarget(
                        file_path=abs_path,
                        qualified_name=node.name,
                        missing_lines=func_missing,
                    )
                )
    return targets


def main() -> None:
    _load_dotenv(REPO_ROOT)

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set — add it to .env")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "__init__.py").touch()

    # Step 1 — baseline coverage
    print("Running baseline coverage …")
    _run_coverage(SAMPLE_DIR)
    before = _read_total(COVERAGE_JSON)
    print(f"  baseline: {before:.1f}%")

    # Step 2 — find the gap
    targets = extract_targets(COVERAGE_JSON, SAMPLE_DIR)
    if not targets:
        sys.exit("No coverage gaps found — fixture already fully covered.")
    target = targets[0]
    print(f"  target: {target.qualified_name}  missing lines {target.missing_lines}")

    # Step 3 — generate with Gemini
    print("Calling Gemini …")
    raw = generate_test_source(
        source_code=target.file_path.read_text(),
        qualified_name=target.qualified_name,
        missing_lines=target.missing_lines,
    )

    try:
        ast.parse(raw)
    except SyntaxError as exc:
        print("--- Gemini output (INVALID SYNTAX) ---")
        print(raw)
        sys.exit(f"Gemini returned invalid Python: {exc}")

    print("  ast.parse OK")

    # Step 4 — write generated test (scan for next available counter, never overwrite)
    module_name = target.file_path.stem
    existing = sorted(OUT_DIR.glob(f"test_reflecta_{module_name}_*.py"))
    next_n = int(existing[-1].stem.rsplit("_", 1)[-1]) + 1 if existing else 0
    out_path = OUT_DIR / f"test_reflecta_{module_name}_{next_n}.py"
    out_path.write_text(raw)
    print(f"  wrote {out_path.relative_to(REPO_ROOT)}")

    # Step 5 — rerun coverage
    print("Rerunning coverage …")
    _run_coverage(SAMPLE_DIR)
    after = _read_total(COVERAGE_JSON)

    # Step 6 — report
    print(f"\ncoverage: {before:.1f}% -> {after:.1f}%")
    if after > before:
        print("SUCCESS: coverage increased.")
    else:
        print("WARNING: coverage did not increase — inspect the generated test.")


if __name__ == "__main__":
    main()
