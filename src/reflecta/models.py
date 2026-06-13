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
    # True for module entrypoints (``main`` / functions invoked under an
    # ``if __name__ == "__main__"`` guard). These parse argv and drive the whole
    # program, so they are near-impossible to unit-test and waste budget. The
    # loop deprioritizes them and, by default, skips them entirely.
    is_entrypoint: bool = False
    # Static, no-LLM testability verdict ("testable" | "risky" | "blocked")
    # plus a human reason. Computed in extract_targets; the loop skips
    # blocked/risky targets before spending any LLM quota. See testability.py.
    testability: str = "testable"
    testability_reason: str = ""


@dataclass
class MutationResult:
    """Outcome of the mutation (honesty) gate for one kept test.

    A mutant is a single-operator perturbation of the target function's code
    (a flipped comparison, a swapped ``+``/``-``, a negated boolean). It is
    *killed* if the generated test fails against the mutated code and *survives*
    if the test still passes — a survivor means the test exercised the line
    without verifying its behaviour. ``total == 0`` means the function had no
    mutable surface, which scores 1.0 (a test can't be faulted for code it
    cannot meaningfully break).
    """

    killed: int
    total: int
    survivors: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return 1.0 if self.total == 0 else self.killed / self.total


@dataclass
class GeneratedTest:
    target: CoverageTarget
    test_file_path: Path
    source_code: str
    model_used: str
    assertion_count: int = 0
    # Fraction of mutants the kept test killed (None when the mutation gate is
    # disabled or the test never reached it). Recorded for the report.
    mutation_score: float | None = None
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
    # Targets whose repair budget exhausted but whose passing test functions
    # were salvaged (failing ones AST-stripped) and went on to clear the gates.
    tests_salvaged: int = 0
    # Mutation (honesty) gate accounting. Only populated when --mutation is on.
    #   tests_mutation_tested  — kept tests that passed the mutation gate
    #   tests_failed_mutation  — tests discarded for too low a mutation score
    #   mutants_killed/total   — aggregate kill ratio across all scored tests
    tests_mutation_tested: int = 0
    tests_failed_mutation: int = 0
    mutants_killed: int = 0
    mutants_total: int = 0
    repair_attempts_used: int = 0
    escalations_attempted: int = 0
    escalations_succeeded: int = 0
    budget: str = ""
    stop_reason: str = ""
    # LLM call counts per provider — incremented by loop.py at each call site
    # so the JSON report carries the counts without extra instrumentation.
    llm_calls_gemini: int = 0
    llm_calls_groq: int = 0
    llm_calls_claude: int = 0
