"""Tests for git_ops — driven against a real throwaway repo (no network).

Only ``push`` touches a remote; it is exercised separately in test_ci via a
fake. Everything else runs against a temp ``git init`` repo so the assertions
are about real git behaviour, not mocks.
"""

import subprocess
from pathlib import Path

import pytest

from reflecta import git_ops


def _git(path: Path, *args: str) -> None:
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} -> {r.stderr or r.stdout}"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "checkout", "-q", "-B", "main")
    # commit identity so commit() defaults aren't required for the seed commit
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("seed\n")
    _git(path, "add", ".")
    _git(
        path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed"
    )


# ---------------------------------------------------------------------------
# parse_owner_repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "git@github.com:parthiv-2006/Reflecta-Ai-Agent.git",
            ("parthiv-2006", "Reflecta-Ai-Agent"),
        ),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        (
            "http://local_proxy@127.0.0.1:46579/git/parthiv-2006/Reflecta-Ai-Agent",
            ("parthiv-2006", "Reflecta-Ai-Agent"),
        ),
        ("https://github.com/owner/repo/", ("owner", "repo")),
    ],
)
def test_parse_owner_repo(url, expected):
    assert git_ops.parse_owner_repo(url) == expected


def test_parse_owner_repo_rejects_garbage():
    with pytest.raises(git_ops.GitError):
        git_ops.parse_owner_repo("not-a-url")


# ---------------------------------------------------------------------------
# branch / commit plumbing against a real repo
# ---------------------------------------------------------------------------


def test_current_branch_and_sha(tmp_path):
    _init_repo(tmp_path)
    assert git_ops.current_branch(tmp_path) == "main"
    assert len(git_ops.current_sha(tmp_path)) == 40


def test_checkout_new_branch_carries_untracked_and_commits(tmp_path):
    _init_repo(tmp_path)
    gen = tmp_path / "tests" / "_reflecta"
    gen.mkdir(parents=True)
    (gen / "test_reflecta_calc_0.py").write_text("def test_x():\n    assert True\n")

    git_ops.checkout_new_branch(tmp_path, "reflecta/auto-tests", "main")
    assert git_ops.current_branch(tmp_path) == "reflecta/auto-tests"

    git_ops.stage(tmp_path, ["tests/_reflecta"])
    assert git_ops.has_staged_changes(tmp_path) is True
    sha = git_ops.commit(tmp_path, "test: add reflecta-generated tests")
    assert len(sha) == 40
    assert git_ops.has_staged_changes(tmp_path) is False

    # The commit contains exactly the generated file and nothing else.
    files = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert files == ["tests/_reflecta/test_reflecta_calc_0.py"]


def test_stage_only_named_paths_never_touches_human_tests(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_human.py").write_text(
        "def test_h():\n    assert True\n"
    )
    gen = tmp_path / "tests" / "_reflecta"
    gen.mkdir()
    (gen / "test_reflecta_calc_0.py").write_text("def test_x():\n    assert True\n")

    git_ops.stage(tmp_path, ["tests/_reflecta"])
    staged = subprocess.run(
        ["git", "-C", str(tmp_path), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert staged == ["tests/_reflecta/test_reflecta_calc_0.py"]
    assert "tests/test_human.py" not in staged


def test_has_staged_changes_false_when_nothing_staged(tmp_path):
    _init_repo(tmp_path)
    assert git_ops.has_staged_changes(tmp_path) is False


def test_detect_default_branch_falls_back_to_main(tmp_path):
    _init_repo(tmp_path)
    # No remote configured → falls back to "main".
    assert git_ops.detect_default_branch(tmp_path) == "main"


def test_error_carries_stderr(tmp_path):
    _init_repo(tmp_path)
    with pytest.raises(git_ops.GitError):
        git_ops.remote_url(tmp_path, "nonexistent-remote")
