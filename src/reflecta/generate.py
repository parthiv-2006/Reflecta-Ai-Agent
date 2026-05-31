import re
from pathlib import Path

from reflecta.llm import gemini
from reflecta.models import CoverageTarget, GeneratedTest
from reflecta.prompts import build_generation_prompt


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
    prompt = build_generation_prompt(
        source=source,
        qualified_name=target.qualified_name,
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
    )
