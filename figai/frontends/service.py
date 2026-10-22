"""Background service entry: tray icon in its own thread, Slack bot blocking the
main thread. Fig's status for the tray reflects the live Slack router once ready."""

from __future__ import annotations

import threading
import time
from typing import Any

from figai.config import Config
from figai.logging_setup import get_logger

logger = get_logger("service")

SESSION_WAIT_SECONDS = 120


def run_service(config: Config) -> None:
    from figai.frontends.slack_bot import run_slack_bot
    from figai.frontends.tray import run_tray_icon
    from figai.frontends.voice import run_voice_frontend

    logger.info("Starting Fig background service")
    state: dict[str, Any] = {}

    def _on_ready(router: Any) -> None:
        state["router"] = router

    def _status_provider() -> str:
        router = state.get("router")
        return router.status_text() if router else "Fig is starting..."

    def _run_tray_icon_safely() -> None:
        try:
            run_tray_icon(status_provider=_status_provider)
        except Exception:
            # macOS's AppKit backend requires the main thread; Windows's win32
            # backend (the real deployment target) has no such restriction. Either
            # way, a tray failure must never take down the Slack bot.
            logger.exception("Tray icon failed to start -- continuing without it")

    def _run_voice_frontend_safely() -> None:
        try:
            if not config.app.voice.enabled:
                return run_voice_frontend(config)
            # Share the Slack session, so voice can do everything Slack can (post, remind, screenshot...).
            deadline = time.monotonic() + SESSION_WAIT_SECONDS
            while "router" not in state and time.monotonic() < deadline:
                time.sleep(0.5)
            router = state.get("router")
            run_voice_frontend(config, session=router.session if router else None)
        except Exception:
            # Missing mic, missing models, no internet for the first model
            # download, etc. -- voice is opt-in and must never take down Slack.
            logger.exception("Voice front-end failed to start -- continuing without it")

    threading.Thread(target=_run_tray_icon_safely, daemon=True).start()
    threading.Thread(target=_run_voice_frontend_safely, daemon=True).start()

    run_slack_bot(config, on_ready=_on_ready)
