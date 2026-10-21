"""search_files: name/extension/recency search restricted to allowlisted folders.
send_file: propose/confirm upload of one of those files through whatever front-end
file-delivery channel is available (e.g. Slack), with a hard size limit."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config
from figai.logging_setup import get_logger
from figai.tools.confirm import ConfirmationStore

logger = get_logger("tools.files")

SEND_FILE_ACTION_TYPE = "send_file"
MAX_SEARCH_RESULTS = 50

FileSender = Callable[[Path], None]


def register_search_files_tool(registry: ToolRegistry, config: Config) -> None:
    def _search(arguments: dict[str, Any]) -> str:
        query = arguments.get("query", "").lower()
        extension = arguments.get("extension")
        if extension:
            extension = "." + extension.lstrip(".").lower()
        modified_within_days = arguments.get("modified_within_days")

        results: list[dict[str, Any]] = []
        for folder in config.app.allowlisted_folders:
            root = Path(folder)
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if len(results) >= MAX_SEARCH_RESULTS:
                    break
                if not path.is_file():
                    continue
                if query and query not in path.name.lower():
                    continue
                if extension and path.suffix.lower() != extension:
                    continue
                if modified_within_days is not None:
                    age_days = (time.time() - path.stat().st_mtime) / 86400
                    if age_days > modified_within_days:
                        continue
                results.append({"path": str(path), "size_bytes": path.stat().st_size})

        return json.dumps({"results": results})

    registry.register(
        Tool(
            name="search_files",
            description="Search allowlisted folders by filename, extension, and/or recency.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Substring to match against the filename."},
                    "extension": {"type": "string", "description": "e.g. 'pdf' or '.pdf'."},
                    "modified_within_days": {"type": "number"},
                },
            },
            handler=_search,
        )
    )


def _is_within_allowlisted_folder(path: Path, allowlisted_folders: list[str]) -> bool:
    resolved = path.resolve()
    for folder in allowlisted_folders:
        folder_resolved = Path(folder).resolve()
        if resolved == folder_resolved or folder_resolved in resolved.parents:
            return True
    return False


def register_send_file_tools(
    registry: ToolRegistry, config: Config, confirmations: ConfirmationStore, file_sender: FileSender | None
) -> None:
    def _propose(arguments: dict[str, Any]) -> str:
        path = Path(arguments["path"])
        if not path.is_file():
            return json.dumps({"error": f"File not found: {path}"})
        if not _is_within_allowlisted_folder(path, config.app.allowlisted_folders):
            return json.dumps({"error": "File is outside the allowlisted folders"})

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > config.app.limits.max_send_file_mb:
            return json.dumps(
                {"error": f"'{path.name}' is {size_mb:.1f} MB, over the {config.app.limits.max_send_file_mb} MB limit"}
            )

        preview = f"Send '{path.name}' ({size_mb:.2f} MB) from {path}"
        token = confirmations.propose(SEND_FILE_ACTION_TYPE, {"path": str(path)}, preview)
        logger.info("Proposed send_file token=%s path=%s", token, path)
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "name": path.name,
                "path": str(path),
                "size_mb": round(size_mb, 2),
                "note": "Tell the user the file name, path, and size, and wait for their explicit yes in a LATER message before calling resolve_send_file.",
            }
        )

    registry.register(
        Tool(
            name="propose_send_file",
            description=(
                "Draft sending a file to the user. Does not send anything -- reports the file's name, path, "
                "and size for the user to explicitly confirm in a later message before resolve_send_file runs."
            ),
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=_propose,
        )
    )

    def _resolve(arguments: dict[str, Any]) -> str:
        token = arguments["token"]
        try:
            payload = confirmations.resolve(token, SEND_FILE_ACTION_TYPE)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})

        if file_sender is None:
            return json.dumps({"error": "No file-delivery channel is connected right now."})

        try:
            file_sender(Path(payload["path"]))
        except Exception as exc:  # noqa: BLE001 - delivery failures must not crash the loop
            logger.exception("send_file delivery failed")
            return json.dumps({"error": str(exc)})

        logger.info("Sent file %s", payload["path"])
        return json.dumps({"status": "sent", "path": payload["path"]})

    registry.register(
        Tool(
            name="resolve_send_file",
            description=(
                "Send a previously proposed file, after the user has explicitly confirmed it in a later "
                "message. Never call this in the same turn the file was proposed."
            ),
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve,
        )
    )
