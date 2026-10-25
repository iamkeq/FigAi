"""Read-only Sonarr/Radarr/SABnzbd inspection, plus a confirm-gated add for
Sonarr/Radarr. Only talks to the exact origins configured in .env. Responses are
recursively redacted before reaching the model."""

from __future__ import annotations

from typing import Any, Protocol

TIMEOUT_SECONDS = 10
REDACT_KEYS = {"password", "apikey", "api_key", "token", "cookie", "secret", "credential", "credentials"}


class MediaServiceError(Exception):
    """A connection or HTTP-level failure talking to a media service."""


class HttpClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any] | None, timeout: int) -> Any: ...
    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: int) -> Any: ...


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in REDACT_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _arr_get(http: HttpClient, base_url: str, path: str, api_key: str, params: dict[str, Any] | None = None) -> Any:
    try:
        response = http.get(
            f"{base_url.rstrip('/')}{path}", headers={"X-Api-Key": api_key}, params=params, timeout=TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 - normalize every transport error the same way
        raise MediaServiceError(str(exc)) from exc


def _arr_post(http: HttpClient, base_url: str, path: str, api_key: str, payload: dict[str, Any]) -> Any:
    try:
        response = http.post(f"{base_url.rstrip('/')}{path}", headers={"X-Api-Key": api_key}, json=payload, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        raise MediaServiceError(str(exc)) from exc


# -- Sonarr -----------------------------------------------------------------


def sonarr_status(http: HttpClient, url: str, api_key: str) -> Any:
    return _arr_get(http, url, "/api/v3/system/status", api_key)


def sonarr_library(http: HttpClient, url: str, api_key: str) -> Any:
    return _arr_get(http, url, "/api/v3/series", api_key)


def sonarr_queue(http: HttpClient, url: str, api_key: str) -> Any:
    return _arr_get(http, url, "/api/v3/queue", api_key)


def sonarr_lookup(http: HttpClient, url: str, api_key: str, term: str) -> Any:
    return _arr_get(http, url, "/api/v3/series/lookup", api_key, params={"term": term})


def sonarr_add(http: HttpClient, url: str, api_key: str, lookup_result: dict[str, Any]) -> Any:
    root_folders = _arr_get(http, url, "/api/v3/rootfolder", api_key)
    quality_profiles = _arr_get(http, url, "/api/v3/qualityprofile", api_key)
    if not root_folders or not quality_profiles:
        raise MediaServiceError("Sonarr has no root folder or quality profile configured")
    payload = {
        **lookup_result,
        "qualityProfileId": quality_profiles[0]["id"],
        "rootFolderPath": root_folders[0]["path"],
        "monitored": True,
        "addOptions": {"searchForMissingEpisodes": True},
    }
    return _arr_post(http, url, "/api/v3/series", api_key, payload)


def sonarr_history(http: HttpClient, url: str, api_key: str, limit: int = 20) -> Any:
    return _arr_get(
        http, url, "/api/v3/history", api_key,
        params={"page": 1, "pageSize": limit, "sortKey": "date", "sortDirection": "descending",
                "includeSeries": "true", "includeEpisode": "true"},
    )


# -- Radarr -------------------------------------------------------------------


def radarr_status(http: HttpClient, url: str, api_key: str) -> Any:
    return _arr_get(http, url, "/api/v3/system/status", api_key)


def radarr_library(http: HttpClient, url: str, api_key: str) -> Any:
    return _arr_get(http, url, "/api/v3/movie", api_key)


def radarr_queue(http: HttpClient, url: str, api_key: str) -> Any:
    return _arr_get(http, url, "/api/v3/queue", api_key)


def radarr_history(http: HttpClient, url: str, api_key: str, limit: int = 20) -> Any:
    return _arr_get(
        http, url, "/api/v3/history", api_key,
        params={"page": 1, "pageSize": limit, "sortKey": "date", "sortDirection": "descending", "includeMovie": "true"},
    )


def radarr_lookup(http: HttpClient, url: str, api_key: str, term: str) -> Any:
    return _arr_get(http, url, "/api/v3/movie/lookup", api_key, params={"term": term})


def radarr_add(http: HttpClient, url: str, api_key: str, lookup_result: dict[str, Any]) -> Any:
    root_folders = _arr_get(http, url, "/api/v3/rootfolder", api_key)
    quality_profiles = _arr_get(http, url, "/api/v3/qualityprofile", api_key)
    if not root_folders or not quality_profiles:
        raise MediaServiceError("Radarr has no root folder or quality profile configured")
    payload = {
        **lookup_result,
        "qualityProfileId": quality_profiles[0]["id"],
        "rootFolderPath": root_folders[0]["path"],
        "monitored": True,
        "addOptions": {"searchForMovie": True},
    }
    return _arr_post(http, url, "/api/v3/movie", api_key, payload)


# -- SABnzbd --------------------------------------------------------------------


def sabnzbd_call(http: HttpClient, url: str, api_key: str, mode: str, **extra: Any) -> Any:
    try:
        response = http.get(
            f"{url.rstrip('/')}/api",
            headers={},
            params={"apikey": api_key, "mode": mode, "output": "json", **extra},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        raise MediaServiceError(str(exc)) from exc
