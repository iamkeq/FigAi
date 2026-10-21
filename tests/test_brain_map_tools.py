from __future__ import annotations

import json
from pathlib import Path

from figai.agent.tools import ToolRegistry
from figai.brain_map.tools import register_brain_map_tool
from figai.ssh.local_vault import LocalVaultBackend


def test_export_brain_map_uses_local_fallback_and_reports_counts(tmp_path: Path) -> None:
    local = LocalVaultBackend(tmp_path / "vault", writable_subfolder="AI")
    local.write("AI/a.md", "Links to [[b]].")
    local.write("AI/b.md", "Links back to [[a]].")

    def exploding_remote_factory():
        raise OSError("no network")

    sent: list[Path] = []
    registry = ToolRegistry()
    register_brain_map_tool(registry, exploding_remote_factory, local, file_sender=sent.append)

    result = json.loads(registry.execute("export_brain_map", "{}"))

    assert result == {"backend": "local", "notes": 2, "links": 1, "sent": True}
    assert len(sent) == 1
    assert not sent[0].exists()  # tool cleans up its temp file after sending


def test_export_brain_map_without_file_sender_reports_error_but_still_renders(tmp_path: Path) -> None:
    local = LocalVaultBackend(tmp_path / "vault", writable_subfolder="AI")
    local.write("AI/a.md", "no links")

    def exploding_remote_factory():
        raise OSError("no network")

    registry = ToolRegistry()
    register_brain_map_tool(registry, exploding_remote_factory, local, file_sender=None)

    result = json.loads(registry.execute("export_brain_map", "{}"))

    assert result["notes"] == 1
    assert "send_error" in result
