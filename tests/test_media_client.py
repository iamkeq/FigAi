from __future__ import annotations

import pytest

from figai.media.client import (
    MediaServiceError,
    redact,
    sonarr_add,
    sonarr_library,
    sonarr_lookup,
)


class FakeResponse:
    def __init__(self, data, status_ok: bool = True) -> None:
        self._data = data
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, get_response=None, post_response=None) -> None:
        self.get_response = get_response
        self.post_response = post_response
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    def get(self, url, *, headers, params, timeout):
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        return self.get_response

    def post(self, url, *, headers, json, timeout):
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        return self.post_response


def test_redact_hides_known_secret_keys_recursively() -> None:
    data = {"apikey": "abc", "nested": {"password": "pw", "name": "ok"}, "list": [{"token": "t"}]}

    result = redact(data)

    assert result == {"apikey": "[redacted]", "nested": {"password": "[redacted]", "name": "ok"}, "list": [{"token": "[redacted]"}]}


def test_sonarr_library_calls_expected_endpoint_with_api_key_header() -> None:
    http = FakeHttp(get_response=FakeResponse([{"title": "Show"}]))

    result = sonarr_library(http, "http://localhost:8989", "key123")

    assert result == [{"title": "Show"}]
    assert http.get_calls[0]["url"] == "http://localhost:8989/api/v3/series"
    assert http.get_calls[0]["headers"] == {"X-Api-Key": "key123"}


def test_sonarr_lookup_raises_media_service_error_on_http_failure() -> None:
    http = FakeHttp(get_response=FakeResponse(None, status_ok=False))

    with pytest.raises(MediaServiceError):
        sonarr_lookup(http, "http://localhost:8989", "key123", "Some Show")


def test_sonarr_add_uses_first_root_folder_and_quality_profile() -> None:
    class SequencedHttp(FakeHttp):
        def __init__(self) -> None:
            super().__init__()
            self._responses = iter(
                [
                    FakeResponse([{"path": "/tv"}]),
                    FakeResponse([{"id": 4, "name": "HD-1080p"}]),
                ]
            )

        def get(self, url, *, headers, params, timeout):
            self.get_calls.append({"url": url})
            return next(self._responses)

        def post(self, url, *, headers, json, timeout):
            self.post_calls.append({"url": url, "json": json})
            return FakeResponse({"id": 1, **json})

    http = SequencedHttp()
    lookup_result = {"title": "Some Show", "tvdbId": 123}

    result = sonarr_add(http, "http://localhost:8989", "key123", lookup_result)

    assert result["qualityProfileId"] == 4
    assert result["rootFolderPath"] == "/tv"
    assert http.post_calls[0]["url"] == "http://localhost:8989/api/v3/series"


def test_sonarr_add_fails_when_no_root_folder_configured() -> None:
    http = FakeHttp(get_response=FakeResponse([]))

    with pytest.raises(MediaServiceError):
        sonarr_add(http, "http://localhost:8989", "key123", {"title": "x"})
