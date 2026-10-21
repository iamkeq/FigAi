"""inspect_media_service (read-only) and propose/resolve_add_media (confirm-gated)
for Sonarr, Radarr, and SABnzbd."""

from __future__ import annotations

import json
from typing import Any

import requests

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config, ServiceConnection
from figai.logging_setup import get_logger
from figai.media.client import (
    HttpClient,
    MediaServiceError,
    radarr_add,
    radarr_library,
    radarr_lookup,
    radarr_queue,
    radarr_status,
    redact,
    sabnzbd_call,
    sonarr_add,
    sonarr_library,
    sonarr_lookup,
    sonarr_queue,
    sonarr_status,
)
from figai.tools.confirm import ConfirmationStore

logger = get_logger("media.tools")

ADD_MEDIA_ACTION_TYPE = "add_media"
RESULT_CHAR_LIMIT = 20000


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
            return json.dumps({"error": f"{service} is not configured"})

        try:
            if service == "sonarr":
                data = {"status": sonarr_status, "library": sonarr_library, "queue": sonarr_queue}[view](
                    http, conn.url, conn.api_key
                )
            elif service == "radarr":
                data = {"status": radarr_status, "library": radarr_library, "queue": radarr_queue}[view](
                    http, conn.url, conn.api_key
                )
            elif service == "sabnzbd":
                data = sabnzbd_call(http, conn.url, conn.api_key, mode="queue" if view == "queue" else "fullstatus")
            else:
                return json.dumps({"error": f"Unknown service: {service}"})
        except KeyError:
            return json.dumps({"error": f"Unsupported view '{view}' for {service}"})
        except MediaServiceError as exc:
            return json.dumps({"error": str(exc)})

        return json.dumps(redact(data))[:RESULT_CHAR_LIMIT]

    registry.register(
        Tool(
            name="inspect_media_service",
            description="Read-only status, library, or queue view of a configured media service.",
            parameters={
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": ["sonarr", "radarr", "sabnzbd"]},
                    "view": {"type": "string", "enum": ["status", "library", "queue"]},
                },
                "required": ["service"],
            },
            handler=_inspect,
        )
    )

    def _propose_add(arguments: dict[str, Any]) -> str:
        service = arguments["service"]
        title = arguments["title"]
        year = arguments.get("year")
        if service not in ("sonarr", "radarr"):
            return json.dumps({"error": "Only sonarr and radarr support adding titles"})

        conn = _connection_for(config, service)
        if conn is None:
            return json.dumps({"error": f"{service} is not configured"})

        lookup_fn = sonarr_lookup if service == "sonarr" else radarr_lookup
        try:
            results = lookup_fn(http, conn.url, conn.api_key, title)
        except MediaServiceError as exc:
            return json.dumps({"error": str(exc)})

        if year is not None:
            results = [r for r in results if r.get("year") == year]
        if not results:
            return json.dumps({"error": f"No match found for '{title}'" + (f" ({year})" if year else "")})
        if len(results) > 1:
            options = [{"title": r.get("title"), "year": r.get("year")} for r in results[:5]]
            return json.dumps(
                {"status": "ambiguous", "options": options, "note": "Ask the user to specify a year to disambiguate."}
            )

        match = results[0]
        preview = f"Add {service} title '{match.get('title')}' ({match.get('year')})"
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
                "Look up a movie/series title in Sonarr or Radarr and draft adding it. Does not add anything -- "
                "the user must explicitly confirm in a later message before resolve_add_media runs."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": ["sonarr", "radarr"]},
                    "title": {"type": "string"},
                    "year": {"type": "integer"},
                },
                "required": ["service", "title"],
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
            return json.dumps({"error": f"{service} is not configured"})

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
