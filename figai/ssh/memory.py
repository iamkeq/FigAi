"""memory_read / memory_write / memory_search: the remote Obsidian vault first,
falling back to the fully independent local vault when the remote one is
unreachable. The two vaults are never merged or synced automatically."""

from __future__ import annotations

import json
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config
from figai.logging_setup import get_logger
from figai.ssh.local_vault import LocalVaultBackend
from figai.ssh.remote_vault import RemoteVaultBackend
from figai.ssh.vault_common import VaultPathError, normalize_subfolder
from figai.tools.confirm import ConfirmationStore

logger = get_logger("ssh.memory")

RemoteFactory = Callable[[], RemoteVaultBackend]
VaultBackend = LocalVaultBackend | RemoteVaultBackend
OVERWRITE_ACTION_TYPE = "memory_overwrite"
MOVE_ACTION_TYPE = "memory_move"
DELETE_ACTION_TYPE = "memory_delete"


def with_fallback(
    remote_factory: RemoteFactory, local: LocalVaultBackend, operation: Callable[[VaultBackend], Any]
) -> tuple[Any, str]:
    """Runs `operation` against the remote vault; on any connectivity failure
    (including a missing/misconfigured host), falls back to the local vault for
    that one call. A VaultPathError is a rejection, not a connectivity failure,
    so it propagates instead of silently retrying against the other backend. Same for a
    missing note (FileNotFoundError is an OSError, but the vault answered)."""
    try:
        remote = remote_factory()
        return operation(remote), "remote"
    except (VaultPathError, FileNotFoundError):
        raise  # rejections and "no such note" are answers, not connectivity failures
    except OSError as exc:
        logger.warning("Remote vault unreachable (%s); falling back to local vault", exc)
        return operation(local), "local"


def load_index_note(remote_factory: RemoteFactory, local: LocalVaultBackend, index_note: str) -> tuple[str | None, str | None]:
    try:
        content, backend = with_fallback(remote_factory, local, lambda b: b.read(index_note))
        return content, backend
    except FileNotFoundError:
        return None, None
    except VaultPathError:
        return None, None


def _note_exists(remote_factory: RemoteFactory, local: LocalVaultBackend, path: str) -> bool:
    try:
        with_fallback(remote_factory, local, lambda b: b.read(path))
        return True
    except FileNotFoundError:
        return False


def _writable_hint(subfolder: str) -> str:
    cleaned = normalize_subfolder(subfolder)
    return f"Restricted to the '{cleaned}/' subfolder." if cleaned else "Allowed anywhere in the vault."


def register_memory_tools(
    registry: ToolRegistry,
    config: Config,
    remote_factory: RemoteFactory,
    local: LocalVaultBackend,
    confirmations: ConfirmationStore,
) -> None:
    def _read(arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        try:
            content, backend = with_fallback(remote_factory, local, lambda b: b.read(path))
        except (VaultPathError, FileNotFoundError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"backend": backend, "content": content})

    registry.register(
        Tool(
            name="memory_read",
            description=(
                "Read a note from the Obsidian vault by its path relative to the vault root "
                "(e.g. 'AI/index.md'). Reads may cover the whole vault."
            ),
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=_read,
        )
    )

    def _write_now(path: str, content: str) -> str:
        try:
            _, backend = with_fallback(remote_factory, local, lambda b: b.write(path, content))
        except VaultPathError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": "written", "backend": backend})

    def _write(arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        content = arguments["content"]

        try:
            exists = _note_exists(remote_factory, local, path)
        except VaultPathError as exc:
            return json.dumps({"error": str(exc)})
        if exists:
            preview = f"Overwrite existing vault note '{path}'"
            token = confirmations.propose(OVERWRITE_ACTION_TYPE, {"path": path, "content": content}, preview)
            logger.info("Proposed memory overwrite token=%s path=%s", token, path)
            return json.dumps(
                {
                    "status": "awaiting_confirmation",
                    "token": token,
                    "preview": preview,
                    "note": (
                        "This note already exists. Wait for the user's explicit yes in a LATER "
                        "message before calling resolve_memory_write."
                    ),
                }
            )

        return _write_now(path, content)

    registry.register(
        Tool(
            name="memory_write",
            description=(
                f"Write a NEW note into the vault at a path relative to the vault root with no leading "
                f"slash (e.g. 'homelab/FigAxe2.md'; use memory_list to find folders). "
                f"{_writable_hint(config.app.vault.remote_writable_subfolder)} If the note already "
                "exists, this instead returns a confirmation token -- use resolve_memory_write "
                "after the user explicitly confirms the overwrite."
            ),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            handler=_write,
        )
    )

    def _resolve_write(arguments: dict[str, Any]) -> str:
        token = arguments["token"]
        try:
            payload = confirmations.resolve(token, OVERWRITE_ACTION_TYPE)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})
        return _write_now(payload["path"], payload["content"])

    registry.register(
        Tool(
            name="resolve_memory_write",
            description=(
                "Overwrite an existing vault note, after the user has explicitly confirmed it in a "
                "later message. Never call this in the same turn memory_write proposed it."
            ),
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve_write,
        )
    )

    def _propose_change(action_type: str, payload: dict[str, Any], preview: str) -> str:
        token = confirmations.propose(action_type, payload, preview)
        logger.info("Proposed %s token=%s %s", action_type, token, preview)
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": "Wait for the user's explicit yes in a LATER message before calling resolve_memory_change.",
            }
        )

    def _move(arguments: dict[str, Any]) -> str:
        source, target = arguments["from_path"], arguments["to_path"]
        try:
            local._resolve(source, for_write=True)  # cheap path-rule check before bothering the user
            local._resolve(target, for_write=True)
        except VaultPathError as exc:
            return json.dumps({"error": str(exc)})
        return _propose_change(
            MOVE_ACTION_TYPE, {"from": source, "to": target}, f"Move vault note/folder '{source}' to '{target}'"
        )

    registry.register(
        Tool(
            name="memory_move",
            description=(
                "Draft moving or renaming a vault note or folder (paths relative to the vault root; missing "
                "folders are created; refuses to overwrite). Does nothing until the user confirms in a later "
                "message, then call resolve_memory_change."
            ),
            parameters={
                "type": "object",
                "properties": {"from_path": {"type": "string"}, "to_path": {"type": "string"}},
                "required": ["from_path", "to_path"],
            },
            handler=_move,
        )
    )

    def _delete(arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        try:
            local._resolve(path, for_write=True)
        except VaultPathError as exc:
            return json.dumps({"error": str(exc)})
        return _propose_change(
            DELETE_ACTION_TYPE,
            {"path": path},
            f"Delete vault note/folder '{path}' (moved to .fig-trash, so it can be recovered)",
        )

    registry.register(
        Tool(
            name="memory_delete",
            description=(
                "Draft deleting a vault note or folder. It is moved into the vault's .fig-trash folder, not "
                "erased, so it can be recovered. Does nothing until the user confirms in a later message, "
                "then call resolve_memory_change."
            ),
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=_delete,
        )
    )

    def _resolve_change(arguments: dict[str, Any]) -> str:
        token = arguments["token"]
        for action_type in (MOVE_ACTION_TYPE, DELETE_ACTION_TYPE):
            try:
                payload = confirmations.resolve(token, action_type)
            except TimeoutError as exc:
                return json.dumps({"error": str(exc)})
            except KeyError:
                continue
            try:
                if action_type == MOVE_ACTION_TYPE:
                    _, backend = with_fallback(remote_factory, local, lambda b: b.move(payload["from"], payload["to"]))
                    return json.dumps({"status": "moved", "backend": backend, "from": payload["from"], "to": payload["to"]})
                destination, backend = with_fallback(remote_factory, local, lambda b: b.trash(payload["path"]))
                return json.dumps({"status": "deleted", "backend": backend, "recoverable_at": destination})
            except (VaultPathError, FileNotFoundError) as exc:
                return json.dumps({"error": f"Nothing changed: {exc}"})
        return json.dumps({"error": f"No pending move/delete for token '{token}'"})

    registry.register(
        Tool(
            name="resolve_memory_change",
            description=(
                "Carry out a vault move or delete after the user explicitly confirmed it in a later message "
                "(token from memory_move or memory_delete)."
            ),
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve_change,
        )
    )

    def _list(arguments: dict[str, Any]) -> str:
        path = arguments.get("path", "")
        try:
            entries, backend = with_fallback(remote_factory, local, lambda b: b.list_dir(path))
        except (VaultPathError, FileNotFoundError) as exc:
            return json.dumps({"error": f"No such folder or not allowed: {exc}"})
        return json.dumps({"backend": backend, "path": path or "/", "entries": entries[:200]})

    registry.register(
        Tool(
            name="memory_list",
            description=(
                "List the files and folders directly inside a vault folder (path relative to the vault root, "
                "no leading slash; omit for the root). Folders end with '/'. Use this to find where a note "
                "belongs before memory_write."
            ),
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=_list,
        )
    )

    def _search(arguments: dict[str, Any]) -> str:
        query = arguments["query"]
        try:
            results, backend = with_fallback(remote_factory, local, lambda b: b.search(query))
        except (VaultPathError, FileNotFoundError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"backend": backend, "results": results})

    registry.register(
        Tool(
            name="memory_search",
            description="Search the vault's Markdown notes for a query string.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            handler=_search,
        )
    )
