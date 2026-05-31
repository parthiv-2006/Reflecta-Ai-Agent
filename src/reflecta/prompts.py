def build_repair_prompt(source: str, test_source: str, traceback: str) -> str:
    return (
        "The following pytest test file fails. Fix it so all tests pass.\n\n"
        "RULES:\n"
        "- Output ONLY valid Python code. No markdown. No explanation.\n"
        "- Do not remove assertions. Fix the logic, imports, or values.\n\n"
        f"Source:\n{source}\n\n"
        f"Failing test:\n{test_source}\n\n"
        f"Traceback:\n{traceback}\n"
    )


def build_generation_prompt(
    source: str,
    qualified_name: str,
    missing_lines: list[int],
    existing_tests: str,
) -> str:
    existing_section = (
        f"Existing tests for context (do NOT duplicate them):\n{existing_tests}\n\n"
        if existing_tests.strip()
        else ""
    )
    return (
        "Write a pytest test file for the Python source below.\n\n"
        "RULES:\n"
        "- Output ONLY valid Python code. No markdown fences. No explanation.\n"
        "- The first line must be an import statement.\n"
        f"- Import the target directly: `from {qualified_name.split('.')[0]} import {qualified_name.split('.')[-1]}`\n"
        "- Write at least two test functions that exercise the target with real assertions.\n"
        "- Do NOT use `assert True` or trivially-true assertions.\n\n"
        f"Source:\n{source}\n\n"
        f"{existing_section}"
        f"Target to cover: `{qualified_name}`\n"
        f"Missing line numbers: {missing_lines}\n"
    )
