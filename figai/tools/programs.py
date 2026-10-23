"""open_program / close_program: launch and close apps by the name you'd say.

Opening: first the friendly-name allowlist in config.yaml, then anything in your Windows Start
menu (the apps you have installed -- "open spotify", "open calculator"). No arbitrary shell
commands or paths. Closing: asks the app to close politely (like clicking its X, so unsaved
work gets its usual prompt); a hard kill is a separate, always-confirmed step. A few system
processes (and Fig himself) can never be closed."""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config
from figai.logging_setup import get_logger
from figai.process_utils import quiet_run
from figai.tools.confirm import ConfirmationStore

logger = get_logger("tools.programs")

Launcher = Callable[[str], None]
StartApps = Callable[[], dict[str, str]]  # display name -> AppID
ProcessLister = Callable[[], list[tuple[int, str]]]  # (pid, image name like "Spotify.exe")
Closer = Callable[[str], None]  # image name

FORCE_CLOSE_ACTION = "force_close"
START_APPS_TTL_SECONDS = 600
CLOSE_WAIT_SECONDS = 2.0
PROTECTED_IMAGES = {
    "python", "pythonw", "explorer", "svchost", "csrss", "wininit", "winlogon", "lsass", "services", "system",
    "smss", "dwm", "fontdrvhost", "taskhostw", "sihost", "ctfmon", "registry", "runtimebroker", "searchhost",
    "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "securityhealthservice", "msmpeng",
    "conhost", "wmiprvse", "audiodg", "spoolsv", "lsaiso", "memcompression", "idle",
}


# -- real implementations (swapped for fakes in tests) -------------------------------------


def default_launcher(path: str) -> None:
    subprocess.Popen([path], close_fds=True)  # noqa: S603 - path comes only from the allowlist


def default_app_launcher(app_id: str) -> None:
    # AppIDs come from the machine's own Start menu listing, never from the model's text.
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], close_fds=True)  # noqa: S603,S607


def scan_start_apps() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    try:
        done = quiet_run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(done.stdout or "[]")
    except Exception:  # noqa: BLE001 - no PowerShell / odd output: just no Start menu apps
        logger.exception("Could not list Start menu apps")
        return {}
    if isinstance(data, dict):
        data = [data]
    return {item["Name"]: item["AppID"] for item in data if item.get("Name") and item.get("AppID")}


def list_processes() -> list[tuple[int, str]]:
    import psutil

    found: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"]:
                found.append((proc.info["pid"], proc.info["name"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def close_politely(image: str) -> None:
    if sys.platform == "win32":
        quiet_run(["taskkill", "/IM", image], capture_output=True, text=True, timeout=15)
    else:
        import psutil

        for proc in psutil.process_iter(["name"]):
            if proc.info["name"] == image:
                proc.terminate()


def close_forcefully(image: str) -> None:
    if sys.platform == "win32":
        quiet_run(["taskkill", "/F", "/IM", image], capture_output=True, text=True, timeout=15)
    else:
        import psutil

        for proc in psutil.process_iter(["name"]):
            if proc.info["name"] == image:
                proc.kill()


# -- matching ------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower().removesuffix(".exe"))


def match_name(query: str, names: list[str]) -> tuple[str | None, list[str]]:
    """Best single match for what you said, or (None, candidates) if it's unknown or ambiguous."""
    wanted = _norm(query)
    if not wanted:
        return None, []
    exact = [name for name in names if name.strip().lower() == query.strip().lower()]
    if len(exact) == 1:  # "notepad" must not be ambiguous with "Notepad++" just because punctuation is ignored
        return exact[0], exact
    normalized = {name: _norm(name) for name in names}
    for test in (
        lambda n: n == wanted,
        lambda n: n.startswith(wanted),
        lambda n: wanted in n,
    ):
        hits = [name for name, norm in normalized.items() if norm and test(norm)]
        if len(hits) == 1:
            return hits[0], hits
        if hits:
            return None, sorted(hits)[:5]
    close = difflib.get_close_matches(wanted, list(normalized.values()), n=3, cutoff=0.75)
    hits = [name for name, norm in normalized.items() if norm in close]
    return (hits[0], hits) if len(hits) == 1 else (None, sorted(hits)[:5])


class _StartAppsCache:
    def __init__(self, scanner: StartApps, clock: Callable[[], float] = time.monotonic) -> None:
        self._scanner, self._clock = scanner, clock
        self._apps: dict[str, str] | None = None
        self._at = 0.0

    def get(self) -> dict[str, str]:
        if self._apps is None or self._clock() - self._at > START_APPS_TTL_SECONDS:
            self._apps, self._at = self._scanner(), self._clock()
        return self._apps


# -- tools ---------------------------------------------------------------------------------


def register_open_program_tool(
    registry: ToolRegistry,
    config: Config,
    launcher: Launcher = default_launcher,
    start_apps: StartApps = scan_start_apps,
    app_launcher: Launcher = default_app_launcher,
) -> None:
    cache = _StartAppsCache(start_apps)

    def _open_program(arguments: dict[str, Any]) -> str:
        name = arguments["name"].strip().lower()
        allowlist = config.app.allowlisted_programs

        if name in allowlist:
            path = allowlist[name]
            if not Path(path).exists():
                return json.dumps({"error": f"Configured path for '{name}' does not exist: {path}"})
            try:
                launcher(path)
            except OSError as exc:
                logger.error("Failed to launch %s (%s): %s", name, path, exc)
                return json.dumps({"error": str(exc)})
            logger.info("Launched allowlisted program '%s'", name)
            return json.dumps({"status": "launched", "program": name})

        if getattr(config.app, "allow_start_menu_apps", True):
            apps = cache.get()
            best, candidates = match_name(name, list(apps))
            if best is not None:
                try:
                    app_launcher(apps[best])
                except OSError as exc:
                    return json.dumps({"error": str(exc)})
                logger.info("Launched Start menu app '%s' (asked for '%s')", best, name)
                return json.dumps({"status": "launched", "program": best})
            if candidates:
                return json.dumps({"error": f"'{name}' could mean several apps; which one?", "candidates": candidates})

        return json.dumps(
            {"error": f"I can't find an app called '{name}'.", "allowlisted": sorted(allowlist)}
        )

    registry.register(
        Tool(
            name="open_program",
            description=(
                "Open an app on this PC by the name the user says (e.g. 'spotify', 'calculator', 'steam'): "
                "programs allowlisted in config.yaml first, then anything in the Start menu."
            ),
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=_open_program,
        )
    )


def register_close_program_tools(
    registry: ToolRegistry,
    config: Config,
    confirmations: ConfirmationStore,
    processes: ProcessLister = list_processes,
    closer: Closer = close_politely,
    killer: Closer = close_forcefully,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    def _resolve_images(query: str) -> tuple[list[str], dict[str, Any] | None]:
        """Running image names for what you said, or an error dict."""
        running = sorted({image for _pid, image in processes()})
        allowlist = config.app.allowlisted_programs
        wanted = query.strip().lower()
        if wanted in allowlist:
            wanted = Path(allowlist[wanted]).stem  # "steam" -> steam.exe's real process name
        names = {image: image for image in running if len(_norm(image)) >= 3}
        best, candidates = match_name(wanted, list(names))
        if best is None:
            if candidates:
                return [], {"error": f"'{query}' could mean several running programs; which one?", "candidates": candidates}
            return [], {"error": f"Nothing running matches '{query}'."}
        if _norm(best) in PROTECTED_IMAGES:
            return [], {"error": f"{best} is a protected system process (or Fig himself); I won't close it."}
        return [best], None

    def _still_running(image: str) -> bool:
        return any(i.lower() == image.lower() for _pid, i in processes())

    def _close(arguments: dict[str, Any]) -> str:
        images, error = _resolve_images(arguments["name"])
        if error:
            return json.dumps(error)
        image = images[0]
        try:
            closer(image)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Couldn't ask {image} to close: {exc}"})
        sleep(CLOSE_WAIT_SECONDS)
        if _still_running(image):
            return json.dumps(
                {
                    "status": "asked_to_close",
                    "program": image,
                    "note": "It's still running -- it may be showing a save prompt or ignoring the request. "
                    "Offer a force close (propose_force_close) if the user wants it killed.",
                }
            )
        logger.info("Closed %s", image)
        return json.dumps({"status": "closed", "program": image})

    registry.register(
        Tool(
            name="close_program",
            description=(
                "Close a running app by the name the user says ('close spotify'). Asks it to close politely, "
                "like clicking the X, so unsaved work gets its normal prompt. Never use for system processes."
            ),
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=_close,
        )
    )

    def _propose_force(arguments: dict[str, Any]) -> str:
        images, error = _resolve_images(arguments["name"])
        if error:
            return json.dumps(error)
        preview = f"FORCE-KILL {images[0]} (unsaved work in it will be lost)"
        token = confirmations.propose(FORCE_CLOSE_ACTION, {"image": images[0]}, preview)
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": "Wait for the user's explicit yes in a LATER message before calling resolve_force_close.",
            }
        )

    registry.register(
        Tool(
            name="propose_force_close",
            description=(
                "Draft force-killing a stuck app that didn't close politely. Always asks the user first, "
                "even by voice, because unsaved work is lost."
            ),
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=_propose_force,
        )
    )

    def _resolve_force(arguments: dict[str, Any]) -> str:
        try:
            payload = confirmations.resolve(arguments["token"], FORCE_CLOSE_ACTION)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})
        if _norm(payload["image"]) in PROTECTED_IMAGES:
            return json.dumps({"error": "That's a protected process."})
        try:
            killer(payload["image"])
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": "killed", "program": payload["image"]})

    registry.register(
        Tool(
            name="resolve_force_close",
            description="Force-kill the app the user confirmed in a later message (token from propose_force_close).",
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve_force,
        )
    )
