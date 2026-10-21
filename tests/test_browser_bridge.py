from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from figai.agent.tools import ToolRegistry
from figai.browser_bridge.server import make_server, sanitize_tabs
from figai.browser_bridge.state import BridgeState
from figai.browser_bridge.tools import register_browser_bridge_tools

TOKEN = "t" * 32


@pytest.fixture
def bridge():
    state = BridgeState()
    server = make_server(state, TOKEN, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def request(method, path, body=None, token=TOKEN, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Fig-Token"] = token
        if host:
            headers["Host"] = host
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    yield state, request, port
    server.shutdown()


# -- state --------------------------------------------------------------------


def test_state_tabs_round_trip_and_age() -> None:
    now = [100.0]
    state = BridgeState(clock=lambda: now[0])
    assert state.get_tabs() == ([], None)
    state.set_tabs([{"url": "https://a.com"}])
    now[0] = 130.0
    tabs, age = state.get_tabs()
    assert tabs == [{"url": "https://a.com"}] and age == 30.0


def test_state_connected_only_when_recently_seen() -> None:
    now = [0.0]
    state = BridgeState(clock=lambda: now[0])
    assert state.is_connected() is False
    state.touch()
    assert state.is_connected() is True
    now[0] = 500.0
    assert state.is_connected() is False


def test_wait_for_commands_returns_queued_immediately_and_clears() -> None:
    state = BridgeState()
    state.queue_command({"type": "open_tab", "url": "https://a.com"})
    assert state.wait_for_commands(5) == [{"type": "open_tab", "url": "https://a.com"}]
    assert state.wait_for_commands(0.05) == []


def test_wait_for_commands_wakes_when_a_command_arrives() -> None:
    state = BridgeState()
    threading.Timer(0.1, lambda: state.queue_command({"type": "open_tab"})).start()
    start = time.time()
    assert state.wait_for_commands(5) == [{"type": "open_tab"}]
    assert time.time() - start < 3


# -- server -------------------------------------------------------------------


def test_server_rejects_missing_and_wrong_token(bridge) -> None:
    _, request, _ = bridge
    assert request("POST", "/tabs", {"tabs": []}, token=None)[0] == 401
    assert request("POST", "/tabs", {"tabs": []}, token="wrong" * 8)[0] == 401


def test_server_rejects_unexpected_host_header(bridge) -> None:
    _, request, _ = bridge
    assert request("POST", "/tabs", {"tabs": []}, host="evil.example.com")[0] == 403


def test_server_never_approves_cors_preflight(bridge) -> None:
    _, request, _ = bridge
    assert request("OPTIONS", "/tabs")[0] == 405


def test_server_stores_posted_tabs(bridge) -> None:
    state, request, _ = bridge
    status, _ = request(
        "POST", "/tabs", {"tabs": [{"id": 1, "windowId": 2, "title": "Hi", "url": "https://a.com", "active": True}]}
    )
    assert status == 200
    assert state.get_tabs()[0] == [{"id": 1, "window": 2, "title": "Hi", "url": "https://a.com", "active": True}]


def test_server_rejects_bad_json(bridge) -> None:
    _, _, port = bridge
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/tabs", body=b"{nope", headers={"X-Fig-Token": TOKEN, "Content-Length": "5"})
    assert conn.getresponse().status == 400


def test_server_delivers_queued_commands_to_the_extension(bridge) -> None:
    state, request, _ = bridge
    state.queue_command({"type": "open_tab", "url": "https://a.com", "active": True})
    status, data = request("GET", "/commands?wait=5")
    assert status == 200
    assert data == {"commands": [{"type": "open_tab", "url": "https://a.com", "active": True}]}


def test_server_long_poll_returns_empty_after_wait(bridge) -> None:
    _, request, _ = bridge
    assert request("GET", "/commands?wait=0.1") == (200, {"commands": []})


def test_server_marks_extension_connected_on_authenticated_request(bridge) -> None:
    state, request, _ = bridge
    assert state.is_connected() is False
    request("GET", "/commands?wait=0")
    assert state.is_connected() is True


def test_sanitize_tabs_truncates_and_drops_junk() -> None:
    tabs = sanitize_tabs([{"title": "x" * 1000, "url": "u" * 5000, "id": "nope"}, "junk", 5])
    assert len(tabs) == 1
    assert len(tabs[0]["title"]) == 300 and len(tabs[0]["url"]) == 2000
    assert tabs[0]["id"] is None
    assert sanitize_tabs("not a list") == []


# -- tools --------------------------------------------------------------------


def make_registry(state: BridgeState, enabled: bool = True) -> ToolRegistry:
    registry = ToolRegistry()
    register_browser_bridge_tools(registry, state, enabled)
    return registry


def test_list_open_tabs_returns_snapshot() -> None:
    state = BridgeState()
    state.set_tabs([{"id": 1, "window": 1, "title": "T", "url": "https://a.com", "active": True}])

    result = json.loads(make_registry(state).execute("list_open_tabs", "{}"))

    assert result["count"] == 1
    assert result["tabs"][0] == {"title": "T", "url": "https://a.com", "active": True, "window": 1}
    assert result["stale"] is False


def test_list_open_tabs_errors_before_extension_ever_reports() -> None:
    assert "error" in json.loads(make_registry(BridgeState()).execute("list_open_tabs", "{}"))


def test_list_open_tabs_flags_stale_snapshot() -> None:
    now = [0.0]
    state = BridgeState(clock=lambda: now[0])
    state.set_tabs([])
    now[0] = 500.0
    assert json.loads(make_registry(state).execute("list_open_tabs", "{}"))["stale"] is True


def test_tools_report_disabled_without_token() -> None:
    registry = make_registry(BridgeState(), enabled=False)
    assert "error" in json.loads(registry.execute("list_open_tabs", "{}"))
    assert "error" in json.loads(registry.execute("open_tab", json.dumps({"url": "https://a.com"})))


def test_open_tab_queues_command_when_extension_connected() -> None:
    state = BridgeState()
    state.touch()

    result = json.loads(make_registry(state).execute("open_tab", json.dumps({"url": "https://a.com"})))

    assert result == {"status": "queued", "url": "https://a.com"}
    assert state.wait_for_commands(0) == [{"type": "open_tab", "url": "https://a.com", "active": True}]


def test_open_tab_background_when_focus_false() -> None:
    state = BridgeState()
    state.touch()
    make_registry(state).execute("open_tab", json.dumps({"url": "https://a.com", "focus": False}))
    assert state.wait_for_commands(0)[0]["active"] is False


def test_open_tab_refuses_non_http_urls() -> None:
    state = BridgeState()
    state.touch()
    result = json.loads(make_registry(state).execute("open_tab", json.dumps({"url": "javascript:alert(1)"})))
    assert "error" in result
    assert state.wait_for_commands(0) == []


def test_open_tab_errors_when_extension_not_connected() -> None:
    result = json.loads(make_registry(BridgeState()).execute("open_tab", json.dumps({"url": "https://a.com"})))
    assert "error" in result
