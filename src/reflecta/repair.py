from pathlib import Path

from reflecta.llm import groq as groq_module
from reflecta.llm.groq import MODEL_FAST, MODEL_HARD
from reflecta.models import (
    GeneratedTest,
    RepairAttempt,
    RepairResult,
    RunResult,
    TargetStatus,
)
from reflecta.prompts import build_repair_prompt
from reflecta.runner import run_test_isolated


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
    relevant_source = extract_relevant_source(source, test.target.qualified_name)

    for attempt_num in range(1, max_repairs + 1):
        model = MODEL_FAST if attempt_num == 1 else MODEL_HARD
        prompt = build_repair_prompt(relevant_source, test.source_code, current_traceback)
        patched_source = groq.repair(prompt, model=model)

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
