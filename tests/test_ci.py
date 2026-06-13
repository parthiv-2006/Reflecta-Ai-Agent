"""Tests for ci.py — PR-body building and the submit() orchestration.

submit() is exercised with an injected fake git module and fake host, so the
control flow (no-tests short-circuit, dry-run, open vs update, branch restore,
staging only the generated dir) is verified without git or the network.
"""

from datetime import datetime

import pytest

from reflecta import ci
from reflecta.forge import PullRequest
from reflecta.models import CoverageTarget, RunReport, TargetStatus


# ---------------------------------------------------------------------------
# fixtures / fakes
# ---------------------------------------------------------------------------


def _report(tmp_path, *, kept=2, **kw) -> RunReport:
    targets = [
        CoverageTarget(
            file_path=tmp_path / "calc.py",
            qualified_name=f"calc.func_{i}",
            missing_lines=[10 + i, 11 + i],
            status=TargetStatus.KEPT,
        )
        for i in range(kept)
    ]
    return RunReport(
        repo_path=tmp_path,
        started_at=datetime.now(),
        coverage_before=70.0,
        coverage_after=85.5,
        targets=targets,
        tests_kept=kept,
        tests_discarded=1,
        repair_attempts_used=3,
        **kw,
    )


def _write_kept_files(tmp_path, n=2) -> None:
    d = tmp_path / "tests" / "_reflecta"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"test_reflecta_calc_{i}.py").write_text(
            "def test_x():\n    assert True\n"
        )


class FakeGit:
    """Records calls and simulates branch state without touching real git."""

    def __init__(self):
        self.calls = []
        self._branch = "feature-x"
        self._staged = True

    def detect_default_branch(self, repo, remote="origin"):
        return "main"

    def current_sha(self, repo):
        return "a" * 40

    def current_branch(self, repo):
        return self._branch

    def checkout_new_branch(self, repo, branch, base):
        self.calls.append(("checkout_new_branch", branch, base))
        self._branch = branch

    def checkout(self, repo, ref):
        self.calls.append(("checkout", ref))
        self._branch = ref

    def stage(self, repo, paths):
        self.calls.append(("stage", tuple(paths)))

    def has_staged_changes(self, repo):
        return self._staged

    def commit(self, repo, message):
        self.calls.append(("commit", message))
        return "b" * 40

    def push(self, repo, branch, remote="origin", *, force=True):
        self.calls.append(("push", branch))


class FakeHost:
    def __init__(self, existing=None):
        self.existing = existing
        self.opened = None

    def find_open_pr(self, head_branch):
        return self.existing

    def open_pull_request(self, *, title, body, head, base):
        self.opened = {"title": title, "body": body, "head": head, "base": base}
        return PullRequest(number=42, url="https://gh/pr/42")


# ---------------------------------------------------------------------------
# build_pr_body
# ---------------------------------------------------------------------------


def test_pr_body_contains_coverage_and_targets(tmp_path):
    report = _report(tmp_path, kept=2)
    body = ci.build_pr_body(report, ci.kept_test_files(tmp_path), tmp_path)
    assert "70.0% → 85.5%" in body
    assert "+15.5 pp" in body
    assert "calc.func_0" in body
    assert "Tests kept | 2" in body


def test_pr_body_shows_mutation_row_when_present(tmp_path):
    report = _report(tmp_path, kept=1, mutants_killed=4, mutants_total=5)
    body = ci.build_pr_body(report, [], tmp_path)
    assert "Mutation gate | killed 4/5 (80%)" in body
    assert "mutation" in body  # gate list in footer


def test_pr_body_omits_mutation_row_when_absent(tmp_path):
    report = _report(tmp_path, kept=1)
    body = ci.build_pr_body(report, [], tmp_path)
    assert "Mutation gate" not in body


def test_commit_and_title_pluralize(tmp_path):
    one = _report(tmp_path, kept=1)
    assert "1 reflecta-generated test " in ci.build_commit_message(one)
    assert "+15.5pp" in ci.build_commit_message(one)
    assert "1 new test " in ci.build_pr_title(one)


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


def test_submit_no_tests_short_circuits(tmp_path):
    report = _report(tmp_path, kept=0)
    report.targets = []
    res = ci.submit(tmp_path, report, git=FakeGit(), host=FakeHost())
    assert res.status == "no_tests"
    assert res.pr is None


def test_submit_dry_run_makes_no_side_effects(tmp_path):
    _write_kept_files(tmp_path, 2)
    report = _report(tmp_path, kept=2)
    git = FakeGit()
    host = FakeHost()
    res = ci.submit(tmp_path, report, dry_run=True, git=git, host=host)
    assert res.status == "dry_run"
    assert res.plan is not None
    assert res.plan.head_branch == ci.DEFAULT_HEAD_BRANCH
    assert res.plan.base_branch == "main"
    assert git.calls == []  # nothing committed or pushed
    assert host.opened is None


def test_submit_opens_pr_and_stages_only_generated_dir(tmp_path):
    _write_kept_files(tmp_path, 2)
    report = _report(tmp_path, kept=2)
    git = FakeGit()
    host = FakeHost(existing=None)

    res = ci.submit(
        tmp_path, report, head_branch="reflecta/auto-tests", git=git, host=host
    )

    assert res.status == "opened"
    assert res.pr == PullRequest(number=42, url="https://gh/pr/42")
    # staged exactly the generated dir, nothing else
    stage_calls = [c for c in git.calls if c[0] == "stage"]
    assert stage_calls == [("stage", ("tests/_reflecta",))]
    # committed, pushed, and restored the original branch
    kinds = [c[0] for c in git.calls]
    assert kinds == [
        "checkout_new_branch",
        "stage",
        "commit",
        "push",
        "checkout",
    ]
    assert git.calls[-1] == ("checkout", "feature-x")
    assert host.opened["head"] == "reflecta/auto-tests"
    assert host.opened["base"] == "main"


def test_submit_reports_update_when_pr_already_open(tmp_path):
    _write_kept_files(tmp_path, 1)
    report = _report(tmp_path, kept=1)
    git = FakeGit()
    host = FakeHost(existing=PullRequest(number=9, url="https://gh/pr/9"))

    res = ci.submit(tmp_path, report, git=git, host=host)

    assert res.status == "updated"
    assert res.pr.number == 9
    assert host.opened is None  # did NOT open a duplicate
    assert ("push", "reflecta/auto-tests") in git.calls


def test_submit_restores_branch_even_if_push_fails(tmp_path):
    _write_kept_files(tmp_path, 1)
    report = _report(tmp_path, kept=1)

    class BoomGit(FakeGit):
        def push(self, repo, branch, remote="origin", *, force=True):
            raise RuntimeError("network down")

    git = BoomGit()
    with pytest.raises(RuntimeError):
        ci.submit(tmp_path, report, git=git, host=FakeHost())
    # branch restored despite the failure
    assert git.calls[-1] == ("checkout", "feature-x")
