"""export_brain_map: renders a deterministic PNG of the vault's notes and their
[[wikilinks]] and sends it through whatever file-delivery channel is available."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from figai.agent.tools import Tool, ToolRegistry
from figai.brain_map.graph import build_graph, render_graph_png
from figai.logging_setup import get_logger
from figai.ssh.memory import RemoteFactory, with_fallback
from figai.ssh.local_vault import LocalVaultBackend
from figai.tools.files import FileSender

logger = get_logger("brain_map")


def register_brain_map_tool(
    registry: ToolRegistry,
    remote_factory: RemoteFactory,
    local_vault: LocalVaultBackend,
    file_sender: FileSender | None,
) -> None:
    def _export(_arguments: dict[str, Any]) -> str:
        all_markdown, backend = with_fallback(remote_factory, local_vault, lambda b: b.list_all_markdown())
        notes_by_name = {Path(rel_path).stem: content for rel_path, content in all_markdown.items()}
        graph = build_graph(notes_by_name)
        image = render_graph_png(graph)

        out_path = Path(tempfile.gettempdir()) / f"figai_brain_map_{int(time.time() * 1000)}.png"
        image.convert("RGB").save(out_path, format="PNG")

        try:
            result: dict[str, Any] = {"backend": backend, "notes": len(graph.nodes), "links": len(graph.edges)}
            if file_sender is None:
                result["send_error"] = "No file-delivery channel is connected right now."
            else:
                try:
                    file_sender(out_path)
                    result["sent"] = True
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed to send brain map image")
                    result["send_error"] = str(exc)
            return json.dumps(result)
        finally:
            out_path.unlink(missing_ok=True)

    registry.register(
        Tool(
            name="export_brain_map",
            description=(
                "Render and send a PNG map of the vault's notes and their [[wikilinks]]. "
                "Deterministic from real notes -- never invents relationships."
            ),
            parameters={"type": "object", "properties": {}},
            handler=_export,
        )
    )
