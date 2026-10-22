"""The "I'm listening" sound: a short two-note bing played when the wake word is heard."""

from __future__ import annotations

import subprocess
import sys
import time

SETTLE_SECONDS = 0.15  # let the speaker's tail die away before the mic starts recording


def play_listening_chime() -> None:
    try:
        if sys.platform == "win32":
            import winsound

            winsound.Beep(988, 80)
            winsound.Beep(1319, 130)
        elif sys.platform == "darwin":
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False, capture_output=True)
        else:
            return
    except Exception:  # noqa: BLE001 - no sound device must never break listening
        return
    time.sleep(SETTLE_SECONDS)
