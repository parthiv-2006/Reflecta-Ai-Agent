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
    module_path: str,
    missing_lines: list[int],
    existing_tests: str,
    retry_reason: str | None = None,
) -> str:
    existing_section = (
        f"Existing tests for context (do NOT duplicate them):\n{existing_tests}\n\n"
        if existing_tests.strip()
        else ""
    )

    # The first dotted component is the importable top-level name: the function
    # itself for a free function, or the enclosing class for a method. Importing
    # the class (not the method) is what makes method targets work.
    top_symbol = qualified_name.split(".")[0]
    is_method = "." in qualified_name
    import_line = f"from {module_path} import {top_symbol}"
    if is_method:
        method_name = qualified_name.split(".")[-1]
        target_hint = (
            f"- Import the class: `{import_line}`\n"
            f"- Instantiate `{top_symbol}` and call its `{method_name}` method.\n"
        )
    else:
        target_hint = f"- Import the target directly: `{import_line}`\n"

    retry_section = (
        "\nYOUR PREVIOUS ATTEMPT WAS REJECTED: "
        f"{retry_reason}.\n"
        "Return the COMPLETE corrected file from the very first import line. "
        "Do not abbreviate, do not reference a previous version.\n"
        if retry_reason
        else ""
    )

    return (
        "Write a complete, self-contained pytest test file for the Python "
        "source below.\n\n"
        "RULES:\n"
        "- Output ONLY valid Python code. No markdown fences. No explanation.\n"
        "- Output ONE complete file. Never abbreviate with comments like "
        "`# rest of the function remains the same`, `...`, or `# your code here`. "
        "Every function body must be written out in full.\n"
        "- The first line must be an import statement.\n"
        "- Import EVERYTHING you use. If you use `mock.patch`, the file MUST "
        "include `from unittest import mock` at the top.\n"
        "- Every fixture passed as a test-function argument must either be a "
        "built-in pytest fixture or be defined with `@pytest.fixture` in this "
        "same file. Do not reference fixtures that are not defined here.\n"
        f"{target_hint}"
        "- For mocking, always use standard library `unittest.mock` (e.g. `mock.patch`, `mock.MagicMock`). Do NOT use the third-party `mocker` fixture from `pytest-mock` since it is not installed.\n"
        "- NEVER write `async def test_*` functions — the target repo may not "
        "have pytest-asyncio configured, so they are silently skipped. To test "
        "an async function, write a normal synchronous test that calls it via "
        "`asyncio.run(...)` (and `import asyncio` at the top).\n"
        "- Write at least two test functions that exercise the target with real assertions.\n"
        "- Do NOT use `assert True` or trivially-true assertions.\n"
        f"{retry_section}\n"
        f"Source:\n{source}\n\n"
        f"{existing_section}"
        f"Target to cover: `{qualified_name}`\n"
        f"Missing line numbers: {missing_lines}\n"
    )

