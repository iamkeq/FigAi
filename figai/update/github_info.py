"""Looks up which merged pull request (and its labels) a given commit belongs
to, via GitHub's REST API, so Fig can tell the owner what actually changed
after it updates itself."""

from __future__ import annotations

import re
from typing import Any, Callable

import requests

from figai.process_utils import quiet_run

GITHUB_API = "https://api.github.com"
TIMEOUT_SECONDS = 10
RUNNER_TIMEOUT_SECONDS = 15

Runner = Callable[..., Any]

_REMOTE_URL_PATTERN = re.compile(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$")


def get_head_sha(runner: Runner = quiet_run, cwd: str = ".") -> str:
    result = runner(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, cwd=cwd, timeout=RUNNER_TIMEOUT_SECONDS
    )
    return result.stdout.strip()


def get_remote_owner_repo(runner: Runner = quiet_run, cwd: str = ".") -> tuple[str, str] | None:
    result = runner(
        ["git", "config", "--get", "remote.origin.url"],
        check=True,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=RUNNER_TIMEOUT_SECONDS,
    )
    match = _REMOTE_URL_PATTERN.search(result.stdout.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def find_merged_pr_for_commit(
    owner: str, repo: str, sha: str, token: str | None = None, http: Any = requests
) -> dict[str, Any] | None:
    """Returns {"number": int, "labels": [str, ...]} for the first PR this
    commit belongs to, or None if there isn't one or the lookup fails for any
    reason (no token on a private repo, rate limiting, network error, ...) --
    this is a nice-to-have detail, never something worth failing an update over."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = http.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}/pulls", headers=headers, timeout=TIMEOUT_SECONDS
        )
        response.raise_for_status()
        pulls = response.json()
    except Exception:  # noqa: BLE001
        return None

    if not pulls:
        return None
    pr = pulls[0]
    return {"number": pr["number"], "labels": [label["name"] for label in pr.get("labels", [])]}
