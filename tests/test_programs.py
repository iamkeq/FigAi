from __future__ import annotations

import json
from pathlib import Path

from figai.agent.tools import ToolRegistry
from figai.config import AppConfig
from figai.tools.programs import register_open_program_tool


def make_config(tmp_path: Path, programs: dict[str, str]) -> AppConfig:
    return AppConfig.model_validate(
        {
            "models": {"default_chat": "x", "vision": "y"},
            "vault": {"ssh_host_alias": "v", "remote_path": "/v", "local_path": str(tmp_path / "vault")},
            "allowlisted_programs": programs,
        }
    )


class FakeConfig:
    def __init__(self, app: AppConfig) -> None:
        self.app = app


def test_open_program_launches_allowlisted_executable(tmp_path: Path) -> None:
    fake_exe = tmp_path / "steam.exe"
    fake_exe.write_text("", encoding="utf-8")
    config = FakeConfig(make_config(tmp_path, {"steam": str(fake_exe)}))

    launched: list[str] = []
    registry = ToolRegistry()
    register_open_program_tool(registry, config, launcher=launched.append)

    result = json.loads(registry.execute("open_program", json.dumps({"name": "Steam"})))

    assert result == {"status": "launched", "program": "steam"}
    assert launched == [str(fake_exe)]


def test_open_program_rejects_non_allowlisted_name(tmp_path: Path) -> None:
    config = FakeConfig(make_config(tmp_path, {}))
    registry = ToolRegistry()
    register_open_program_tool(registry, config, launcher=lambda path: None)

    result = json.loads(registry.execute("open_program", json.dumps({"name": "cmd"})))

    assert "error" in result


def test_open_program_rejects_missing_configured_path(tmp_path: Path) -> None:
    config = FakeConfig(make_config(tmp_path, {"obs": str(tmp_path / "does_not_exist.exe")}))
    registry = ToolRegistry()
    register_open_program_tool(registry, config, launcher=lambda path: None)

    result = json.loads(registry.execute("open_program", json.dumps({"name": "obs"})))

    assert "error" in result
