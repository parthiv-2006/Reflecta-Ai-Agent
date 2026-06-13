"""git_ops.py — a thin, mockable wrapper over the git CLI.

`reflecta ci` needs to put the accepted tests on a branch and push it. That is
deterministic plumbing, not feature logic, so it lives here behind small named
functions that the orchestration layer (``ci.py``) calls. Every function shells
out to ``git`` and raises ``GitError`` (carrying stderr) on failure, so a broken
checkout surfaces a clear message instead of a raw non-zero exit.

Nothing here touches the network except ``push``; tests drive the rest against a
real throwaway repo (fast, deterministic) and inject a fake for ``push``.
"""

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A git command exited non-zero. Carries the captured stderr tail."""


def _run(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git {' '.join(args)} failed: {tail}")
    return proc.stdout.strip()


def current_branch(repo: Path) -> str:
    return _run(["rev-parse", "--abbrev-ref", "HEAD"], repo)


def current_sha(repo: Path) -> str:
    return _run(["rev-parse", "HEAD"], repo)


def remote_url(repo: Path, remote: str = "origin") -> str:
    return _run(["remote", "get-url", remote], repo)


def detect_default_branch(repo: Path, remote: str = "origin") -> str:
    """Best-effort name of the remote's default branch.

    Reads ``origin/HEAD`` when set; falls back to ``main`` then ``master`` if it
    can be resolved locally, else ``main``. Used as the PR base when the caller
    does not pass one explicitly.
    """
    try:
        ref = _run(["symbolic-ref", f"refs/remotes/{remote}/HEAD"], repo)
        return ref.rsplit("/", 1)[-1]
    except GitError:
        pass
    for name in ("main", "master"):
        try:
            _run(["rev-parse", "--verify", f"refs/remotes/{remote}/{name}"], repo)
            return name
        except GitError:
            continue
    return "main"


_OWNER_REPO = re.compile(r"[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def parse_owner_repo(url: str) -> tuple[str, str]:
    """Pull ``(owner, repo)`` out of an https/ssh/proxy remote URL.

    Handles ``git@github.com:owner/repo.git``, ``https://github.com/owner/repo``,
    and proxy forms like ``http://x@host/git/owner/repo`` — anything ending in
    ``…/owner/repo[.git]``.
    """
    m = _OWNER_REPO.search(url.strip())
    if not m:
        raise GitError(f"could not parse owner/repo from remote URL: {url!r}")
    return m.group("owner"), m.group("repo")


def checkout_new_branch(repo: Path, branch: str, base: str) -> None:
    """Create (or reset) ``branch`` at ``base`` and switch to it.

    ``checkout -B`` carries untracked files (the generated tests) onto the new
    branch, which is exactly what we want: the accepted tests are untracked in
    the working tree and follow us onto the PR branch.
    """
    _run(["checkout", "-B", branch, base], repo)


def checkout(repo: Path, ref: str) -> None:
    _run(["checkout", ref], repo)


def stage(repo: Path, paths: list[str]) -> None:
    """Stage exactly ``paths`` — never ``git add .``.

    Restricting the pathspec is a safety boundary: ci only ever stages the
    generated-tests directory, so a human-written file can never be swept into
    an automated commit (hard rule #1).
    """
    _run(["add", "--", *paths], repo)


def has_staged_changes(repo: Path) -> bool:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    # exit 1 = differences staged, 0 = none. Any other code is a real error.
    if proc.returncode not in (0, 1):
        raise GitError((proc.stderr or "git diff --cached failed").strip())
    return proc.returncode == 1


def commit(repo: Path, message: str) -> str:
    """Commit the staged changes and return the new commit sha.

    Uses an explicit author/committer identity in the command env so ci works on
    a fresh CI runner where ``user.name``/``user.email`` are unset (git would
    otherwise abort the commit).
    """
    _run(
        [
            "-c",
            "user.name=reflecta-bot",
            "-c",
            "user.email=reflecta-bot@users.noreply.github.com",
            "commit",
            "-m",
            message,
        ],
        repo,
    )
    return current_sha(repo)


def push(
    repo: Path, branch: str, remote: str = "origin", *, force: bool = True
) -> None:
    """Push ``branch`` to ``remote``.

    Force-with-lease by default: ci owns its branch (``reflecta/auto-tests`` by
    default), and a re-run replaces the previous auto-commit rather than piling
    up history, while ``--force-with-lease`` still refuses to clobber a branch
    someone else advanced.
    """
    args = ["push", "--set-upstream", remote, branch]
    if force:
        args.insert(1, "--force-with-lease")
    _run(args, repo)
