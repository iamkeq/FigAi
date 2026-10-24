"""Spoken feedback. Short fixed phrases ("Yes, sir." / "All set, sir." ...) are rendered once with
Windows' built-in voice (System.Speech, no extra software, works offline) into WAV files and then
played instantly with winsound; anything else (reading a confirmation aloud) is synthesized on
demand. Everywhere except Windows this quietly does nothing."""

from __future__ import annotations

import hashlib
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Callable

from figai.logging_setup import get_logger
from figai.process_utils import quiet_run

logger = get_logger("voice.speech")

Synth = Callable[[str, Path, "str | None", int], bool]  # (text, wav path, voice name, rate) -> ok
Player = Callable[[Path], None]

POWERSHELL_SPEAK = (
    "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "if ($env:FIG_TTS_VOICE) { $s.SelectVoice($env:FIG_TTS_VOICE) }; $s.Rate = [int]$env:FIG_TTS_RATE; "
    "$s.SetOutputToWaveFile($env:FIG_TTS_PATH); $s.Speak($env:FIG_TTS_TEXT); $s.Dispose()"
)
MAX_SPOKEN_CHARS = 300


def default_synth(text: str, path: Path, voice: str | None, rate: int) -> bool:
    if sys.platform != "win32":
        return False
    env = {**os.environ, "FIG_TTS_TEXT": text, "FIG_TTS_PATH": str(path), "FIG_TTS_VOICE": voice or "", "FIG_TTS_RATE": str(rate)}
    try:
        done = quiet_run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SPEAK],
            env=env, capture_output=True, text=True, timeout=60,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Speech synthesis failed")
        return False
    if done.returncode != 0:
        logger.warning("Speech synthesis failed: %s", (done.stderr or "").strip()[:200])
    return done.returncode == 0 and path.exists()


def default_player(path: Path) -> None:
    if sys.platform != "win32":
        return
    import winsound

    winsound.PlaySound(str(path), winsound.SND_FILENAME)  # blocks until done; Speaker plays on its own thread


def phrases(honorific: str) -> dict[str, str]:
    tail = f", {honorific}" if honorific.strip() else ""
    return {
        "ack": f"Yes{tail}.",
        "done": f"All set{tail}.",
        "fail": f"I'm afraid that didn't work{tail}.",
        "confirm": f"Shall I proceed{tail}?",
        "cancelled": f"Very well{tail}.",
    }


class Speaker:
    """Phrases play one after another on a background thread, so "Yes, sir." is never cut off by
    "All set, sir." and a slow synthesis never holds up the command being run."""

    def __init__(
        self,
        cache_dir: Path,
        honorific: str = "sir",
        voice: str | None = None,
        rate: int = 0,
        synth: Synth = default_synth,
        player: Player = default_player,
        background: bool = True,
    ) -> None:
        self._cache = cache_dir
        self._phrases = phrases(honorific)
        self._voice, self._rate = voice, rate
        self._synth, self._player = synth, player
        self._background = background
        self._jobs: queue.Queue[Callable[[], None]] = queue.Queue()
        self._worker: threading.Thread | None = None

    def _path(self, text: str) -> Path:
        key = hashlib.sha1(f"{text}|{self._voice}|{self._rate}".encode()).hexdigest()[:16]
        return self._cache / f"{key}.wav"

    def prepare(self) -> None:
        """Renders the fixed phrases that aren't cached yet. Run it in a background thread."""
        self._cache.mkdir(parents=True, exist_ok=True)
        for text in self._phrases.values():
            path = self._path(text)
            if not path.exists():
                self._synth(text, path, self._voice, self._rate)

    def play(self, key: str) -> None:
        """Plays a fixed phrase ("ack", "done", "fail", "confirm", "cancelled") if it has been rendered."""
        text = self._phrases.get(key)
        if text is None:
            return
        self._submit(lambda: self._play_if_exists(self._path(text)))

    def say(self, text: str) -> None:
        """Speaks arbitrary text (e.g. what Fig is about to do)."""
        text = " ".join(text.split())[:MAX_SPOKEN_CHARS]
        if not text:
            return

        def work() -> None:
            self._cache.mkdir(parents=True, exist_ok=True)
            path = self._path(text)
            if not path.exists() and not self._synth(text, path, self._voice, self._rate):
                return
            self._play_if_exists(path)

        self._submit(work)

    def say_then(self, text: str, key: str) -> None:
        """Speaks `text` followed by the fixed phrase `key` (a preview, then "Shall I proceed, sir?")."""
        self.say(f"{' '.join(text.split())[:MAX_SPOKEN_CHARS]} {self._phrases.get(key, '')}".strip())

    def _submit(self, job: Callable[[], None]) -> None:
        if not self._background:
            self._run(job)
            return
        self._jobs.put(job)
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._drain, name="fig-speaker", daemon=True)
            self._worker.start()

    def _drain(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=30)
            except queue.Empty:
                return
            self._run(job)

    @staticmethod
    def _run(job: Callable[[], None]) -> None:
        try:
            job()
        except Exception:  # noqa: BLE001 - no sound device must never break a command
            logger.exception("Spoken feedback failed")

    def _play_if_exists(self, path: Path) -> None:
        if path.exists():
            self._player(path)
