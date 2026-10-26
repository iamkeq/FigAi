"""inspect_media_service (read-only) and propose/resolve_add_media (confirm-gated)
for Sonarr, Radarr, and SABnzbd."""

from __future__ import annotations

import json
from typing import Any

import requests

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config, ServiceConnection
from figai.logging_setup import get_logger
from figai.media import summary
from figai.media.client import (
    HttpClient,
    MediaServiceError,
    radarr_add,
    radarr_history,
    radarr_library,
    radarr_lookup,
    radarr_queue,
    radarr_status,
    redact,
    sabnzbd_call,
    sonarr_add,
    sonarr_history,
    sonarr_library,
    sonarr_lookup,
    sonarr_queue,
    sonarr_status,
)
from figai.tools.confirm import ConfirmationStore

logger = get_logger("media.tools")

ADD_MEDIA_ACTION_TYPE = "add_media"
RESULT_CHAR_LIMIT = 20000
MAX_HISTORY = 25


def _unreachable(service: str, url: str, exc: Exception) -> str:
    return f"Couldn't reach {service.capitalize()} at {url}: {exc}. Is turbo-plex up, and is that the right address/port?"


SERVICES = ("sonarr", "radarr", "sabnzbd")
DEFAULT_PORTS = {"sonarr": 8989, "radarr": 7878, "sabnzbd": 8080}


def not_configured(service: str) -> str:
    name = service.upper()
    return (
        f"{service.capitalize()} isn't set up yet. These services run on the turbo-plex server, so add to .env: "
        f"{name}_API_KEY=<key from {service.capitalize()} -> Settings -> General> and "
        f"{name}_URL=http://<turbo-plex address>:{DEFAULT_PORTS.get(service, 8080)} (or one line, MEDIA_HOST=<turbo-plex address>, "
        f"to cover all three), then restart Fig. 127.0.0.1 only works for a service running on this PC."
    )


def _connection_for(config: Config, service: str) -> ServiceConnection | None:
    return {"sonarr": config.settings.sonarr, "radarr": config.settings.radarr, "sabnzbd": config.settings.sabnzbd}.get(
        service
    )


def register_media_tools(
    registry: ToolRegistry, config: Config, confirmations: ConfirmationStore, http: HttpClient = requests
) -> None:
    def _inspect(arguments: dict[str, Any]) -> str:
        service = arguments["service"]
        view = arguments.get("view", "status")
        conn = _connection_for(config, service)
        if conn is None:
            return json.dumps({"error": not_configured(service)})

        try:
            if service in ("sonarr", "radarr"):
                funcs = {
                    "sonarr": {"status": sonarr_status, "library": sonarr_library, "queue": sonarr_queue, "history": sonarr_history},
                    "radarr": {"status": radarr_status, "library": radarr_library, "queue": radarr_queue, "history": radarr_history},
                }[service]
                data = funcs[view](http, conn.url, conn.api_key)
                if view == "library":
                    return json.dumps(summary.library_summary(service, data))
                if view == "queue":
                    return json.dumps({"summary": summary.arr_queue(service, data)})
                if view == "history":
                    return json.dumps({"summary": summary.arr_history(service, data)})
            elif service == "sabnzbd":
                if view == "queue":
                    return json.dumps({"summary": summary.sab_queue(sabnzbd_call(http, conn.url, conn.api_key, mode="queue"))})
                if view == "history":
                    return json.dumps(
                        {"summary": summary.sab_history(sabnzbd_call(http, conn.url, conn.api_key, mode="history", limit=MAX_HISTORY))}
                    )
                data = sabnzbd_call(http, conn.url, conn.api_key, mode="fullstatus")
            else:
                return json.dumps({"error": f"Unknown service: {service}"})
        except KeyError:
            return json.dumps({"error": f"Unsupported view '{view}' for {service}"})
        except MediaServiceError as exc:
            return json.dumps({"error": _unreachable(service, conn.url, exc)})

        return json.dumps(redact(data))[:RESULT_CHAR_LIMIT]

    registry.register(
        Tool(
            name="inspect_media_service",
            description=(
                "Read-only view of Sonarr (TV), Radarr (movies) or SABnzbd (downloader) on the turbo-plex server: "
                "status, library (counts and titles), queue (what's downloading), or history (recently downloaded). "
                "For 'what's downloading' / 'what was recently downloaded' prefer media_downloads / media_recent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": ["sonarr", "radarr", "sabnzbd"]},
                    "view": {"type": "string", "enum": ["status", "library", "queue", "history"]},
                },
                "required": ["service"],
            },
            handler=_inspect,
        )
    )

    def _lookup(service: str, title: str, year: int | None) -> list[dict[str, Any]]:
        conn = _connection_for(config, service)
        if conn is None:
            raise MediaServiceError(not_configured(service))
        lookup_fn = sonarr_lookup if service == "sonarr" else radarr_lookup
        results = lookup_fn(http, conn.url, conn.api_key, title)
        return [r for r in results if r.get("year") == year] if year is not None else results

    def _propose_add(arguments: dict[str, Any]) -> str:
        service = arguments.get("service")
        title = arguments["title"]
        year = arguments.get("year")
        if service not in (None, "", "sonarr", "radarr"):
            return json.dumps({"error": "Only sonarr (TV) and radarr (movies) support adding titles"})

        if not service:  # "download Dune": look in both and see which one knows it
            found: dict[str, list[dict[str, Any]]] = {}
            problems: list[str] = []
            for candidate in ("radarr", "sonarr"):
                try:
                    hits = _lookup(candidate, title, year)
                except MediaServiceError as exc:
                    problems.append(str(exc))
                    continue
                if hits:
                    found[candidate] = hits
            if not found:
                return json.dumps({"error": f"No match found for '{title}'" + (f" ({year})" if year else "")
                                   + (f". Problems: {'; '.join(problems)}" if problems else "")})
            if len(found) > 1:
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "options": [{"service": svc, "title": hits[0].get("title"), "year": hits[0].get("year"),
                                     "kind": "movie" if svc == "radarr" else "TV series"} for svc, hits in found.items()],
                        "note": "Both a movie and a TV series match. Ask the user which one they want.",
                    }
                )
            service = next(iter(found))
            results = found[service]
        else:
            try:
                results = _lookup(service, title, year)
            except MediaServiceError as exc:
                return json.dumps({"error": str(exc)})

        if not results:
            return json.dumps({"error": f"No match found for '{title}'" + (f" ({year})" if year else "")})
        if len(results) > 1:
            options = [{"title": r.get("title"), "year": r.get("year")} for r in results[:5]]
            return json.dumps(
                {"status": "ambiguous", "service": service, "options": options,
                 "note": "Ask the user to specify a year to disambiguate."}
            )

        match = results[0]
        preview = f"Add {'movie' if service == 'radarr' else 'TV series'} '{match.get('title')}' ({match.get('year')}) to {service.capitalize()}"
        token = confirmations.propose(ADD_MEDIA_ACTION_TYPE, {"service": service, "match": match}, preview)
        logger.info("Proposed add_media token=%s service=%s title=%s", token, service, match.get("title"))
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": "Wait for the user's explicit yes in a LATER message before calling resolve_add_media.",
            }
        )

    registry.register(
        Tool(
            name="propose_add_media",
            description=(
                "Download a movie or TV show: look the title up on the turbo-plex server and draft adding it to Radarr "
                "(movies) or Sonarr (TV). Leave service out to search both. Does not add anything until the user confirms "
                "in a later message (then resolve_add_media)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": ["sonarr", "radarr"], "description": "Omit to search both."},
                    "title": {"type": "string"},
                    "year": {"type": "integer"},
                },
                "required": ["title"],
            },
            handler=_propose_add,
        )
    )

    def _resolve_add(arguments: dict[str, Any]) -> str:
        token = arguments["token"]
        try:
            payload = confirmations.resolve(token, ADD_MEDIA_ACTION_TYPE)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})

        service = payload["service"]
        match = payload["match"]
        conn = _connection_for(config, service)
        if conn is None:
            return json.dumps({"error": not_configured(service)})

        add_fn = sonarr_add if service == "sonarr" else radarr_add
        try:
            add_fn(http, conn.url, conn.api_key, match)
        except MediaServiceError as exc:
            return json.dumps({"error": str(exc)})

        logger.info("Added %s title via %s", match.get("title"), service)
        return json.dumps({"status": "added", "service": service, "title": match.get("title")})

    registry.register(
        Tool(
            name="resolve_add_media",
            description=(
                "Add a previously proposed title, after the user has explicitly confirmed it in a later "
                "message. Never call this in the same turn the title was proposed."
            ),
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve_add,
        )
    )


    def _report(kind: str) -> str:
        """One text block covering every configured service (current queues, or recent history)."""
        blocks: list[str] = []
        configured = False
        for service in ("sabnzbd", "sonarr", "radarr"):
            conn = _connection_for(config, service)
            if conn is None:
                continue
            configured = True
            try:
                if service == "sabnzbd":
                    blocks.append(
                        summary.sab_queue(sabnzbd_call(http, conn.url, conn.api_key, mode="queue"))
                        if kind == "current"
                        else summary.sab_history(sabnzbd_call(http, conn.url, conn.api_key, mode="history", limit=MAX_HISTORY))
                    )
                else:
                    funcs = {"sonarr": (sonarr_queue, sonarr_history), "radarr": (radarr_queue, radarr_history)}[service]
                    data = funcs[0 if kind == "current" else 1](http, conn.url, conn.api_key)
                    blocks.append(summary.arr_queue(service, data) if kind == "current" else summary.arr_history(service, data))
            except MediaServiceError as exc:
                blocks.append(f"{service.capitalize()}: {_unreachable(service, conn.url, exc)}")
        if not configured:
            return not_configured("sabnzbd")
        return "\n\n".join(blocks)

    registry.register(
        Tool(
            name="media_downloads",
            description=(
                "What is downloading right now: the SABnzbd queue plus the Sonarr (TV) and Radarr (movie) queues, "
                "all from the turbo-plex server. Use for 'what's downloading', 'what's in the queue', 'is it done yet'."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda _a: json.dumps({"summary": _report("current")}),
        )
    )
    registry.register(
        Tool(
            name="media_recent",
            description=(
                "What finished downloading recently (SABnzbd history plus what Sonarr and Radarr imported), from the "
                "turbo-plex server. Use for 'what was recently downloaded', 'what finished', 'what came in today'."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda _a: json.dumps({"summary": _report("recent")}),
        )
    )
