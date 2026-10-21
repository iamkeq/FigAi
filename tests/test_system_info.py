from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.system_info import register_system_info_tools


def test_system_status_returns_expected_keys() -> None:
    registry = ToolRegistry()
    register_system_info_tools(registry)

    result = json.loads(registry.execute("system_status", "{}"))

    for key in ("cpu_percent", "memory_percent", "disk_percent", "uptime_hours", "network_sent_mb"):
        assert key in result


def test_list_running_programs_respects_top_n() -> None:
    registry = ToolRegistry()
    register_system_info_tools(registry)

    result = json.loads(registry.execute("list_running_programs", json.dumps({"top_n": 3})))

    assert isinstance(result, list)
    assert len(result) <= 3
    if result:
        assert {"pid", "name", "cpu_percent", "memory_mb"} <= result[0].keys()


def test_list_running_programs_sorts_by_memory() -> None:
    registry = ToolRegistry()
    register_system_info_tools(registry)

    result = json.loads(registry.execute("list_running_programs", json.dumps({"top_n": 50, "sort_by": "memory"})))

    memory_values = [p["memory_mb"] for p in result]
    assert memory_values == sorted(memory_values, reverse=True)


def test_battery_status_returns_available_flag() -> None:
    registry = ToolRegistry()
    register_system_info_tools(registry)

    result = json.loads(registry.execute("battery_status", "{}"))

    assert "available" in result
    if result["available"]:
        assert "percent" in result
        assert "plugged_in" in result


def test_network_status_returns_interface_list() -> None:
    registry = ToolRegistry()
    register_system_info_tools(registry)

    result = json.loads(registry.execute("network_status", "{}"))

    assert isinstance(result["interfaces"], list)
    assert len(result["interfaces"]) >= 1
    assert {"name", "is_up", "speed_mbps", "addresses"} <= result["interfaces"][0].keys()
