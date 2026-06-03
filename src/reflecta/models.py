import enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


class TargetStatus(enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    KEPT = "kept"
    DISCARDED = "discarded"
    ESCALATED = "escalated"
    FAILED = "failed"
    # The generated file was structurally unrunnable (empty, no test, missing
    # import) and could not be regenerated into something coherent. Distinct
    # from FAILED (a valid test that genuinely couldn't be repaired) so the
    # report says *why* no test was produced.
    SKIPPED = "skipped"


class RepairResult(enum.Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass
class CoverageTarget:
    file_path: Path
    qualified_name: str
    missing_lines: list[int]
    priority: float = 0.0
    status: TargetStatus = TargetStatus.PENDING


@dataclass
class GeneratedTest:
    target: CoverageTarget
    test_file_path: Path
    source_code: str
    model_used: str
    assertion_count: int = 0
    # Number of generation LLM calls this test cost (1 normally, more when the
    # first draft was structurally invalid and had to be regenerated). The loop
    # charges the budget by this so regeneration is counted honestly.
    generation_calls: int = 1
    # Set when the final draft is still structurally unrunnable. The loop skips
    # the (pointless) repair path for these and marks the target SKIPPED.
    structural_error: str | None = None


@dataclass
class RepairAttempt:
    attempt_number: int
    traceback: str
    model_used: str
    result: RepairResult


@dataclass
class RunResult:
    passed: bool
    traceback: str
    duration: float
    # How the run ended, used by the loop to route failures. One of:
    #   ""               — passed
    #   "test_failure"   — a real assertion/exception failure (repair can help)
    #   "no_tests"       — pytest collected nothing (exit 5; nothing to repair)
    #   "collection_error" — import/collection failure (exit 2)
    #   "import_error"   — collection failed on a missing module (env problem)
    #   "timeout"        — the subprocess was killed
    failure_kind: str = ""


@dataclass
class RunReport:
    repo_path: Path
    started_at: datetime
    coverage_before: float
    coverage_after: float
    targets: list[CoverageTarget] = field(default_factory=list)
    tests_kept: int = 0
    tests_discarded: int = 0
    tests_skipped: int = 0
    repair_attempts_used: int = 0
    escalations_attempted: int = 0
    escalations_succeeded: int = 0
    budget: str = ""
    stop_reason: str = ""
