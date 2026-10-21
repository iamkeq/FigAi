"""OBS control tools: status, recording/streaming start-stop, scene switching.
These are low-stakes actions (per the project's own risk classification) --
same tier as send_notification/media_key, no confirmation gate."""

from __future__ import annotations

import json
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.obs.client import (
    ObsClient,
    ObsError,
    get_status,
    start_recording,
    start_streaming,
    stop_recording,
    stop_streaming,
    switch_scene,
)

ClientFactory = Callable[[], ObsClient]


def _run(client_factory: ClientFactory, operation: Callable[[ObsClient], Any]) -> str:
    try:
        client = client_factory()
    except ObsError as exc:
        return json.dumps({"error": str(exc)})

    try:
        result = operation(client)
    except Exception as exc:  # noqa: BLE001 - any OBS/transport failure
        return json.dumps({"error": str(exc)})
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

    return json.dumps(result if result is not None else {"status": "ok"})


def register_obs_tools(registry: ToolRegistry, client_factory: ClientFactory) -> None:
    registry.register(
        Tool(
            name="obs_status",
            description="Get OBS recording/streaming state, the current scene, and the list of scenes.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: _run(client_factory, get_status),
        )
    )
    registry.register(
        Tool(
            name="obs_start_recording",
            description="Start recording in OBS.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: _run(client_factory, start_recording),
        )
    )
    registry.register(
        Tool(
            name="obs_stop_recording",
            description="Stop recording in OBS.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: _run(client_factory, stop_recording),
        )
    )
    registry.register(
        Tool(
            name="obs_start_streaming",
            description="Start streaming in OBS.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: _run(client_factory, start_streaming),
        )
    )
    registry.register(
        Tool(
            name="obs_stop_streaming",
            description="Stop streaming in OBS.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: _run(client_factory, stop_streaming),
        )
    )

    def _switch_scene(arguments: dict[str, Any]) -> str:
        return _run(client_factory, lambda c: switch_scene(c, arguments["scene_name"]))

    registry.register(
        Tool(
            name="obs_switch_scene",
            description="Switch OBS's current program scene by name.",
            parameters={
                "type": "object",
                "properties": {"scene_name": {"type": "string"}},
                "required": ["scene_name"],
            },
            handler=_switch_scene,
        )
    )
