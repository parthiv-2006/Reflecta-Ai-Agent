import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from reflecta.models import CoverageTarget, RunReport, TargetStatus


def _serialize(obj):
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, TargetStatus):
        return obj.value
    raise TypeError(f"Not serializable: {type(obj)}")


def write_report(report: RunReport, path: Path) -> None:
    data = asdict(report)
    path.write_text(json.dumps(data, default=_serialize, indent=2), encoding="utf-8")


def read_report(path: Path) -> RunReport:
    if not path.exists():
        raise FileNotFoundError(f"No report found at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))

    targets = [
        CoverageTarget(
            file_path=Path(t["file_path"]),
            qualified_name=t["qualified_name"],
            missing_lines=t["missing_lines"],
            priority=t["priority"],
            status=TargetStatus(t["status"]),
        )
        for t in data.get("targets", [])
    ]

    return RunReport(
        repo_path=Path(data["repo_path"]),
        started_at=datetime.fromisoformat(data["started_at"]),
        coverage_before=data["coverage_before"],
        coverage_after=data["coverage_after"],
        targets=targets,
        tests_kept=data["tests_kept"],
        tests_discarded=data["tests_discarded"],
        repair_attempts_used=data["repair_attempts_used"],
        budget=data.get("budget", ""),
        stop_reason=data.get("stop_reason", ""),
    )
