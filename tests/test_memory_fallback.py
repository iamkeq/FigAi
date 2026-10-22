from __future__ import annotations

import json
from pathlib import Path

import pytest

from figai.agent.tools import ToolRegistry
from figai.config import AppConfig
from figai.ssh.local_vault import LocalVaultBackend
from figai.ssh.memory import load_index_note, register_memory_tools, with_fallback
from figai.ssh.vault_common import VaultPathError
from figai.tools.confirm import ConfirmationStore


class ExplodingRemote:
    """Stands in for a RemoteVaultBackend that can't reach the network."""

    def read(self, _path: str) -> str:
        raise OSError("Connection refused")

    def write(self, _path: str, _content: str) -> None:
        raise OSError("Connection refused")

    def search(self, _query: str) -> list[dict[str, str]]:
        raise OSError("Connection refused")

    def list_dir(self, _path: str = "") -> list[str]:
        raise OSError("Connection refused")


class WorkingRemote:
    def __init__(self) -> None:
        self.written: dict[str, str] = {}

    def read(self, path: str) -> str:
        return f"remote content for {path}"

    def write(self, path: str, content: str) -> None:
        self.written[path] = content

    def search(self, query: str) -> list[dict[str, str]]:
        return [{"path": "AI/index.md", "snippet": query}]


def local_vault(tmp_path: Path) -> LocalVaultBackend:
    return LocalVaultBackend(tmp_path / "vault", writable_subfolder="AI")


def test_with_fallback_uses_remote_when_available(tmp_path: Path) -> None:
    result, backend = with_fallback(lambda: WorkingRemote(), local_vault(tmp_path), lambda b: b.read("AI/index.md"))
    assert backend == "remote"
    assert result == "remote content for AI/index.md"


def test_with_fallback_falls_back_to_local_on_connection_error(tmp_path: Path) -> None:
    local = local_vault(tmp_path)
    local.write("AI/index.md", "local content")

    result, backend = with_fallback(lambda: ExplodingRemote(), local, lambda b: b.read("AI/index.md"))

    assert backend == "local"
    assert result == "local content"


def test_with_fallback_propagates_vault_path_error_without_falling_back(tmp_path: Path) -> None:
    class RejectingRemote:
        def write(self, _path: str, _content: str) -> None:
            raise VaultPathError("writes restricted to AI/")

    with pytest.raises(VaultPathError):
        with_fallback(lambda: RejectingRemote(), local_vault(tmp_path), lambda b: b.write("notes/x.md", "y"))


def test_with_fallback_missing_ssh_config_falls_back_to_local(tmp_path: Path) -> None:
    def factory():
        raise OSError("SSH host alias 'vault' is not configured in .env")

    local = local_vault(tmp_path)
    local.write("AI/index.md", "local only")

    result, backend = with_fallback(factory, local, lambda b: b.read("AI/index.md"))

    assert backend == "local"
    assert result == "local only"


def test_load_index_note_returns_none_when_missing_everywhere(tmp_path: Path) -> None:
    content, backend = load_index_note(lambda: ExplodingRemote(), local_vault(tmp_path), "AI/index.md")
    assert content is None
    assert backend is None


def test_load_index_note_finds_local_fallback(tmp_path: Path) -> None:
    local = local_vault(tmp_path)
    local.write("AI/index.md", "my memory")

    content, backend = load_index_note(lambda: ExplodingRemote(), local, "AI/index.md")

    assert content == "my memory"
    assert backend == "local"


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "models": {"default_chat": "x", "vision": "y"},
            "vault": {
                "ssh_host_alias": "vault",
                "remote_path": "/home/matt/Vault",
                "local_path": str(tmp_path / "vault"),
            },
        }
    )


def test_memory_tools_round_trip_with_fallback(tmp_path: Path) -> None:
    registry = ToolRegistry()
    local = local_vault(tmp_path)

    class FakeConfig:
        app = _config(tmp_path)

    register_memory_tools(registry, FakeConfig(), lambda: ExplodingRemote(), local, ConfirmationStore())

    write_result = json.loads(
        registry.execute("memory_write", json.dumps({"path": "AI/index.md", "content": "hello"}))
    )
    assert write_result == {"status": "written", "backend": "local"}

    read_result = json.loads(registry.execute("memory_read", json.dumps({"path": "AI/index.md"})))
    assert read_result == {"backend": "local", "content": "hello"}

    search_result = json.loads(registry.execute("memory_search", json.dumps({"query": "hello"})))
    assert search_result["backend"] == "local"
    assert search_result["results"][0]["path"] == "AI/index.md"


def test_memory_write_outside_ai_returns_error_not_exception(tmp_path: Path) -> None:
    registry = ToolRegistry()
    local = local_vault(tmp_path)

    class FakeConfig:
        app = _config(tmp_path)

    register_memory_tools(registry, FakeConfig(), lambda: ExplodingRemote(), local, ConfirmationStore())

    result = json.loads(
        registry.execute("memory_write", json.dumps({"path": "notes/x.md", "content": "nope"}))
    )
    assert "error" in result


def test_memory_write_to_new_note_does_not_require_confirmation(tmp_path: Path) -> None:
    registry = ToolRegistry()
    local = local_vault(tmp_path)

    class FakeConfig:
        app = _config(tmp_path)

    register_memory_tools(registry, FakeConfig(), lambda: ExplodingRemote(), local, ConfirmationStore())

    result = json.loads(
        registry.execute("memory_write", json.dumps({"path": "AI/brand_new.md", "content": "first draft"}))
    )

    assert result == {"status": "written", "backend": "local"}


def test_memory_write_to_existing_note_requires_confirmation(tmp_path: Path) -> None:
    registry = ToolRegistry()
    local = local_vault(tmp_path)
    local.write("AI/index.md", "original content")

    class FakeConfig:
        app = _config(tmp_path)

    register_memory_tools(registry, FakeConfig(), lambda: ExplodingRemote(), local, ConfirmationStore())

    propose_result = json.loads(
        registry.execute("memory_write", json.dumps({"path": "AI/index.md", "content": "new content"}))
    )

    assert propose_result["status"] == "awaiting_confirmation"
    assert local.read("AI/index.md") == "original content"  # untouched until confirmed

    resolve_result = json.loads(
        registry.execute("resolve_memory_write", json.dumps({"token": propose_result["token"]}))
    )

    assert resolve_result == {"status": "written", "backend": "local"}
    assert local.read("AI/index.md") == "new content"


def test_resolve_memory_write_without_prior_proposal_returns_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    local = local_vault(tmp_path)

    class FakeConfig:
        app = _config(tmp_path)

    register_memory_tools(registry, FakeConfig(), lambda: ExplodingRemote(), local, ConfirmationStore())

    result = json.loads(registry.execute("resolve_memory_write", json.dumps({"token": "madeup"})))

    assert "error" in result


class MissingNoteRemote(WorkingRemote):
    def read(self, path: str) -> str:
        raise FileNotFoundError(path)

    def list_dir(self, path: str = "") -> list[str]:
        return ["homelab/", "notes.md"]


def test_missing_remote_note_is_not_treated_as_unreachable(tmp_path: Path) -> None:
    local = local_vault(tmp_path)
    local.write("AI/index.md", "local content")
    with pytest.raises(FileNotFoundError):
        with_fallback(lambda: MissingNoteRemote(), local, lambda b: b.read("AI/index.md"))


@pytest.mark.parametrize("subfolder", ["", "/", ".", "*"])
def test_whole_vault_writable_and_leading_slash_tolerated(tmp_path: Path, subfolder: str) -> None:
    local = LocalVaultBackend(tmp_path / "vault", writable_subfolder=subfolder)
    local.write("/homelab/FigAxe2.md", "hi")
    assert (tmp_path / "vault" / "homelab" / "FigAxe2.md").read_text() == "hi"
    with pytest.raises(VaultPathError):
        local.write("/", "x")
    with pytest.raises(VaultPathError):
        local.write("../escape.md", "x")


def test_memory_tools_write_into_new_folder_list_and_report_bad_paths(tmp_path: Path) -> None:
    local = LocalVaultBackend(tmp_path / "vault", writable_subfolder="")
    config = AppConfig(
        models={"default_chat": "a/b", "vision": "c/d"},
        vault={"ssh_host_alias": "v", "remote_path": "/r", "local_path": str(tmp_path / "vault")},
    )
    config.vault.remote_writable_subfolder = ""

    class Cfg:
        app = config

    registry = ToolRegistry()
    register_memory_tools(registry, Cfg(), lambda: ExplodingRemote(), local, ConfirmationStore())  # type: ignore[arg-type]

    # remote is down, so everything lands in the local vault (which does the path checks)
    written = json.loads(registry.execute("memory_write", json.dumps({"path": "/a.md", "content": "x"})))
    assert written == {"status": "written", "backend": "local"}
    assert "error" in json.loads(registry.execute("memory_write", json.dumps({"path": "/", "content": "x"})))
    listing = json.loads(registry.execute("memory_list", json.dumps({})))
    assert listing["entries"] == ["a.md"]
