from __future__ import annotations

import json
from pathlib import Path

from figai.agent.tools import ToolRegistry
from figai.config import AppConfig, ServiceConnection
from figai.media.tools import register_media_tools
from figai.tools.confirm import ConfirmationStore


class FakeResponse:
    def __init__(self, data) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url, *, headers, params, timeout):
        self.calls.append(url)
        for key, data in self.responses.items():
            if key in url:
                return FakeResponse(data)
        raise AssertionError(f"Unexpected GET {url}")

    def post(self, url, *, headers, json, timeout):
        self.calls.append(url)
        return FakeResponse({"id": 1, **json})


def make_config(tmp_path: Path, **settings_overrides) -> object:
    app = AppConfig.model_validate(
        {
            "models": {"default_chat": "x", "vision": "y"},
            "vault": {"ssh_host_alias": "v", "remote_path": "/v", "local_path": str(tmp_path / "vault")},
        }
    )

    class FakeSettings:
        def __init__(self) -> None:
            self.sonarr = settings_overrides.get("sonarr")
            self.radarr = settings_overrides.get("radarr")
            self.sabnzbd = settings_overrides.get("sabnzbd")

    class FakeConfig:
        def __init__(self) -> None:
            self.app = app
            self.settings = FakeSettings()

    return FakeConfig()


def test_inspect_media_service_not_configured_returns_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_media_tools(registry, make_config(tmp_path), ConfirmationStore(), http=FakeHttp({}))

    result = json.loads(registry.execute("inspect_media_service", json.dumps({"service": "sonarr"})))

    assert "error" in result


def test_inspect_media_service_returns_redacted_library(tmp_path: Path) -> None:
    config = make_config(tmp_path, sonarr=ServiceConnection(url="http://localhost:8989", api_key="secret-key"))
    http = FakeHttp({"/api/v3/series": [{"title": "Show", "apikey": "leak"}]})
    registry = ToolRegistry()
    register_media_tools(registry, config, ConfirmationStore(), http=http)

    result = json.loads(
        registry.execute("inspect_media_service", json.dumps({"service": "sonarr", "view": "library"}))
    )

    assert result == [{"title": "Show", "apikey": "[redacted]"}]


def test_propose_add_media_ambiguous_returns_options(tmp_path: Path) -> None:
    config = make_config(tmp_path, sonarr=ServiceConnection(url="http://localhost:8989", api_key="k"))
    http = FakeHttp({"/api/v3/series/lookup": [{"title": "Show A", "year": 2020}, {"title": "Show A", "year": 2021}]})
    registry = ToolRegistry()
    register_media_tools(registry, config, ConfirmationStore(), http=http)

    result = json.loads(
        registry.execute("propose_add_media", json.dumps({"service": "sonarr", "title": "Show A"}))
    )

    assert result["status"] == "ambiguous"
    assert len(result["options"]) == 2


def test_propose_then_resolve_add_media_round_trip(tmp_path: Path) -> None:
    config = make_config(tmp_path, radarr=ServiceConnection(url="http://localhost:7878", api_key="k"))
    http = FakeHttp(
        {
            "/api/v3/movie/lookup": [{"title": "A Movie", "year": 2020, "tmdbId": 1}],
            "/api/v3/rootfolder": [{"path": "/movies"}],
            "/api/v3/qualityprofile": [{"id": 7}],
        }
    )
    registry = ToolRegistry()
    register_media_tools(registry, config, ConfirmationStore(), http=http)

    propose_result = json.loads(
        registry.execute("propose_add_media", json.dumps({"service": "radarr", "title": "A Movie", "year": 2020}))
    )
    assert propose_result["status"] == "awaiting_confirmation"

    resolve_result = json.loads(
        registry.execute("resolve_add_media", json.dumps({"token": propose_result["token"]}))
    )

    assert resolve_result == {"status": "added", "service": "radarr", "title": "A Movie"}


def test_resolve_add_media_without_prior_proposal_returns_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_media_tools(registry, make_config(tmp_path), ConfirmationStore(), http=FakeHttp({}))

    result = json.loads(registry.execute("resolve_add_media", json.dumps({"token": "madeup"})))

    assert "error" in result


def test_propose_add_media_no_match_returns_error(tmp_path: Path) -> None:
    config = make_config(tmp_path, sonarr=ServiceConnection(url="http://localhost:8989", api_key="k"))
    http = FakeHttp({"/api/v3/series/lookup": []})
    registry = ToolRegistry()
    register_media_tools(registry, config, ConfirmationStore(), http=http)

    result = json.loads(
        registry.execute("propose_add_media", json.dumps({"service": "sonarr", "title": "Nope"}))
    )

    assert "error" in result
