from __future__ import annotations

import json

import pytest
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


# -- Start menu apps, closing ----------------------------------------------------------


START_APPS = {
    "Spotify": r"Spotify.exe",
    "Calculator": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
    "Visual Studio Code": r"C:\Code\Code.exe",
    "Notepad": "notepad",
    "Notepad++": "npp",
}


def make_open(tmp_path: Path, programs=None, apps=START_APPS):
    config = FakeConfig(make_config(tmp_path, programs or {}))
    launched: list[str] = []
    registry = ToolRegistry()
    register_open_program_tool(
        registry, config, launcher=lambda p: launched.append(f"path:{p}"),
        start_apps=lambda: apps, app_launcher=lambda a: launched.append(f"app:{a}"),
    )
    return registry, launched


def open_call(registry, name):
    return json.loads(registry.execute("open_program", json.dumps({"name": name})))


def test_open_falls_back_to_start_menu_apps(tmp_path: Path) -> None:
    registry, launched = make_open(tmp_path)
    assert open_call(registry, "spotify") == {"status": "launched", "program": "Spotify"}
    assert open_call(registry, "calc")["program"] == "Calculator"
    assert open_call(registry, "visual studio code")["program"] == "Visual Studio Code"
    assert launched == ["app:Spotify.exe", "app:Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", r"app:C:\Code\Code.exe"]


def test_open_ambiguous_or_unknown_apps_ask_or_refuse(tmp_path: Path) -> None:
    registry, launched = make_open(tmp_path, apps={"Notepad": "a", "Notepad++": "b", "Spotify": "c"})
    out = open_call(registry, "note")
    assert out["candidates"] == ["Notepad", "Notepad++"] and launched == []
    assert "error" in open_call(registry, "photoshop")


def test_open_prefers_the_allowlist_and_can_disable_start_menu(tmp_path: Path) -> None:
    exe = tmp_path / "steam.exe"
    exe.write_text("")
    registry, launched = make_open(tmp_path, {"steam": str(exe)}, apps={"Steam": "other"})
    assert open_call(registry, "Steam")["status"] == "launched" and launched == [f"path:{exe}"]

    config = FakeConfig(make_config(tmp_path, {}))
    config.app.allow_start_menu_apps = False
    registry2 = ToolRegistry()
    register_open_program_tool(registry2, config, start_apps=lambda: START_APPS, app_launcher=lambda a: None)
    assert "error" in json.loads(registry2.execute("open_program", json.dumps({"name": "spotify"})))


class World:
    """A pretend process table: closing politely removes the process unless it 'ignores' the request."""

    def __init__(self, running: list[str], stubborn: set[str] = frozenset()) -> None:
        self.running = list(running)
        self.stubborn = {s.lower() for s in stubborn}
        self.polite: list[str] = []
        self.killed: list[str] = []

    def processes(self):
        return [(i, name) for i, name in enumerate(self.running, start=100)]

    def close(self, image):
        self.polite.append(image)
        if image.lower() not in self.stubborn:
            self.running = [r for r in self.running if r.lower() != image.lower()]

    def kill(self, image):
        self.killed.append(image)
        self.running = [r for r in self.running if r.lower() != image.lower()]


def make_close(tmp_path: Path, world: World, programs=None):
    from figai.tools.confirm import ConfirmationStore
    from figai.tools.programs import register_close_program_tools

    registry = ToolRegistry()
    register_close_program_tools(
        registry, FakeConfig(make_config(tmp_path, programs or {})), ConfirmationStore(),
        processes=world.processes, closer=world.close, killer=world.kill, sleep=lambda _s: None,
    )

    def call(tool, **args):
        return json.loads(registry.execute(tool, json.dumps(args)))

    return call


def test_close_program_closes_politely_and_reports(tmp_path: Path) -> None:
    world = World(["explorer.exe", "Spotify.exe", "Spotify.exe", "chrome.exe"])
    call = make_close(tmp_path, world)
    assert call("close_program", name="spotify") == {"status": "closed", "program": "Spotify.exe"}
    assert world.polite == ["Spotify.exe"] and world.killed == []
    assert "Nothing running" in call("close_program", name="spotify")["error"]


def test_close_uses_the_allowlist_exe_name(tmp_path: Path) -> None:
    world = World(["Code.exe", "chrome.exe"])
    call = make_close(tmp_path, world, programs={"vs code": r"C:\x\Code.exe"})
    assert call("close_program", name="VS Code")["program"] == "Code.exe"


@pytest.mark.parametrize("name", ["explorer", "python", "pythonw", "svchost", "csrss"])
def test_protected_processes_and_fig_himself_are_never_closed(tmp_path: Path, name: str) -> None:
    world = World(["explorer.exe", "python.exe", "pythonw.exe", "svchost.exe", "csrss.exe"])
    call = make_close(tmp_path, world)
    assert "protected" in call("close_program", name=name)["error"]
    assert world.polite == []


def test_ambiguous_close_asks_which(tmp_path: Path) -> None:
    world = World(["chrome.exe", "chromedriver.exe"])
    call = make_close(tmp_path, world)
    assert call("close_program", name="chro")["candidates"] == ["chrome.exe", "chromedriver.exe"]
    assert world.polite == []


def test_stubborn_app_offers_force_close_which_always_needs_confirmation(tmp_path: Path) -> None:
    world = World(["frozen.exe"], stubborn={"frozen.exe"})
    call = make_close(tmp_path, world)
    out = call("close_program", name="frozen")
    assert out["status"] == "asked_to_close" and "force" in out["note"]
    proposed = call("propose_force_close", name="frozen")
    assert proposed["status"] == "awaiting_confirmation" and world.killed == []
    assert call("resolve_force_close", token=proposed["token"]) == {"status": "killed", "program": "frozen.exe"}
    assert "error" in call("resolve_force_close", token=proposed["token"])


def test_force_close_is_not_in_the_voice_auto_confirm_list() -> None:
    from figai.agent.auto_confirm import RESOLVERS

    assert "force_close" not in RESOLVERS
