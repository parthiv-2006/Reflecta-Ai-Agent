"""Claude escalation for targets that Groq repair cannot fix.

Gives Claude real tools (read_file, write_test, run_test) and runs a bounded
tool-use loop. Reserved for targets marked ESCALATED after repair exhaustion.
Drawing on a Pro/Max subscription or API key via ANTHROPIC_API_KEY — never the
main loop.

We call the Messages API directly over ``httpx`` rather than via the
``anthropic`` SDK. Two reasons, both learned the hard way on Windows:

1. The SDK's ``messages.create`` can block indefinitely here and its own
   ``timeout=`` never fires, whereas a plain ``httpx`` request honours its
   timeout and returns in well under a second.
2. ``ANTHROPIC_API_KEY`` may hold an OAuth *subscription* token
   (``sk-ant-oat01-…``) rather than a console API key (``sk-ant-api03-…``).
   Those authenticate completely differently — Bearer + an OAuth beta header +
   the Claude Code system prompt — which the SDK's ``x-api-key`` path can't do.
   ``_ClaudeClient`` auto-detects the token type and sets the right headers.
"""
from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import httpx

from reflecta.models import GeneratedTest, RunResult, TargetStatus
from reflecta.runner import run_test_isolated

logger = logging.getLogger("reflecta")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_OAUTH_BETA = "oauth-2025-04-20"
# Required verbatim as the first system block when authenticating with an OAuth
# subscription token; harmless for console API keys. Without it the API rejects
# subscription tokens with a 429.
_CLAUDE_CODE_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."
# Per-round-trip wall-clock deadline. httpx enforces this reliably on Windows,
# so no ThreadPoolExecutor hack is needed.
_ROUND_TRIP_TIMEOUT_S = 55.0


def _serialize_block(block: object) -> dict:
    """Turn an assistant content block (SDK-shaped namespace) into API JSON.

    The tool-use loop appends ``response.content`` blocks back into the message
    history; before re-sending we must render them as plain dicts. Already-dict
    blocks (and tool_result messages built by the loop) pass through untouched.
    """
    if isinstance(block, dict):
        return block
    kind = getattr(block, "type", None)
    if kind == "text":
        return {"type": "text", "text": block.text}
    if kind == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    raise ValueError(f"cannot serialize content block of type {kind!r}")


def _serialize_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            content = [_serialize_block(b) for b in content]
        out.append({"role": msg["role"], "content": content})
    return out


def _parse_response(data: dict) -> SimpleNamespace:
    """Render a Messages API JSON response in the SDK-compatible shape the loop
    expects: ``.stop_reason`` plus ``.content`` blocks with attribute access."""
    blocks: list[SimpleNamespace] = []
    for raw in data.get("content", []):
        if raw.get("type") == "text":
            blocks.append(SimpleNamespace(type="text", text=raw.get("text", "")))
        elif raw.get("type") == "tool_use":
            blocks.append(
                SimpleNamespace(
                    type="tool_use",
                    id=raw["id"],
                    name=raw["name"],
                    input=raw.get("input", {}),
                )
            )
    return SimpleNamespace(stop_reason=data.get("stop_reason"), content=blocks)


class _ClaudeClient:
    """Minimal Messages API client over httpx, mirroring the slice of the
    anthropic SDK surface the loop uses (``client.messages.create(**kwargs)``).

    Auto-detects an OAuth subscription token vs a console API key and sets the
    appropriate auth headers.
    """

    def __init__(self, token: str | None = None, *, timeout: float = _ROUND_TRIP_TIMEOUT_S):
        token = token or os.environ.get("ANTHROPIC_API_KEY")
        if not token:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file "
                "(see .env.example) or export it before running escalation."
            )
        headers = {
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if token.startswith("sk-ant-oat"):
            # OAuth subscription token (e.g. from `claude setup-token`).
            headers["authorization"] = f"Bearer {token}"
            headers["anthropic-beta"] = _OAUTH_BETA
        else:
            # Console API key (sk-ant-api03-…).
            headers["x-api-key"] = token
        self._client = httpx.Client(timeout=httpx.Timeout(timeout), headers=headers)
        self.messages = self

    def create(self, *, model, max_tokens, messages, tools=None, system=None) -> SimpleNamespace:
        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": _serialize_messages(messages),
        }
        # The Claude Code system prompt must lead the system blocks for OAuth
        # tokens; it is harmless for API keys.
        system_blocks = [{"type": "text", "text": _CLAUDE_CODE_SYSTEM}]
        if system:
            system_blocks.append({"type": "text", "text": system})
        body["system"] = system_blocks
        if tools:
            body["tools"] = tools
        resp = self._client.post(_API_URL, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Claude API {resp.status_code}: {resp.text[:300]}")
        return _parse_response(resp.json())


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
        claude_client = _ClaudeClient()

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
