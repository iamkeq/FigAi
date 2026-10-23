"""focus_window / list_windows: "bring discord up" -- find the window by the app's name, restore it if
it's minimized, and make it the foreground window. If the app isn't running at all, launch it (via
open_program), since that's what you want when you ask for it by name. Windows only; elsewhere it
reports that honestly."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.logging_setup import get_logger
from figai.tools.platform_errors import UnsupportedPlatformError

logger = get_logger("tools.window_focus")

# Spoken names that differ from the process name
ALIASES = {"vscode": "code", "visualstudiocode": "code", "googlechrome": "chrome", "microsoftedge": "msedge", "edge": "msedge"}
MAX_LISTED = 40


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str  # e.g. "Discord.exe"
    minimized: bool = False


Lister = Callable[[], list[WindowInfo]]
Focuser = Callable[[int], bool]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower().removesuffix(".exe"))


def pick_window(query: str, windows: list[WindowInfo]) -> tuple[WindowInfo | None, list[str]]:
    """Best window for what you said. Windows come front-to-back (most recently used first), so
    for an app with several windows the first is the one you used last. Returns (None, candidates)
    when two different apps match equally well."""
    wanted = _norm(query)
    wanted = ALIASES.get(wanted, wanted)
    if len(wanted) < 2:
        return None, []
    scored: list[tuple[int, WindowInfo]] = []
    for window in windows:
        process, title = _norm(window.process), _norm(window.title)
        if process == wanted:
            score = 4
        elif len(process) >= 3 and (process.startswith(wanted) or wanted.startswith(process)):
            score = 3
        elif wanted in title:
            score = 2
        elif len(process) >= 3 and wanted in process:
            score = 1
        else:
            continue
        scored.append((score, window))
    if not scored:
        return None, []
    best = max(score for score, _ in scored)
    top = [w for score, w in scored if score == best]
    processes = {w.process.lower() for w in top}
    if len(processes) > 1:
        return None, sorted({w.process.removesuffix(".exe") for w in top})[:5]
    return top[0], []


# -- real Windows implementation ------------------------------------------------------------


def list_windows_windows() -> list[WindowInfo]:
    import ctypes
    from ctypes import wintypes

    import psutil

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
    found: list[WindowInfo] = []
    names: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            exstyle = user32.GetWindowLongW(hwnd, -20)
            if exstyle & 0x80 and not exstyle & 0x40000:  # tool window that isn't an app window
                return True
            cloaked = wintypes.DWORD()
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in names:
                try:
                    names[pid.value] = psutil.Process(pid.value).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    names[pid.value] = "?"
            found.append(WindowInfo(int(hwnd), buffer.value, names[pid.value], bool(user32.IsIconic(hwnd))))
        except Exception:  # noqa: BLE001 - one odd window must not stop the scan
            logger.exception("Skipping a window")
        return True

    user32.EnumWindows(visit, 0)
    return found


def focus_window_windows(hwnd: int) -> bool:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)  # SW_RESTORE if minimized, else SW_SHOW
    # Windows only lets the foreground process steal focus; a tap of ALT counts as input and lifts that.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True
    # Fallback: share input state with the current foreground thread for a moment.
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    this_thread = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(this_thread, foreground_thread, True)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        user32.AttachThreadInput(this_thread, foreground_thread, False)
    return user32.GetForegroundWindow() == hwnd


def set_window_state_windows(hwnd: int, state: str) -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.ShowWindow(hwnd, {"minimize": 6, "maximize": 3, "restore": 9}[state])


def minimize_all_windows() -> None:
    """Win+M: minimize every window (leaves the desktop showing)."""
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for key, flags in ((0x5B, 0), (0x4D, 0), (0x4D, 2), (0x5B, 2)):
        user32.keybd_event(key, 0, flags, 0)


def default_lister() -> list[WindowInfo]:
    if sys.platform == "win32":
        return list_windows_windows()
    raise UnsupportedPlatformError(f"Window control is not supported on {sys.platform}")


def default_state_setter(hwnd: int, state: str) -> None:
    if sys.platform == "win32":
        return set_window_state_windows(hwnd, state)
    raise UnsupportedPlatformError(f"Window control is not supported on {sys.platform}")


def default_minimize_all() -> None:
    if sys.platform == "win32":
        return minimize_all_windows()
    raise UnsupportedPlatformError(f"Window control is not supported on {sys.platform}")


def default_focuser(hwnd: int) -> bool:
    if sys.platform == "win32":
        return focus_window_windows(hwnd)
    raise UnsupportedPlatformError(f"Window control is not supported on {sys.platform}")


# -- tools ----------------------------------------------------------------------------------


CURRENT_WORDS = {"this", "current", "active", "the current window", "this window", "current window", "active window", "the active window"}
ALL_WORDS = {"all", "everything", "all windows", "all of them", "every window"}


def register_window_focus_tools(
    registry: ToolRegistry,
    lister: Lister = default_lister,
    focuser: Focuser = default_focuser,
    state_setter: Callable[[int, str], None] = default_state_setter,
    minimize_all: Callable[[], None] = default_minimize_all,
) -> None:
    def _focus(arguments: dict[str, Any]) -> str:
        name = arguments["name"].strip()
        try:
            window, candidates = pick_window(name, lister())
        except UnsupportedPlatformError as exc:
            return json.dumps({"error": str(exc)})
        if candidates:
            return json.dumps({"error": f"'{name}' could mean several apps; which one?", "candidates": candidates})
        if window is None:
            # Not running (or nothing visible): launch it, which is what asking for it by name means.
            if "open_program" in registry:
                opened = json.loads(registry.execute("open_program", json.dumps({"name": name})))
                if opened.get("status") == "launched":
                    return json.dumps({"status": "launched", "program": opened.get("program", name), "note": "It wasn't open, so I started it."})
                return json.dumps({"error": f"There's no open window for '{name}', and I couldn't start it either: {opened.get('error', 'unknown')}"})
            return json.dumps({"error": f"There's no open window matching '{name}'."})
        try:
            focused = focuser(window.hwnd)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Couldn't bring {window.process} forward: {exc}"})
        label = window.process.removesuffix(".exe")
        if not focused:
            return json.dumps(
                {
                    "status": "asked",
                    "window": window.title,
                    "note": f"I asked Windows to show {label}, but it may still be behind another window (Windows sometimes blocks this).",
                }
            )
        logger.info("Focused %s (%s)", label, window.title[:60])
        return json.dumps({"status": "focused", "app": label, "window": window.title, "was_minimized": window.minimized})

    registry.register(
        Tool(
            name="focus_window",
            description=(
                "Bring an app's window to the front and make it the active window, restoring it if it's minimized "
                "('bring discord up', 'switch to chrome', 'pull up spotify'). If the app isn't open it gets started."
            ),
            parameters={"type": "object", "properties": {"name": {"type": "string", "description": "The app, e.g. 'discord'."}}, "required": ["name"]},
            handler=_focus,
        )
    )

    def _list(_arguments: dict[str, Any]) -> str:
        try:
            windows = lister()
        except UnsupportedPlatformError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(
            {
                "windows": [
                    {"app": w.process.removesuffix(".exe"), "title": w.title[:80], "minimized": w.minimized}
                    for w in windows[:MAX_LISTED]
                ]
            }
        )

    registry.register(
        Tool(
            name="list_windows",
            description="List the open windows on this PC (app, title, minimized or not), most recently used first.",
            parameters={"type": "object", "properties": {}},
            handler=_list,
        )
    )

    def _change_state(name: str, state: str) -> dict[str, Any]:
        past = {"minimize": "minimized", "maximize": "maximized"}[state]
        windows = lister()
        if state == "minimize" and name.strip().lower() in ALL_WORDS:
            minimize_all()
            return {"status": past, "app": "all windows"}
        if name.strip().lower() in CURRENT_WORDS or not name.strip():
            window = windows[0] if windows else None  # front-most window is first in the list
            candidates: list[str] = []
        else:
            window, candidates = pick_window(name, windows)
        if candidates:
            return {"error": f"'{name}' could mean several apps; which one?", "candidates": candidates}
        if window is None:
            return {"error": f"There's no open window matching '{name}'."}
        state_setter(window.hwnd, state)
        logger.info("%s %s (%s)", past.capitalize(), window.process, window.title[:60])
        return {"status": past, "app": window.process.removesuffix(".exe"), "window": window.title}

    def _state_handler(state: str):
        def handler(arguments: dict[str, Any]) -> str:
            try:
                return json.dumps(_change_state(arguments.get("name", ""), state))
            except UnsupportedPlatformError as exc:
                return json.dumps({"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"error": f"Couldn't {state} it: {exc}"})

        return handler

    registry.register(
        Tool(
            name="minimize_window",
            description=(
                "Minimize an app's window to the taskbar ('minimize discord'). name 'this'/'current' (or omitted) means the "
                "window in front; 'all'/'everything' minimizes every window."
            ),
            parameters={"type": "object", "properties": {"name": {"type": "string"}}},
            handler=_state_handler("minimize"),
        )
    )
    registry.register(
        Tool(
            name="maximize_window",
            description="Maximize an app's window to fill the screen ('maximize chrome'). name 'this'/'current' means the front window.",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}},
            handler=_state_handler("maximize"),
        )
    )
