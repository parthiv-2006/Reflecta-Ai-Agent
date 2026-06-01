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


@dataclass
class RunReport:
    repo_path: Path
    started_at: datetime
    coverage_before: float
    coverage_after: float
    targets: list[CoverageTarget] = field(default_factory=list)
    tests_kept: int = 0
    tests_discarded: int = 0
    repair_attempts_used: int = 0
    escalations_attempted: int = 0
    escalations_succeeded: int = 0
    budget: str = ""
    stop_reason: str = ""
