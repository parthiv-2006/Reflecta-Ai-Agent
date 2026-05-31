
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
from reflecta.runner import run_test


def repair_test(
    test: GeneratedTest,
    result: RunResult,
    source: str,
    *,
    max_repairs: int = 2,
    groq_client=None,
) -> tuple[GeneratedTest | None, list[RepairAttempt]]:
    """groq_client may be the real groq module or a test double with .repair(prompt, model=)."""
    groq = groq_client if groq_client is not None else groq_module
    attempts: list[RepairAttempt] = []
    current_traceback = result.traceback

    for attempt_num in range(1, max_repairs + 1):
        model = MODEL_FAST if attempt_num == 1 else MODEL_HARD
        prompt = build_repair_prompt(source, test.source_code, current_traceback)
        patched_source = groq.repair(prompt, model=model)

        test.test_file_path.write_text(patched_source)

        run_result = run_test(test.test_file_path, test.test_file_path.parent)

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
