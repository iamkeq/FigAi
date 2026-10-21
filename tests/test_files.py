from __future__ import annotations

import json
from pathlib import Path

from figai.agent.tools import ToolRegistry
from figai.config import AppConfig
from figai.tools.confirm import ConfirmationStore
from figai.tools.files import register_search_files_tool, register_send_file_tools


class FakeConfig:
    def __init__(self, app: AppConfig) -> None:
        self.app = app


def make_config(tmp_path: Path, folders: list[str], max_send_file_mb: int = 25) -> FakeConfig:
    app = AppConfig.model_validate(
        {
            "models": {"default_chat": "x", "vision": "y"},
            "vault": {"ssh_host_alias": "v", "remote_path": "/v", "local_path": str(tmp_path / "vault")},
            "allowlisted_folders": folders,
            "limits": {"max_send_file_mb": max_send_file_mb},
        }
    )
    return FakeConfig(app)


def test_search_files_finds_by_name_and_extension(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "report.pdf").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"y")

    registry = ToolRegistry()
    register_search_files_tool(registry, make_config(tmp_path, [str(folder)]))

    result = json.loads(registry.execute("search_files", json.dumps({"extension": "pdf"})))

    assert [r["path"] for r in result["results"]] == [str(folder / "report.pdf")]


def test_search_files_ignores_non_allowlisted_folders(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"x")

    registry = ToolRegistry()
    register_search_files_tool(registry, make_config(tmp_path, [str(tmp_path / "docs")]))

    result = json.loads(registry.execute("search_files", json.dumps({"query": "secret"})))

    assert result["results"] == []


def test_propose_send_file_rejects_file_outside_allowlist(tmp_path: Path) -> None:
    outside_file = tmp_path / "outside.txt"
    outside_file.write_bytes(b"data")

    registry = ToolRegistry()
    config = make_config(tmp_path, [str(tmp_path / "docs")])
    register_send_file_tools(registry, config, ConfirmationStore(), file_sender=None)

    result = json.loads(registry.execute("propose_send_file", json.dumps({"path": str(outside_file)})))

    assert "error" in result


def test_propose_send_file_rejects_oversized_file(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    big_file = folder / "big.bin"
    big_file.write_bytes(b"0" * 1024)  # 1 KB

    registry = ToolRegistry()
    config = make_config(tmp_path, [str(folder)], max_send_file_mb=0)  # 0 MB limit -> always too big
    register_send_file_tools(registry, config, ConfirmationStore(), file_sender=None)

    result = json.loads(registry.execute("propose_send_file", json.dumps({"path": str(big_file)})))

    assert "error" in result


def test_send_file_round_trip_requires_confirmation_then_delivers(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    target = folder / "report.pdf"
    target.write_bytes(b"content")

    sent: list[Path] = []
    registry = ToolRegistry()
    config = make_config(tmp_path, [str(folder)])
    register_send_file_tools(registry, config, ConfirmationStore(), file_sender=sent.append)

    propose_result = json.loads(registry.execute("propose_send_file", json.dumps({"path": str(target)})))
    assert propose_result["status"] == "awaiting_confirmation"
    assert sent == []  # nothing sent yet -- still waiting on the user's confirmation

    resolve_result = json.loads(
        registry.execute("resolve_send_file", json.dumps({"token": propose_result["token"]}))
    )

    assert resolve_result == {"status": "sent", "path": str(target)}
    assert sent == [target]


def test_resolve_send_file_without_sender_configured_returns_error(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    target = folder / "report.pdf"
    target.write_bytes(b"content")

    registry = ToolRegistry()
    config = make_config(tmp_path, [str(folder)])
    register_send_file_tools(registry, config, ConfirmationStore(), file_sender=None)

    propose_result = json.loads(registry.execute("propose_send_file", json.dumps({"path": str(target)})))
    resolve_result = json.loads(
        registry.execute("resolve_send_file", json.dumps({"token": propose_result["token"]}))
    )

    assert "error" in resolve_result
