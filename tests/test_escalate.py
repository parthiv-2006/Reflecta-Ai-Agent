"""Tests for the Claude Agent SDK escalation module (TDD — written before implementation)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reflecta.models import CoverageTarget, GeneratedTest, RunResult, TargetStatus


# ---------------------------------------------------------------------------
# Helpers — build fake anthropic response objects
# ---------------------------------------------------------------------------

def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(tool_id: str, name: str, input_: dict):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=input_)


def _response(stop_reason: str, content: list):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


def _make_client(*responses):
    """Build a mock anthropic client that returns *responses* in sequence."""
    client = MagicMock()
    client.messages.create.side_effect = list(responses)
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path):
    """Minimal repo: a source file + tests/_reflecta dir."""
    src = tmp_path / "mymod.py"
    src.write_text("def add(a, b):\n    return a + b\n")
    reflecta_dir = tmp_path / "tests" / "_reflecta"
    reflecta_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def failing_test(tmp_repo):
    target = CoverageTarget(
        file_path=tmp_repo / "mymod.py",
        qualified_name="mymod.add",
        missing_lines=[2],
    )
    test_path = tmp_repo / "tests" / "_reflecta" / "test_reflecta_mymod_0.py"
    test_path.write_text("def test_add():\n    assert 1 == 2\n")
    return GeneratedTest(
        target=target,
        test_file_path=test_path,
        source_code=test_path.read_text(),
        model_used="gemini",
        assertion_count=1,
    )


@pytest.fixture
def failing_result():
    return RunResult(passed=False, traceback="AssertionError: assert 1 == 2", duration=0.1)


# ---------------------------------------------------------------------------
# Import guard — escalate must be importable (will fail until module exists)
# ---------------------------------------------------------------------------

def test_escalate_module_imports():
    from reflecta import escalate  # noqa: F401


# ---------------------------------------------------------------------------
# Core behaviour tests
# ---------------------------------------------------------------------------

def test_escalate_succeeds_when_claude_writes_and_runs_passing_test(
    tmp_repo, failing_test, failing_result
):
    """Claude writes a fixed test then calls run_test → escalation succeeds."""
    from reflecta.escalate import escalate_target

    good_source = "from mymod import add\ndef test_add():\n    assert add(1, 2) == 3\n"

    responses = [
        # Round 1: Claude calls write_test then run_test
        _response("tool_use", [
            _tool_use_block("t1", "write_test", {"content": good_source}),
            _tool_use_block("t2", "run_test", {}),
        ]),
        # Round 2: Claude sees PASSED, wraps up
        _response("end_turn", [_text_block("Done, test is passing.")]),
    ]
    client = _make_client(*responses)

    with patch("reflecta.runner.run_test_isolated") as mock_run:
        mock_run.return_value = RunResult(passed=True, traceback="", duration=0.05)
        result = escalate_target(
            failing_test, failing_result, "def add(a,b): return a+b",
            repo_path=tmp_repo, max_iters=3, claude_client=client,
        )

    assert result is not None
    assert result.model_used == "claude-opus-4-8"
    assert failing_test.target.status == TargetStatus.KEPT


def test_escalate_returns_none_when_max_iters_exhausted(
    tmp_repo, failing_test, failing_result
):
    """Claude never fixes the test — returns None after max_iters, marks ESCALATED."""
    from reflecta.escalate import escalate_target

    # Each round: Claude calls run_test but it always fails
    one_round = _response("tool_use", [
        _tool_use_block("t1", "run_test", {}),
    ])
    client = _make_client(one_round, one_round, one_round)  # 3 rounds = max_iters

    with patch("reflecta.runner.run_test_isolated") as mock_run:
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        result = escalate_target(
            failing_test, failing_result, "source",
            repo_path=tmp_repo, max_iters=3, claude_client=client,
        )

    assert result is None
    assert failing_test.target.status == TargetStatus.ESCALATED


def test_escalate_marks_escalated_on_end_turn_without_passing_test(
    tmp_repo, failing_test, failing_result
):
    """Claude ends the turn without running a passing test → ESCALATED."""
    from reflecta.escalate import escalate_target

    client = _make_client(
        _response("end_turn", [_text_block("I cannot fix this test.")])
    )

    with patch("reflecta.runner.run_test_isolated") as mock_run:
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        result = escalate_target(
            failing_test, failing_result, "source",
            repo_path=tmp_repo, max_iters=3, claude_client=client,
        )

    assert result is None
    assert failing_test.target.status == TargetStatus.ESCALATED


def test_escalate_read_file_within_repo(tmp_repo, failing_test, failing_result):
    """Claude can read a file inside the repo via read_file tool."""
    from reflecta.escalate import escalate_target

    captured: list[str] = []

    def mock_create(**kwargs):
        messages = kwargs.get("messages", [])
        # After round 1 (read_file), check the tool result was the file content
        if len(messages) >= 3:  # user, assistant(tool_use), user(tool_result)
            tool_result_msg = messages[-1]
            if isinstance(tool_result_msg.get("content"), list):
                for part in tool_result_msg["content"]:
                    if part.get("type") == "tool_result":
                        captured.append(part["content"])
            return _response("end_turn", [_text_block("done")])
        return _response("tool_use", [
            _tool_use_block("t1", "read_file", {"path": "mymod.py"}),
        ])

    client = MagicMock()
    client.messages.create.side_effect = lambda **kw: mock_create(**kw)

    with patch("reflecta.runner.run_test_isolated") as mock_run:
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        escalate_target(
            failing_test, failing_result, "source",
            repo_path=tmp_repo, max_iters=3, claude_client=client,
        )

    assert len(captured) >= 1
    assert "def add" in captured[0]


def test_escalate_read_file_outside_repo_is_blocked(tmp_repo, failing_test, failing_result):
    """read_file for a path outside the repo returns an error, not the file content."""
    from reflecta.escalate import escalate_target

    captured: list[str] = []

    def mock_create(**kwargs):
        messages = kwargs.get("messages", [])
        if len(messages) >= 3:
            tool_result_msg = messages[-1]
            if isinstance(tool_result_msg.get("content"), list):
                for part in tool_result_msg["content"]:
                    if part.get("type") == "tool_result":
                        captured.append(part["content"])
            return _response("end_turn", [_text_block("done")])
        return _response("tool_use", [
            _tool_use_block("t1", "read_file", {"path": "../../etc/passwd"}),
        ])

    client = MagicMock()
    client.messages.create.side_effect = lambda **kw: mock_create(**kw)

    with patch("reflecta.runner.run_test_isolated") as mock_run:
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        escalate_target(
            failing_test, failing_result, "source",
            repo_path=tmp_repo, max_iters=3, claude_client=client,
        )

    assert len(captured) >= 1
    assert "Error" in captured[0]


def test_escalate_write_test_updates_file_on_disk(tmp_repo, failing_test, failing_result):
    """write_test tool actually writes the new content to the test file path."""
    from reflecta.escalate import escalate_target

    new_content = "def test_ok():\n    assert True\n"
    responses = [
        _response("tool_use", [
            _tool_use_block("t1", "write_test", {"content": new_content}),
        ]),
        _response("end_turn", [_text_block("done")]),
    ]
    client = _make_client(*responses)

    with patch("reflecta.runner.run_test_isolated") as mock_run:
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        escalate_target(
            failing_test, failing_result, "source",
            repo_path=tmp_repo, max_iters=3, claude_client=client,
        )

    assert failing_test.test_file_path.read_text() == new_content


def test_escalate_raises_import_error_without_anthropic(
    tmp_repo, failing_test, failing_result
):
    """If anthropic is not installed and no client provided, raises ImportError."""
    from reflecta.escalate import escalate_target

    with patch.dict("sys.modules", {"anthropic": None}):
        with pytest.raises(ImportError, match="anthropic"):
            escalate_target(
                failing_test, failing_result, "source",
                repo_path=tmp_repo, claude_client=None,
            )


def test_escalate_initial_prompt_contains_traceback(tmp_repo, failing_test, failing_result):
    """The first message sent to Claude includes the failing traceback."""
    from reflecta.escalate import escalate_target

    captured_messages: list = []

    def capture(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        return _response("end_turn", [_text_block("done")])

    client = MagicMock()
    client.messages.create.side_effect = lambda **kw: capture(**kw)

    with patch("reflecta.runner.run_test_isolated") as mock_run:
        mock_run.return_value = RunResult(passed=False, traceback="err", duration=0.0)
        escalate_target(
            failing_test, failing_result, "source",
            repo_path=tmp_repo, max_iters=3, claude_client=client,
        )

    first_user_content = captured_messages[0][0]["content"]
    assert "AssertionError: assert 1 == 2" in first_user_content
