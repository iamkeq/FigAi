"""Entry point: `python -m figai` (background) or `python -m figai --cli` (terminal)."""

from __future__ import annotations

import argparse
import sys

from figai.config import Config, ConfigError
from figai.logging_setup import get_logger, setup_logging
from figai.process_utils import acquire_single_instance, detach_console, ensure_std_streams, install_excepthooks


def main() -> int:
    parser = argparse.ArgumentParser(prog="figai")
    parser.add_argument(
        "--cli", action="store_true", help="Run an interactive terminal session instead of the background service."
    )
    args = parser.parse_args()

    # Background mode must never keep a window open (see detach_console); the dead
    # std streams are replaced with a log file, along with crash tracebacks.
    if not args.cli and detach_console():
        sys.stdout = sys.stderr = None
    ensure_std_streams()
    install_excepthooks()

    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.app.log_level)
    logger = get_logger("main")
    logger.info("Fig starting (cli=%s)", args.cli)

    if not args.cli and not acquire_single_instance():
        logger.error("Another Fig is already running -- exiting so Slack doesn't get duplicate replies")
        return 0

    if args.cli:
        from figai.frontends.cli import run_cli

        run_cli(config)
    else:
        from figai.frontends.service import run_service

        run_service(config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
