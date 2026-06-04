from pathlib import Path

from reflecta.llm import groq as groq_module
from reflecta.llm.groq import MODEL_FAST, MODEL_HARD
from reflecta.llm.limits import (
    estimate_tokens,
    request_char_budget,
    request_token_budget,
)
from reflecta.llm.provider import RequestTooLarge
from reflecta.models import (
    GeneratedTest,
    RepairAttempt,
    RepairResult,
    RunResult,
    TargetStatus,
)
from reflecta.prompts import build_repair_prompt
from reflecta.runner import run_test_isolated

# Characters reserved for the repair prompt's fixed template (rules + labels).
_TEMPLATE_OVERHEAD_CHARS = 400


def _trim_traceback(traceback: str, max_chars: int) -> str:
    """Keep the most diagnostic part of a pytest traceback within ``max_chars``.

    The actual error (AssertionError / exception + the failing line) is at the
    *end* of pytest output, so we preserve the tail, plus a few head lines for
    context, and mark the elision.
    """
    traceback = traceback or ""
    if len(traceback) <= max_chars or max_chars <= 0:
        return traceback[:max_chars] if max_chars > 0 else ""
    lines = traceback.splitlines()
    head = lines[:4]
    tail = lines[-60:]
    joined = "\n".join(head + ["... [traceback trimmed] ..."] + tail)
    # If still too long, hard-keep the tail (where the error lives).
    return joined[-max_chars:]


def _budget_repair_prompt(
    source: str,
    test_source: str,
    traceback: str,
    *,
    model: str,
    qualified_name: str,
) -> str:
    """Build a repair prompt sized to ``model``'s per-minute token budget (TPM).

    Priority within the budget: the failing test (it's what we must fix) is kept
    whole, then the traceback tail (the error), then as much of the source as
    fits via AST extraction. A final token-estimate guard shrinks the source
    further (then the traceback) so the request never exceeds the model's TPM —
    which is what produced the HTTP 413 "request too large" failures.
    """
    avail = request_char_budget(model) - _TEMPLATE_OVERHEAD_CHARS
    if avail < 0:
        avail = 0

    # 1. Failing test — keep whole when possible (truncating it defeats repair).
    test_part = test_source
    if len(test_part) > avail:
        test_part = test_part[:avail]
    remaining = avail - len(test_part)

    # 2. Traceback — up to half of what's left, tail-biased.
    tb_part = _trim_traceback(traceback, max_chars=int(remaining * 0.5))
    remaining -= len(tb_part)

    # 3. Source — whatever budget remains, AST-extracted to the target function.
    source_part = (
        extract_relevant_source(source, qualified_name, max_chars=max(0, remaining))
        if remaining > 0
        else ""
    )

    prompt = build_repair_prompt(source_part, test_part, tb_part)

    # Final guard: ensure the whole assembled prompt fits the token budget.
    token_budget = request_token_budget(model)
    while estimate_tokens(prompt) > token_budget and source_part:
        source_part = source_part[: len(source_part) // 2]
        prompt = build_repair_prompt(source_part, test_part, tb_part)
    while estimate_tokens(prompt) > token_budget and len(tb_part) > 200:
        tb_part = tb_part[-(len(tb_part) // 2):]
        prompt = build_repair_prompt(source_part, test_part, tb_part)
    return prompt


def extract_relevant_source(source: str, qualified_name: str, max_chars: int = 15000) -> str:
    """Extract relevant parts of source code to prevent exceeding Groq payload limits."""
    if len(source) <= max_chars:
        return source

    import ast
    try:
        tree = ast.parse(source)
        lines = source.splitlines()
        target_lines = []

        class_map = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for lineno in range(node.lineno, node.end_lineno + 1):
                    class_map[lineno] = node.name

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                curr_class = class_map.get(node.lineno, "")
                curr_qual = f"{curr_class}.{node.name}" if curr_class else node.name
                if curr_qual == qualified_name:
                    start = max(0, node.lineno - 1)
                    end = node.end_lineno
                    target_lines = lines[start:end]
                    break

        top_lines = lines[:100]
        relevant = "\n".join(top_lines) + "\n\n... [truncated for context size] ...\n\n" + "\n".join(target_lines)
        if len(relevant) > max_chars:
            return relevant[:max_chars]
        return relevant
    except Exception:
        return source[:max_chars]


def repair_test(
    test: GeneratedTest,
    result: RunResult,
    source: str,
    *,
    repo_path: Path,
    max_repairs: int = 2,
    groq_client=None,
    python_exe: str | None = None,
) -> tuple[GeneratedTest | None, list[RepairAttempt]]:
    """groq_client may be the real groq module or a test double with .repair(prompt, model=).

    The repaired test is re-run with ``cwd=repo_path`` so import resolution matches
    the environment the loop uses everywhere else.
    """
    groq = groq_client if groq_client is not None else groq_module
    attempts: list[RepairAttempt] = []
    current_traceback = result.traceback
    qualified_name = test.target.qualified_name

    for attempt_num in range(1, max_repairs + 1):
        model = MODEL_FAST if attempt_num == 1 else MODEL_HARD

        # Size the prompt to this model's free-tier TPM so the request can't be
        # rejected with HTTP 413 "request too large".
        prompt = _budget_repair_prompt(
            source,
            test.source_code,
            current_traceback,
            model=model,
            qualified_name=qualified_name,
        )
        try:
            patched_source = groq.repair(prompt, model=model)
        except RequestTooLarge as exc:
            # The request is too big even after trimming. The 70B model has 2x
            # the TPM of the 8B, so re-trim and retry there once; if we're
            # already on it, record a clear failure rather than looping.
            if model != MODEL_HARD:
                model = MODEL_HARD
                prompt = _budget_repair_prompt(
                    source,
                    test.source_code,
                    current_traceback,
                    model=model,
                    qualified_name=qualified_name,
                )
                try:
                    patched_source = groq.repair(prompt, model=model)
                except RequestTooLarge as exc2:
                    attempts.append(
                        RepairAttempt(
                            attempt_number=attempt_num,
                            traceback=f"request too large for repair: {exc2}",
                            model_used=model,
                            result=RepairResult.FAIL,
                        )
                    )
                    break
            else:
                attempts.append(
                    RepairAttempt(
                        attempt_number=attempt_num,
                        traceback=f"request too large for repair: {exc}",
                        model_used=model,
                        result=RepairResult.FAIL,
                    )
                )
                break

        # utf-8: patched tests may contain non-ASCII; the platform default
        # (cp1252 on Windows) would raise UnicodeEncodeError. Matches generate.py.
        test.test_file_path.write_text(patched_source, encoding="utf-8")

        run_result = run_test_isolated(
            test.test_file_path, repo_path, python_exe=python_exe
        )

        if run_result.passed:
            repaired = GeneratedTest(
                target=test.target,
                test_file_path=test.test_file_path,
                source_code=patched_source,
                model_used=model,
                assertion_count=test.assertion_count,
            )
            attempts.append(
                RepairAttempt(
                    attempt_number=attempt_num,
                    traceback=current_traceback,
                    model_used=model,
                    result=RepairResult.PASS,
                )
            )
            test.target.status = TargetStatus.KEPT
            return repaired, attempts

        attempts.append(
            RepairAttempt(
                attempt_number=attempt_num,
                traceback=current_traceback,
                model_used=model,
                result=RepairResult.FAIL,
            )
        )
        current_traceback = run_result.traceback

    test.target.status = TargetStatus.FAILED
    return None, attempts
