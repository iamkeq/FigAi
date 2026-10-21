"""Rotating file logging. Never logs secret values."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "figai.log"


def setup_logging(level: str = "INFO") -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("figai")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Under pythonw.exe (no console attached) sys.stdout is None; a StreamHandler
    # pointed at it would raise on every log call, so only attach it when a
    # console is actually available (e.g. `--cli` mode run from a terminal).
    if sys.stdout is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"figai.{name}")
