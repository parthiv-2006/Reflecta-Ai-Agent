"""
metrics.py — eval harness dataclasses.

EvalMetrics captures all per-fixture measurements from a single harness run.
MetricResult records the pass/fail verdict for one metric against its baseline spec.
EvalReport aggregates the results for one fixture run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class EvalMetrics:
    """All measurements captured from one fixture run."""

    fixture_name: str

    # Coverage
    coverage_before: float
    coverage_after: float
    coverage_delta: float  # coverage_after - coverage_before

    # Generation outcomes
    targets_attempted: int
    tests_accepted: int
    tests_discarded: int
    repair_attempts_used: int

    # Triage
    targets_skipped_blocked: int
    targets_skipped_risky: int
    targets_skipped_entrypoint: int

    # LLM calls (from RunReport fields added in Task E-3)
    llm_calls_gemini: int
    llm_calls_groq: int
    llm_calls_claude: int

    # Run metadata
    run_time_seconds: float
    stop_reason: str

    def to_dict(self) -> dict:
        """Serialise to a plain dict (JSON-round-trip safe)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvalMetrics":
        """Deserialise from a plain dict produced by ``to_dict``."""
        return cls(**d)


@dataclass
class MetricResult:
    """Pass/fail verdict for a single metric against its baseline spec."""

    name: str
    actual: float
    baseline: float  # the reference value (min, max, or exact)
    tolerance: float  # reserved for future use; 0.0 means exact/spec-bounded
    passed: bool
    message: str  # human-readable verdict line

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MetricResult":
        return cls(**d)


@dataclass
class EvalReport:
    """Aggregated results for one fixture run."""

    fixture_name: str
    metrics: EvalMetrics
    results: list[MetricResult] = field(default_factory=list)
    overall_passed: bool = False

    def to_dict(self) -> dict:
        return {
            "fixture_name": self.fixture_name,
            "metrics": self.metrics.to_dict(),
            "results": [r.to_dict() for r in self.results],
            "overall_passed": self.overall_passed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvalReport":
        metrics = EvalMetrics.from_dict(d["metrics"])
        results = [MetricResult.from_dict(r) for r in d.get("results", [])]
        return cls(
            fixture_name=d["fixture_name"],
            metrics=metrics,
            results=results,
            overall_passed=d.get("overall_passed", False),
        )
