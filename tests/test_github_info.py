from __future__ import annotations

from types import SimpleNamespace

from figai.update.announcement import build_startup_announcement
from figai.update.github_info import find_merged_pr_for_commit, get_head_sha, get_remote_owner_repo


def fake_runner(stdout: str):
    def run(cmd, **kwargs):
        return SimpleNamespace(stdout=stdout)

    return run


class FakeResponse:
    def __init__(self, data, ok: bool = True) -> None:
        self._data = data
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def get(self, url, *, headers, timeout):
        self.calls.append({"url": url, "headers": headers})
        return self.response


def test_get_head_sha_strips_output() -> None:
    assert get_head_sha(runner=fake_runner("abc123\n")) == "abc123"


def test_remote_owner_repo_parses_https_url() -> None:
    assert get_remote_owner_repo(runner=fake_runner("https://github.com/iamkeq/FigAi.git\n")) == ("iamkeq", "FigAi")


def test_remote_owner_repo_parses_https_url_without_dot_git() -> None:
    assert get_remote_owner_repo(runner=fake_runner("https://github.com/iamkeq/FigAi\n")) == ("iamkeq", "FigAi")


def test_remote_owner_repo_parses_ssh_url() -> None:
    assert get_remote_owner_repo(runner=fake_runner("git@github.com:iamkeq/FigAi.git\n")) == ("iamkeq", "FigAi")


def test_remote_owner_repo_returns_none_for_non_github_remote() -> None:
    assert get_remote_owner_repo(runner=fake_runner("https://gitlab.com/a/b.git\n")) is None


def test_find_merged_pr_returns_number_and_labels() -> None:
    http = FakeHttp(FakeResponse([{"number": 7, "labels": [{"name": "feature"}, {"name": "bot"}]}]))

    pr = find_merged_pr_for_commit("iamkeq", "FigAi", "abc", token="tok", http=http)

    assert pr == {"number": 7, "labels": ["feature", "bot"]}
    assert http.calls[0]["url"].endswith("/repos/iamkeq/FigAi/commits/abc/pulls")
    assert http.calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_find_merged_pr_omits_auth_header_without_token() -> None:
    http = FakeHttp(FakeResponse([]))

    find_merged_pr_for_commit("iamkeq", "FigAi", "abc", http=http)

    assert "Authorization" not in http.calls[0]["headers"]


def test_find_merged_pr_returns_none_when_no_pr() -> None:
    assert find_merged_pr_for_commit("o", "r", "abc", http=FakeHttp(FakeResponse([]))) is None


def test_find_merged_pr_returns_none_on_http_error() -> None:
    assert find_merged_pr_for_commit("o", "r", "abc", http=FakeHttp(FakeResponse(None, ok=False))) is None


def test_announcement_plain_restart() -> None:
    assert build_startup_announcement(None) == "Fig is back online."


def test_announcement_with_pr_and_labels() -> None:
    text = build_startup_announcement({"sha": "abc", "pr": {"number": 8, "labels": ["bugfix"]}})
    assert text == "Fig is back online after a code update: PR #8 (label: bugfix)."


def test_announcement_with_pr_and_no_labels() -> None:
    text = build_startup_announcement({"sha": "abc", "pr": {"number": 8, "labels": []}})
    assert "PR #8" in text and "no label" in text


def test_announcement_update_without_resolvable_pr() -> None:
    text = build_startup_announcement({"sha": "abc", "pr": None})
    assert "code update" in text and "PR #" not in text


# -- decide_pending_announcement ------------------------------------------------


def test_decide_prefers_explicit_marker() -> None:
    from figai.update.announcement import decide_pending_announcement

    marker = {"sha": "abc", "pr": {"number": 1, "labels": []}}
    assert decide_pending_announcement(marker, "new", "old", lambda s: None) == marker


def test_decide_detects_change_by_comparing_commits() -> None:
    from figai.update.announcement import decide_pending_announcement

    result = decide_pending_announcement(None, "new", "old", lambda sha: {"number": 8, "labels": ["enhancement"]})

    assert result == {"sha": "new", "pr": {"number": 8, "labels": ["enhancement"]}}


def test_decide_is_none_when_commit_unchanged() -> None:
    from figai.update.announcement import decide_pending_announcement

    assert decide_pending_announcement(None, "same", "same", lambda s: {"number": 1, "labels": []}) is None


def test_decide_is_none_on_first_run_with_nothing_to_compare() -> None:
    from figai.update.announcement import decide_pending_announcement

    assert decide_pending_announcement(None, "new", None, lambda s: {"number": 1, "labels": []}) is None


def test_decide_is_none_when_head_unknown() -> None:
    from figai.update.announcement import decide_pending_announcement

    assert decide_pending_announcement(None, None, "old", lambda s: None) is None
