"""version_info: which commit Fig is running, and which merged PR (and labels)
it came from. One read-only call, so "what was the last PR you pulled?" doesn't
send the model hunting through unrelated tools."""

from __future__ import annotations

import json
from typing import Any, Callable

import requests

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config
from figai.process_utils import quiet_run
from figai.update.github_info import find_merged_pr_for_commit, get_remote_owner_repo

RUNNER_TIMEOUT_SECONDS = 15


def _git(runner: Callable[..., Any], *args: str) -> str:
    result = runner(["git", *args], check=True, capture_output=True, text=True, timeout=RUNNER_TIMEOUT_SECONDS)
    return result.stdout.strip()


def register_version_info_tool(
    registry: ToolRegistry, config: Config, runner: Callable[..., Any] = quiet_run, http: Any = requests
) -> None:
    def _version(_arguments: dict[str, Any]) -> str:
        try:
            sha, subject, committed_at = _git(runner, "log", "-1", "--format=%H%n%s%n%cI").split("\n", 2)
            branch = _git(runner, "rev-parse", "--abbrev-ref", "HEAD")
        except Exception as exc:  # noqa: BLE001 - not a git checkout, git missing, ...
            return json.dumps({"error": f"Could not read git history: {exc}"})

        pr = None
        try:
            owner_repo = get_remote_owner_repo(runner)
            if owner_repo:
                pr = find_merged_pr_for_commit(*owner_repo, sha, token=config.settings.github_token, http=http)
        except Exception:  # noqa: BLE001 - the PR detail is best-effort
            pr = None

        return json.dumps(
            {"branch": branch, "commit": sha[:8], "subject": subject, "committed_at": committed_at, "pr": pr}
        )

    registry.register(
        Tool(
            name="version_info",
            description=(
                "Report which code version Fig is running: the current commit, its message and date, the "
                "branch, and the merged GitHub PR number and labels it came from. Use this for questions like "
                "'what was the last PR you pulled' or 'what version are you on'."
            ),
            parameters={"type": "object", "properties": {}},
            handler=_version,
        )
    )
