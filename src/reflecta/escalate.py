"""Claude Agent SDK escalation for targets that Groq repair cannot fix.

Gives Claude real tools (read_file, write_test, run_test) and runs a bounded
tool-use loop. Reserved for targets marked ESCALATED after repair exhaustion.
Drawing on Pro/Max subscription via ANTHROPIC_API_KEY — never the main loop.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

from reflecta.models import GeneratedTest, RunResult, TargetStatus
from reflecta.runner import run_test_isolated

logger = logging.getLogger("reflecta")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096


def _tools() -> list[dict]:
    return [
        {
            "name": "read_file",
            "description": "Read the contents of a file in the repository.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repository root.",
                    }
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_test",
            "description": "Overwrite the failing test file with new content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Complete Python test file source code.",
                    }
                },
                "required": ["content"],
            },
        },
        {
            "name": "run_test",
            "description": (
                "Run the current test file and return PASSED or FAILED with traceback."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    ]


def _execute_tool(
    name: str,
    input_: dict,
    *,
    test: GeneratedTest,
    repo_path: Path,
) -> str:
    if name == "read_file":
        rel = input_.get("path", "")
        target = (repo_path / rel).resolve()
        if not str(target).startswith(str(repo_path.resolve())):
            return "Error: path is outside the repository root"
        if not target.exists():
            return f"Error: file not found: {rel}"
        return target.read_text(encoding="utf-8")

    if name == "write_test":
        content = input_.get("content", "")
        test.test_file_path.parent.mkdir(parents=True, exist_ok=True)
        test.test_file_path.write_text(content, encoding="utf-8")
        test.source_code = content
        try:
            ast.parse(content)
            return "Test file written successfully."
        except SyntaxError as exc:
            return f"Warning: written file has a syntax error: {exc}"

    if name == "run_test":
        result = run_test_isolated(test.test_file_path, repo_path)
        return "PASSED" if result.passed else f"FAILED\n{result.traceback}"

    return f"Error: unknown tool '{name}'"


def escalate_target(
    test: GeneratedTest,
    result: RunResult,
    source: str,
    *,
    repo_path: Path,
    max_iters: int = 3,
    claude_client=None,
) -> GeneratedTest | None:
    """Repair a failing test using a Claude tool-use loop.

    Returns a repaired GeneratedTest on success, or None if max_iters is
    exhausted without a passing test. Sets target.status to ESCALATED on
    failure so the caller can distinguish it from a plain FAILED target.
    """
    if claude_client is None:
        try:
            import anthropic

            claude_client = anthropic.Anthropic(timeout=60.0)
        except (ImportError, TypeError) as exc:
            raise ImportError(
                "The anthropic package is required for escalation. "
                "Install it with: pip install anthropic"
            ) from exc

    initial_prompt = (
        f"I have a failing pytest test that needs repair.\n"
        f"Test file path: {test.test_file_path}\n\n"
        f"## Failing test\n```python\n{test.source_code}\n```\n\n"
        f"## Traceback\n```\n{result.traceback}\n```\n\n"
        f"## Source under test\n```python\n{source}\n```\n\n"
        "Please fix the test so it passes. Use read_file to inspect other files "
        "if needed, write_test to update the test file, and run_test to verify it "
        "passes. The test must contain real, non-trivial assertions."
    )

    messages: list[dict] = [{"role": "user", "content": initial_prompt}]

    for iteration in range(max_iters):
        logger.debug("escalation iteration %d/%d for %s", iteration + 1, max_iters, test.target.qualified_name)

        try:
            response = claude_client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                tools=_tools(),
                messages=messages,
                timeout=60,  # 60s per round-trip; 600s SDK default is too long
            )
        except Exception as exc:
            logger.warning("escalation API call failed (iter %d): %s", iteration + 1, exc)
            break

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            final = run_test_isolated(test.test_file_path, repo_path)
            if final.passed:
                return _build_repaired(test)
            break

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        test_passed = False

        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_output = _execute_tool(block.name, block.input, test=test, repo_path=repo_path)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": tool_output}
            )
            if block.name == "run_test" and tool_output == "PASSED":
                test_passed = True

        messages.append({"role": "user", "content": tool_results})

        if test_passed:
            return _build_repaired(test)

    test.target.status = TargetStatus.ESCALATED
    return None


def _build_repaired(test: GeneratedTest) -> GeneratedTest:
    test.target.status = TargetStatus.KEPT
    return GeneratedTest(
        target=test.target,
        test_file_path=test.test_file_path,
        source_code=test.test_file_path.read_text(encoding="utf-8"),
        model_used=MODEL,
        assertion_count=test.assertion_count,
    )
