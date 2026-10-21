"""System tray icon. The fig is drawn at runtime with Pillow -- no image asset
needed. Menu: Status, Open Logs, Restart, Quit."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from figai.logging_setup import LOG_DIR, get_logger

logger = get_logger("tray")

BODY_COLOR = (142, 68, 173, 255)  # plum/purple
SHADOW_COLOR = (108, 52, 131, 255)
LEAF_COLOR = (67, 160, 71, 255)  # green
STEM_COLOR = (93, 64, 55, 255)  # brown


def generate_fig_icon(size: int = 64) -> Image.Image:
    """Draws a simple fig: a purple teardrop body, a brown stem, a green leaf."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    cx, cy = size * 0.5, size * 0.6
    radius = size * 0.34

    # Teardrop: round bottom + a narrowing triangle up to the stem.
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=BODY_COLOR)
    draw.polygon(
        [
            (cx - radius * 0.55, cy - radius * 0.65),
            (cx + radius * 0.55, cy - radius * 0.65),
            (cx, size * 0.14),
        ],
        fill=BODY_COLOR,
    )

    # Shadow crescent for a little depth.
    draw.ellipse(
        [cx - radius * 0.85, cy - radius * 0.1, cx + radius * 0.45, cy + radius * 0.95],
        fill=SHADOW_COLOR,
    )

    # Stem + leaf.
    stem_width = size * 0.05
    draw.rectangle([cx - stem_width / 2, size * 0.08, cx + stem_width / 2, size * 0.18], fill=STEM_COLOR)
    draw.polygon(
        [(cx, size * 0.13), (cx + size * 0.22, size * 0.07), (cx + size * 0.08, size * 0.23)],
        fill=LEAF_COLOR,
    )

    return image


def default_open_logs() -> None:
    path = str(LOG_DIR.resolve())
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opens Explorer on a path we control
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def default_restart() -> None:
    logger.info("Restart requested via tray icon")
    os.execv(sys.executable, [sys.executable, "-m", "figai"])


def default_quit() -> None:
    logger.info("Quit requested via tray icon")
    os._exit(0)


def build_tray_icon(
    status_provider: Callable[[], str],
    on_restart: Callable[[], None] = default_restart,
    on_open_logs: Callable[[], None] = default_open_logs,
    on_quit: Callable[[], None] = default_quit,
) -> pystray.Icon:
    def _status(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        icon.notify(status_provider())

    def _open_logs(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        on_open_logs()

    def _restart(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        on_restart()

    def _quit(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        icon.stop()
        on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("Status", _status),
        pystray.MenuItem("Open Logs", _open_logs),
        pystray.MenuItem("Restart", _restart),
        pystray.MenuItem("Quit", _quit),
    )
    return pystray.Icon("figai", generate_fig_icon(), "Fig", menu)


def run_tray_icon(
    status_provider: Callable[[], str],
    on_restart: Callable[[], None] = default_restart,
    on_open_logs: Callable[[], None] = default_open_logs,
    on_quit: Callable[[], None] = default_quit,
) -> None:
    build_tray_icon(status_provider, on_restart, on_open_logs, on_quit).run()
