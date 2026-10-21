"""Thin wrapper around obsws-python's OBS WebSocket client. Every function here
takes an already-connected client, so tests can inject a fake one duck-typed to
the same interface -- no real OBS server needed to exercise the logic."""

from __future__ import annotations

from typing import Any, Callable, Protocol

from figai.config import Config


class ObsError(Exception):
    """Raised for both 'OBS isn't configured' and real connection failures."""


class ObsClient(Protocol):
    def get_record_status(self) -> Any: ...
    def start_record(self) -> None: ...
    def stop_record(self) -> None: ...
    def get_stream_status(self) -> Any: ...
    def start_stream(self) -> None: ...
    def stop_stream(self) -> None: ...
    def get_scene_list(self) -> Any: ...
    def get_current_program_scene(self) -> Any: ...
    def set_current_program_scene(self, sceneName: str) -> None: ...
    def disconnect(self) -> None: ...


def connect_real(host: str, port: int, password: str | None) -> ObsClient:
    import obsws_python  # imported lazily -- only needed when OBS is actually configured

    return obsws_python.ReqClient(host=host, port=port, password=password or "", timeout=5)


def build_client_factory(config: Config) -> Callable[[], ObsClient]:
    def factory() -> ObsClient:
        conn = config.settings.obs
        if conn is None:
            raise ObsError("OBS is not configured (set OBS_WS_HOST in .env)")
        try:
            return connect_real(conn.host, conn.port, conn.password)
        except Exception as exc:  # noqa: BLE001
            raise ObsError(f"Could not connect to OBS: {exc}") from exc

    return factory


def response_to_dict(response: Any) -> dict[str, Any]:
    """obsws-python builds response objects with a dynamic `.attrs()` listing
    their (snake_cased) field names rather than normal dataclass fields."""
    if hasattr(response, "attrs"):
        return {name: getattr(response, name) for name in response.attrs()}
    return dict(vars(response))


def get_status(client: ObsClient) -> dict[str, Any]:
    record = response_to_dict(client.get_record_status())
    stream = response_to_dict(client.get_stream_status())
    current_scene = response_to_dict(client.get_current_program_scene())
    scene_list = response_to_dict(client.get_scene_list())
    scenes = [s.get("sceneName") or s.get("scene_name") for s in scene_list.get("scenes", [])]
    return {
        "recording": bool(record.get("output_active")),
        "streaming": bool(stream.get("output_active")),
        "current_scene": current_scene.get("current_program_scene_name") or current_scene.get("scene_name"),
        "scenes": scenes,
    }


def start_recording(client: ObsClient) -> None:
    client.start_record()


def stop_recording(client: ObsClient) -> None:
    client.stop_record()


def start_streaming(client: ObsClient) -> None:
    client.start_stream()


def stop_streaming(client: ObsClient) -> None:
    client.stop_stream()


def switch_scene(client: ObsClient, scene_name: str) -> None:
    client.set_current_program_scene(scene_name)
