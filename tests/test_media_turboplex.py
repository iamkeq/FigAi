from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from figai.agent.tools import ToolRegistry
from figai.config import AppConfig, ServiceConnection, Settings
from figai.media import summary
from figai.media.tools import not_configured, register_media_tools
from figai.tools.confirm import ConfirmationStore

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

SAB_QUEUE = {"queue": {"paused": False, "speed": "42.1 M", "timeleft": "0:12:00", "slots": [
    {"filename": "Dune.2021.2160p", "percentage": "45", "timeleft": "0:08:10", "status": "Downloading"},
    {"filename": "Severance.S02E03", "percentage": "0", "timeleft": "0:20:00", "status": "Queued"}]}}
SAB_HISTORY = {"history": {"slots": [
    {"name": "Arrival.2016", "status": "Completed", "size": "8.1 GB", "completed": int(datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc).timestamp())}]}}
SONARR_QUEUE = {"records": [{"series": {"title": "Severance"}, "episode": {"seasonNumber": 2, "episodeNumber": 3},
                             "size": 1000, "sizeleft": 250, "timeleft": "00:05:00", "status": "downloading"}]}
RADARR_QUEUE = {"records": [{"movie": {"title": "Dune", "year": 2021}, "size": 8000, "sizeleft": 4400, "timeleft": "00:08:10", "status": "downloading"}]}
SONARR_HISTORY = {"records": [
    {"eventType": "downloadFolderImported", "date": "2026-10-25T10:30:00Z", "series": {"title": "Andor"}, "episode": {"seasonNumber": 1, "episodeNumber": 4}},
    {"eventType": "downloadFailed", "date": "2026-10-25T09:00:00Z", "series": {"title": "Nope"}, "episode": {"seasonNumber": 1, "episodeNumber": 1}}]}
RADARR_HISTORY = {"records": [{"eventType": "grabbed", "date": "2026-10-25T11:55:00Z", "movie": {"title": "Heat", "year": 1995}}]}


def test_queue_and_history_summaries_are_short_and_readable() -> None:
    text = summary.sab_queue(SAB_QUEUE)
    assert "2 in the queue" in text and "Dune.2021.2160p -- 45%, 0:08:10 left" in text
    assert "nothing downloading" in summary.sab_queue({"queue": {"slots": []}})
    assert "PAUSED" in summary.sab_queue({"queue": {"paused": True, "slots": []}})
    assert "Severance S02E03 -- 75%, 00:05:00" in summary.arr_queue("sonarr", SONARR_QUEUE)
    assert "Dune (2021) -- 45%" in summary.arr_queue("radarr", RADARR_QUEUE)
    assert "nothing in the download queue" in summary.arr_queue("radarr", {"records": []})
    assert "Arrival.2016 -- Completed, 8.1 GB, 1 h ago" in summary.sab_history(SAB_HISTORY, NOW)
    history = summary.arr_history("sonarr", SONARR_HISTORY, NOW)
    assert "Andor S01E04 -- downloaded 1 h ago" in history and "Nope" not in history  # failures aren't "downloaded"
    assert "Heat (1995) -- grabbed (downloading) 5 min ago" in summary.arr_history("radarr", RADARR_HISTORY, NOW)


def test_long_queues_are_capped() -> None:
    big = {"records": [{"movie": {"title": f"Movie {i}"}, "size": 10, "sizeleft": 5} for i in range(25)]}
    text = summary.arr_queue("radarr", big)
    assert text.count("\n") <= 11 and "and 15 more" in text


class Resp:
    def __init__(self, data) -> None:
        self.data = data

    def raise_for_status(self) -> None: ...
    def json(self):
        return self.data


class Http:
    def __init__(self, routes: dict[str, object], down: set[str] = frozenset()) -> None:
        self.routes, self.down, self.calls = routes, set(down), []

    def get(self, url, *, headers, params, timeout):
        self.calls.append((url, params))
        for host in self.down:
            if host in url:
                raise ConnectionError("connection refused")
        for key, data in self.routes.items():
            if key in f"{url}?mode={(params or {}).get('mode', '')}":
                return Resp(data)
        raise AssertionError(f"unexpected {url} {params}")

    def post(self, url, *, headers, json, timeout):
        return Resp({"id": 1, **json})


def make_tools(tmp_path: Path, http: Http, **services):
    app = AppConfig.model_validate({
        "models": {"default_chat": "x", "vision": "y"},
        "vault": {"ssh_host_alias": "v", "remote_path": "/v", "local_path": str(tmp_path)},
    })
    settings = type("S", (), {"sonarr": None, "radarr": None, "sabnzbd": None, **services})()
    config = type("C", (), {"app": app, "settings": settings})()
    registry = ToolRegistry()
    register_media_tools(registry, config, ConfirmationStore(), http=http)

    def call(tool, **args):
        return json.loads(registry.execute(tool, json.dumps(args)))

    return call


ALL = {
    "sabnzbd": ServiceConnection(url="http://plex:8080", api_key="k"),
    "sonarr": ServiceConnection(url="http://plex:8989", api_key="k"),
    "radarr": ServiceConnection(url="http://plex:7878", api_key="k"),
}
ROUTES = {
    "mode=queue": SAB_QUEUE, "mode=history": SAB_HISTORY,
    ":8989/api/v3/queue": SONARR_QUEUE, ":7878/api/v3/queue": RADARR_QUEUE,
    ":8989/api/v3/history": SONARR_HISTORY, ":7878/api/v3/history": RADARR_HISTORY,
}


def test_media_downloads_covers_all_three_services(tmp_path: Path) -> None:
    http = Http(ROUTES)
    text = make_tools(tmp_path, http, **ALL)("media_downloads")["summary"]
    assert "SABnzbd" in text and "Severance S02E03" in text and "Dune (2021)" in text


def test_media_recent_covers_history_everywhere(tmp_path: Path) -> None:
    text = make_tools(tmp_path, Http(ROUTES), **ALL)("media_recent")["summary"]
    assert "Arrival.2016" in text and "Andor S01E04" in text and "Heat (1995)" in text


def test_one_unreachable_service_does_not_hide_the_others(tmp_path: Path) -> None:
    http = Http(ROUTES, down={":8989"})
    text = make_tools(tmp_path, http, **ALL)("media_downloads")["summary"]
    assert "Couldn't reach Sonarr at http://plex:8989" in text and "turbo-plex" in text and "Dune (2021)" in text


def test_unconfigured_services_explain_how_to_set_them_up(tmp_path: Path) -> None:
    out = make_tools(tmp_path, Http({}))("media_downloads")["summary"]
    assert "turbo-plex" in out and "MEDIA_HOST" in out and "API_KEY" in out
    assert "SONARR_API_KEY" in not_configured("sonarr") and "7878" in not_configured("radarr")
    only_sab = make_tools(tmp_path, Http(ROUTES), sabnzbd=ALL["sabnzbd"])("media_downloads")["summary"]
    assert "SABnzbd" in only_sab and "Sonarr" not in only_sab  # unconfigured ones are simply skipped


def test_inspect_history_and_queue_views(tmp_path: Path) -> None:
    call = make_tools(tmp_path, Http(ROUTES), **ALL)
    assert "Andor" in call("inspect_media_service", service="sonarr", view="history")["summary"]
    assert "Arrival" in call("inspect_media_service", service="sabnzbd", view="history")["summary"]
    assert "Dune" in call("inspect_media_service", service="sabnzbd", view="queue")["summary"]


def test_download_without_a_service_searches_both_and_picks_the_one_that_knows_it(tmp_path: Path) -> None:
    routes = {"/api/v3/movie/lookup": [{"title": "Heat", "year": 1995}], "/api/v3/series/lookup": []}
    call = make_tools(tmp_path, Http(routes), **ALL)
    proposed = call("propose_add_media", title="Heat")
    assert proposed["status"] == "awaiting_confirmation" and "movie 'Heat' (1995) to Radarr" in proposed["preview"]


def test_download_that_matches_both_a_movie_and_a_series_asks_which(tmp_path: Path) -> None:
    routes = {"/api/v3/movie/lookup": [{"title": "Fargo", "year": 1996}], "/api/v3/series/lookup": [{"title": "Fargo", "year": 2014}]}
    out = make_tools(tmp_path, Http(routes), **ALL)("propose_add_media", title="Fargo")
    assert out["status"] == "ambiguous" and {o["kind"] for o in out["options"]} == {"movie", "TV series"}


def test_download_with_nothing_found_or_nothing_configured(tmp_path: Path) -> None:
    routes = {"/api/v3/movie/lookup": [], "/api/v3/series/lookup": []}
    assert "No match" in make_tools(tmp_path, Http(routes), **ALL)("propose_add_media", title="Zzzz")["error"]
    assert "isn't set up" in make_tools(tmp_path, Http({}))("propose_add_media", title="Heat", service="radarr")["error"]


# -- MEDIA_HOST ------------------------------------------------------------------------------------


def test_media_host_builds_the_urls_from_just_the_api_keys(monkeypatch, tmp_path: Path) -> None:
    for var in ("SONARR_URL", "RADARR_URL", "SABNZBD_URL", "SONARR_API_KEY", "RADARR_API_KEY", "SABNZBD_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MEDIA_HOST", "192.168.50.51")
    monkeypatch.setenv("SONARR_API_KEY", "a")
    monkeypatch.setenv("SABNZBD_API_KEY", "b")
    for var, value in {"OPENROUTER_API_KEY": "k", "SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "ALLOWED_SLACK_USER_ID": "U"}.items():
        monkeypatch.setenv(var, value)
    settings = Settings.from_env(tmp_path / "nope.env")
    assert settings.sonarr.url == "http://192.168.50.51:8989" and settings.sabnzbd.url == "http://192.168.50.51:8080"
    assert settings.radarr is None  # no key, so not configured
    monkeypatch.setenv("SONARR_URL", "http://custom:1234")
    assert Settings.from_env(tmp_path / "nope.env").sonarr.url == "http://custom:1234"  # an explicit URL wins
