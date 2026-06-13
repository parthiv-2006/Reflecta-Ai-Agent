"""ci.py — turn a finished run into a pull request of accepted tests.

This is the orchestration seam for ``reflecta ci``: it takes a completed
``RunReport`` plus the accepted test files already on disk, writes them to a
dedicated branch, and opens (or updates) a pull request describing the run. It
does not run the loop or change any gate — the CLI runs ``run_loop`` and hands
the report here.

Safety and idempotency are the whole point:
  • Only ``tests/_reflecta/`` is ever staged, so a human-written test can never
    be swept into an automated commit (hard rule #1).
  • When nothing was kept, no branch and no PR are created.
  • A re-run pushes onto the same branch and, finding the PR already open,
    reports an update instead of opening a duplicate.
  • ``--dry-run`` builds the full plan (branch, commit message, PR title/body)
    and returns it without touching git or the network.
"""

import contextlib
from dataclasses import dataclass
from pathlib import Path

from reflecta import git_ops
from reflecta.forge import PullRequest, PullRequestHost, host_from_repo
from reflecta.models import RunReport, TargetStatus

DEFAULT_HEAD_BRANCH = "reflecta/auto-tests"
_GENERATED_DIR = "tests/_reflecta"
_GENERATED_GLOB = "test_reflecta_*.py"


@dataclass
class CIPlan:
    """Everything ci would do, computable without any side effects."""

    head_branch: str
    base_branch: str
    commit_message: str
    pr_title: str
    pr_body: str
    test_files: list[Path]


@dataclass
class CIResult:
    status: str  # "opened" | "updated" | "no_tests" | "dry_run"
    pr: PullRequest | None
    plan: CIPlan | None


def kept_test_files(repo_path: Path) -> list[Path]:
    """The generated tests currently on disk — exactly the accepted ones.

    The loop unlinks every non-KEPT generated file, so whatever remains in
    ``tests/_reflecta/`` is the accepted set. Returned sorted for stable output.
    """
    d = Path(repo_path) / _GENERATED_DIR
    return sorted(d.glob(_GENERATED_GLOB)) if d.is_dir() else []


def _kept_targets(report: RunReport) -> list:
    return [t for t in report.targets if t.status == TargetStatus.KEPT]


def build_commit_message(report: RunReport) -> str:
    n = report.tests_kept
    delta = report.coverage_after - report.coverage_before
    return (
        f"test: add {n} reflecta-generated test{'s' if n != 1 else ''} "
        f"(+{delta:.1f}pp coverage)"
    )


def build_pr_title(report: RunReport) -> str:
    n = report.tests_kept
    return f"reflecta: {n} new test{'s' if n != 1 else ''} raising coverage to {report.coverage_after:.1f}%"


def build_pr_body(report: RunReport, test_files: list[Path], repo_path: Path) -> str:
    """Render a reviewer-friendly PR body from the run report.

    Uses only data already in the report: coverage delta, kept/discarded/repair
    counts, the aggregate mutation score (when the honesty gate ran), and the
    list of newly-covered targets. No secrets are referenced.
    """
    repo_path = Path(repo_path)
    delta = report.coverage_after - report.coverage_before
    kept_targets = _kept_targets(report)

    lines: list[str] = []
    n = report.tests_kept
    lines.append(f"## 🤖 reflecta added {n} test{'s' if n != 1 else ''}")
    lines.append("")
    lines.append(
        f"Coverage **{report.coverage_before:.1f}% → {report.coverage_after:.1f}%** "
        f"(**+{delta:.1f} pp**)."
    )
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Tests kept | {report.tests_kept} |")
    lines.append(f"| Tests discarded | {report.tests_discarded} |")
    lines.append(f"| Repairs used | {report.repair_attempts_used} |")
    if report.mutants_total or report.tests_failed_mutation:
        total = report.mutants_total
        pct = f"{report.mutants_killed / total * 100:.0f}%" if total else "n/a"
        lines.append(
            f"| Mutation gate | killed {report.mutants_killed}/{total} ({pct}) |"
        )
    lines.append("")

    if kept_targets:
        lines.append("### Tests added")
        for t in kept_targets:
            n_lines = len(t.missing_lines)
            try:
                rel = Path(t.file_path).resolve().relative_to(repo_path)
            except ValueError:
                rel = Path(t.file_path).name
            lines.append(
                f"- `{t.qualified_name}` in `{rel}` — "
                f"{n_lines} previously-uncovered line{'s' if n_lines != 1 else ''}"
            )
        lines.append("")

    if test_files:
        lines.append("<details><summary>Generated test files</summary>")
        lines.append("")
        for f in test_files:
            try:
                rel = Path(f).resolve().relative_to(repo_path)
            except ValueError:
                rel = Path(f).name
            lines.append(f"- `{rel}`")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    gates = "assertion + coverage-delta"
    if report.mutants_total or report.tests_failed_mutation:
        gates += " + mutation"
    lines.append(
        f"<sub>Generated by [reflecta]. Every kept test cleared the {gates} "
        f"gate(s): it contains real assertions and strictly raises coverage. "
        f"Review before merging.</sub>"
    )
    return "\n".join(lines)


def build_plan(
    report: RunReport,
    repo_path: Path,
    *,
    head_branch: str,
    base_branch: str,
) -> CIPlan:
    test_files = kept_test_files(repo_path)
    return CIPlan(
        head_branch=head_branch,
        base_branch=base_branch,
        commit_message=build_commit_message(report),
        pr_title=build_pr_title(report),
        pr_body=build_pr_body(report, test_files, repo_path),
        test_files=test_files,
    )


def submit(
    repo_path: Path,
    report: RunReport,
    *,
    head_branch: str = DEFAULT_HEAD_BRANCH,
    base_branch: str | None = None,
    dry_run: bool = False,
    host: PullRequestHost | None = None,
    git=git_ops,
) -> CIResult:
    """Commit the accepted tests to ``head_branch`` and open/update a PR.

    Returns a ``CIResult`` describing the outcome. ``git`` and ``host`` are
    injectable so the whole flow is unit-testable without git or the network.
    """
    repo_path = Path(repo_path).resolve()

    if report.tests_kept == 0 or not kept_test_files(repo_path):
        return CIResult(status="no_tests", pr=None, plan=None)

    base = base_branch or git.detect_default_branch(repo_path)
    plan = build_plan(report, repo_path, head_branch=head_branch, base_branch=base)

    if dry_run:
        return CIResult(status="dry_run", pr=None, plan=plan)

    # Branch off the exact commit reflecta ran against (not a branch name that
    # may not exist locally on a detached CI checkout); the generated tests are
    # untracked and follow the checkout onto the new branch.
    start_point = git.current_sha(repo_path)
    original = git.current_branch(repo_path)
    if original == "HEAD":  # detached — restore by sha
        original = start_point

    try:
        git.checkout_new_branch(repo_path, head_branch, start_point)
        git.stage(repo_path, [_GENERATED_DIR])
        if not git.has_staged_changes(repo_path):
            return CIResult(status="no_tests", pr=None, plan=plan)
        git.commit(repo_path, plan.commit_message)
        git.push(repo_path, head_branch)
    finally:
        # Best effort: leave the user on the branch they started on.
        with contextlib.suppress(Exception):
            git.checkout(repo_path, original)

    host = host or host_from_repo(repo_path)
    existing = host.find_open_pr(head_branch)
    if existing is not None:
        return CIResult(status="updated", pr=existing, plan=plan)
    pr = host.open_pull_request(
        title=plan.pr_title, body=plan.pr_body, head=head_branch, base=base
    )
    return CIResult(status="opened", pr=pr, plan=plan)
