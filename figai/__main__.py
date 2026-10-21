"""Entry point: `python -m figai` (background) or `python -m figai --cli` (terminal)."""

from __future__ import annotations

import argparse
import sys

from figai.config import Config, ConfigError
from figai.logging_setup import get_logger, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="figai")
    parser.add_argument(
        "--cli", action="store_true", help="Run an interactive terminal session instead of the background service."
    )
    args = parser.parse_args()

    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.app.log_level)
    logger = get_logger("main")
    logger.info("Fig starting (cli=%s)", args.cli)

    if args.cli:
        from figai.frontends.cli import run_cli

        run_cli(config)
    else:
        from figai.frontends.service import run_service

        run_service(config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
