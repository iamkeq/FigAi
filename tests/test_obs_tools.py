from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.obs.client import ObsError
from figai.obs.tools import register_obs_tools


class FakeResponse:
    def __init__(self, **kwargs) -> None:
        self._fields = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def attrs(self) -> list[str]:
        return list(self._fields)


class FakeObsClient:
    def __init__(self) -> None:
        self.actions: list[object] = []
        self.disconnected = False

    def get_record_status(self):
        return FakeResponse(output_active=False)

    def get_stream_status(self):
        return FakeResponse(output_active=False)

    def get_current_program_scene(self):
        return FakeResponse(current_program_scene_name="Main")

    def get_scene_list(self):
        return FakeResponse(scenes=[{"sceneName": "Main"}])

    def start_record(self) -> None:
        self.actions.append("start_record")

    def stop_record(self) -> None:
        self.actions.append("stop_record")

    def start_stream(self) -> None:
        self.actions.append("start_stream")

    def stop_stream(self) -> None:
        self.actions.append("stop_stream")

    def set_current_program_scene(self, sceneName: str) -> None:
        self.actions.append(("switch_scene", sceneName))

    def disconnect(self) -> None:
        self.disconnected = True


def make_registry(client: FakeObsClient | None = None, configured: bool = True) -> ToolRegistry:
    registry = ToolRegistry()

    def factory() -> FakeObsClient:
        if not configured:
            raise ObsError("OBS is not configured (set OBS_WS_HOST in .env)")
        assert client is not None
        return client

    register_obs_tools(registry, factory)
    return registry


def test_obs_status_returns_combined_state_and_disconnects() -> None:
    client = FakeObsClient()
    registry = make_registry(client)

    result = json.loads(registry.execute("obs_status", "{}"))

    assert result == {"recording": False, "streaming": False, "current_scene": "Main", "scenes": ["Main"]}
    assert client.disconnected is True


def test_obs_start_recording_calls_client() -> None:
    client = FakeObsClient()
    registry = make_registry(client)

    result = json.loads(registry.execute("obs_start_recording", "{}"))

    assert result == {"status": "ok"}
    assert client.actions == ["start_record"]


def test_obs_switch_scene_passes_scene_name() -> None:
    client = FakeObsClient()
    registry = make_registry(client)

    registry.execute("obs_switch_scene", json.dumps({"scene_name": "BRB"}))

    assert client.actions == [("switch_scene", "BRB")]


def test_obs_tool_not_configured_returns_error_without_raising() -> None:
    registry = make_registry(configured=False)

    result = json.loads(registry.execute("obs_status", "{}"))

    assert "error" in result


def test_obs_tool_connection_failure_returns_error_without_raising() -> None:
    class ExplodingClient:
        def get_record_status(self):
            raise RuntimeError("connection reset")

        def disconnect(self) -> None:
            pass

    registry = ToolRegistry()
    register_obs_tools(registry, lambda: ExplodingClient())

    result = json.loads(registry.execute("obs_status", "{}"))

    assert "error" in result
