"""forge.py — open pull requests on the code host (GitHub).

`ci.py` produces a branch and a PR body; this module is the seam that turns that
into an actual pull request. It is deliberately small and host-shaped so the
rest of reflecta never imports an HTTP client or knows about GitHub's API: it
talks to a ``PullRequestHost`` with two methods — find an existing open PR for a
branch (idempotency) and open a new one.

GitHub is reached through the REST API over ``httpx`` (already a core
dependency — no new SDK). The ``httpx.Client`` is injectable, so tests drive a
transport stub and never touch the network. Auth is a ``GITHUB_TOKEN`` read from
the environment and sent as a bearer token; it is never logged or persisted.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from reflecta import git_ops

_GITHUB_API = "https://api.github.com"


class ForgeError(RuntimeError):
    """A code-host operation failed (auth, network, or API error)."""


@dataclass
class PullRequest:
    number: int
    url: str


class PullRequestHost(Protocol):
    """The minimal surface ci.py needs from a code host."""

    def find_open_pr(self, head_branch: str) -> PullRequest | None: ...

    def open_pull_request(
        self, *, title: str, body: str, head: str, base: str
    ) -> PullRequest: ...


class GitHubHost:
    """GitHub implementation of ``PullRequestHost`` over the REST API."""

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        *,
        api_url: str = _GITHUB_API,
        client: httpx.Client | None = None,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self._api = api_url.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def find_open_pr(self, head_branch: str) -> PullRequest | None:
        """Return the open PR whose head is ``head_branch``, or None.

        This is what makes ``reflecta ci`` idempotent: a re-run pushes new
        commits to the same branch and, finding the PR already open, reports an
        update instead of opening a duplicate.
        """
        url = f"{self._api}/repos/{self.owner}/{self.repo}/pulls"
        params = {"head": f"{self.owner}:{head_branch}", "state": "open"}
        try:
            resp = self._client.get(url, params=params, headers=self._headers())
        except httpx.HTTPError as exc:  # pragma: no cover - network failure path
            raise ForgeError(f"GitHub request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ForgeError(_describe(resp, "list pull requests"))
        data = resp.json()
        if not data:
            return None
        pr = data[0]
        return PullRequest(number=pr["number"], url=pr["html_url"])

    def open_pull_request(
        self, *, title: str, body: str, head: str, base: str
    ) -> PullRequest:
        url = f"{self._api}/repos/{self.owner}/{self.repo}/pulls"
        payload = {"title": title, "body": body, "head": head, "base": base}
        try:
            resp = self._client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:  # pragma: no cover - network failure path
            raise ForgeError(f"GitHub request failed: {exc}") from exc
        if resp.status_code not in (200, 201):
            raise ForgeError(_describe(resp, "open pull request"))
        pr = resp.json()
        return PullRequest(number=pr["number"], url=pr["html_url"])


def _describe(resp: httpx.Response, action: str) -> str:
    """Build an actionable error without leaking the token (it's in a header)."""
    detail = ""
    try:
        body = resp.json()
        detail = body.get("message", "") if isinstance(body, dict) else ""
    except Exception:
        detail = (resp.text or "").strip()[:200]
    return f"GitHub could not {action} (HTTP {resp.status_code}): {detail}"


def host_from_repo(
    repo_path: Path,
    *,
    token: str | None = None,
    client: httpx.Client | None = None,
    api_url: str = _GITHUB_API,
) -> GitHubHost:
    """Build a ``GitHubHost`` from the repo's ``origin`` remote and env token.

    The token comes from ``GITHUB_TOKEN`` (or ``GH_TOKEN``) — the same variable
    GitHub Actions injects — and is never written anywhere. Raises ForgeError
    with a clear remedy when it is missing.
    """
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise ForgeError(
            "GITHUB_TOKEN is not set. reflecta ci needs a token with 'pull "
            "request: write' scope to open the PR. In GitHub Actions, pass "
            "${{ secrets.GITHUB_TOKEN }}; locally, export a personal access token."
        )
    owner, repo = git_ops.parse_owner_repo(git_ops.remote_url(repo_path))
    return GitHubHost(owner, repo, token, api_url=api_url, client=client)
