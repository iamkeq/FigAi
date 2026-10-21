from __future__ import annotations

import json
from types import SimpleNamespace

from figai.agent.tools import ToolRegistry
from figai.tools.version_info import register_version_info_tool


class FakeConfig:
    class settings:  # noqa: N801
        github_token = None


def make_runner(log_out="f3079bd0aaaa\nMerge pull request #8\n2026-10-21T18:00:00-04:00", remote="https://github.com/iamkeq/FigAi.git"):
    def run(cmd, **kwargs):
        if cmd[:2] == ["git", "log"]:
            return SimpleNamespace(stdout=log_out + "\n")
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(stdout="main\n")
        if cmd[:2] == ["git", "config"]:
            return SimpleNamespace(stdout=remote + "\n")
        raise AssertionError(cmd)

    return run


class FakeResponse:
    def __init__(self, data) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, data) -> None:
        self.data = data

    def get(self, url, *, headers, timeout):
        return FakeResponse(self.data)


def test_version_info_reports_commit_and_pr() -> None:
    registry = ToolRegistry()
    register_version_info_tool(
        registry, FakeConfig(), runner=make_runner(), http=FakeHttp([{"number": 8, "labels": [{"name": "enhancement"}]}])
    )

    result = json.loads(registry.execute("version_info", "{}"))

    assert result == {
        "branch": "main",
        "commit": "f3079bd0",
        "subject": "Merge pull request #8",
        "committed_at": "2026-10-21T18:00:00-04:00",
        "pr": {"number": 8, "labels": ["enhancement"]},
    }


def test_version_info_pr_is_none_when_not_found() -> None:
    registry = ToolRegistry()
    register_version_info_tool(registry, FakeConfig(), runner=make_runner(), http=FakeHttp([]))

    assert json.loads(registry.execute("version_info", "{}"))["pr"] is None


def test_version_info_reports_error_when_git_unavailable() -> None:
    def failing(cmd, **kwargs):
        raise OSError("git not found")

    registry = ToolRegistry()
    register_version_info_tool(registry, FakeConfig(), runner=failing)

    assert "error" in json.loads(registry.execute("version_info", "{}"))
