from reflecta.gates import passes_delta_gate
from reflecta.models import GeneratedTest, TargetStatus


def process_test(test: GeneratedTest, *, coverage_before: float, coverage_after: float) -> str:
    """Apply the coverage-delta gate to a passing test.

    Returns "kept" if coverage strictly rose (file left on disk),
    or "discarded" if coverage did not rise (file deleted, target marked DISCARDED).
    """
    if passes_delta_gate(coverage_before, coverage_after):
        test.target.status = TargetStatus.KEPT
        return "kept"

    test.test_file_path.unlink(missing_ok=True)
    test.target.status = TargetStatus.DISCARDED
    return "discarded"
