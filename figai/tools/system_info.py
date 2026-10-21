"""Read-only system introspection: running programs and overall machine status."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import psutil

from figai.agent.tools import Tool, ToolRegistry


def _list_running_programs(arguments: dict[str, Any]) -> str:
    top_n = int(arguments.get("top_n", 10))
    sort_by = arguments.get("sort_by", "cpu")
    sort_key = "memory_mb" if sort_by == "memory" else "cpu_percent"

    processes: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            memory_info = info.get("memory_info")
            processes.append(
                {
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "memory_mb": round((memory_info.rss if memory_info else 0) / (1024 * 1024), 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    processes.sort(key=lambda p: p[sort_key], reverse=True)
    return json.dumps(processes[: max(1, top_n)])


def _system_status(_arguments: dict[str, Any]) -> str:
    cpu_percent = psutil.cpu_percent(interval=0.3)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    net = psutil.net_io_counters()
    uptime_hours = (time.time() - psutil.boot_time()) / 3600

    return json.dumps(
        {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_gb": round(memory.used / (1024**3), 2),
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "uptime_hours": round(uptime_hours, 1),
            "network_sent_mb": round(net.bytes_sent / (1024**2), 1),
            "network_recv_mb": round(net.bytes_recv / (1024**2), 1),
        }
    )


def _battery_status(_arguments: dict[str, Any]) -> str:
    battery = psutil.sensors_battery()
    if battery is None:
        return json.dumps({"available": False, "note": "No battery sensor (likely a desktop)."})
    return json.dumps(
        {
            "available": True,
            "percent": battery.percent,
            "plugged_in": battery.power_plugged,
            "seconds_left": battery.secsleft if isinstance(battery.secsleft, int) else None,
        }
    )


def _network_status(_arguments: dict[str, Any]) -> str:
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    interfaces = []
    for name, stat in stats.items():
        ips = [addr.address for addr in addrs.get(name, []) if addr.family.name in ("AF_INET", "AF_INET6")]
        interfaces.append({"name": name, "is_up": stat.isup, "speed_mbps": stat.speed, "addresses": ips})
    return json.dumps({"interfaces": interfaces})


def register_system_info_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="list_running_programs",
            description="List the top N running programs on this machine by CPU or memory usage.",
            parameters={
                "type": "object",
                "properties": {
                    "top_n": {"type": "integer", "description": "How many processes to return (default 10)."},
                    "sort_by": {"type": "string", "enum": ["cpu", "memory"]},
                },
            },
            handler=_list_running_programs,
        )
    )
    registry.register(
        Tool(
            name="system_status",
            description="Get overall machine status: CPU, RAM, disk, uptime, and network usage.",
            parameters={"type": "object", "properties": {}},
            handler=_system_status,
        )
    )
    registry.register(
        Tool(
            name="battery_status",
            description="Get battery charge percentage and whether the machine is plugged in.",
            parameters={"type": "object", "properties": {}},
            handler=_battery_status,
        )
    )
    registry.register(
        Tool(
            name="network_status",
            description="List network interfaces with up/down state, link speed, and IP addresses.",
            parameters={"type": "object", "properties": {}},
            handler=_network_status,
        )
    )
