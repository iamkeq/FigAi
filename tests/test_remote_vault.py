from __future__ import annotations

import pytest

from figai.config import SshHost
from figai.ssh.remote_vault import RemoteVaultBackend
from figai.ssh.vault_common import VaultPathError


def make_backend() -> RemoteVaultBackend:
    host = SshHost(host="10.0.0.5", user="matt", port=22, key_path="/tmp/key")
    return RemoteVaultBackend(host, remote_root="/home/matt/Vault", writable_subfolder="AI")


def test_resolve_read_path_within_root() -> None:
    backend = make_backend()
    assert backend._resolve("notes/thing.md", for_write=False) == "/home/matt/Vault/notes/thing.md"


def test_resolve_rejects_path_escaping_root() -> None:
    backend = make_backend()
    with pytest.raises(VaultPathError):
        backend._resolve("../../etc/passwd", for_write=False)


def test_resolve_write_within_writable_subfolder_allowed() -> None:
    backend = make_backend()
    assert backend._resolve("AI/index.md", for_write=True) == "/home/matt/Vault/AI/index.md"


def test_resolve_write_outside_writable_subfolder_rejected() -> None:
    backend = make_backend()
    with pytest.raises(VaultPathError):
        backend._resolve("notes/thing.md", for_write=True)


def test_resolve_write_path_traversal_out_of_writable_subfolder_rejected() -> None:
    backend = make_backend()
    with pytest.raises(VaultPathError):
        backend._resolve("AI/../other.md", for_write=True)


class FakeSftp:
    def __init__(self, existing: set[str]) -> None:
        self.existing = set(existing)
        self.made: list[str] = []

    def stat(self, path: str):
        if path not in self.existing:
            raise FileNotFoundError(path)

    def mkdir(self, path: str) -> None:
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.existing:
            raise OSError("No such file")
        self.made.append(path)
        self.existing.add(path)


def test_missing_vault_root_is_created_one_level() -> None:
    backend = make_backend()
    sftp = FakeSftp({"/home/matt"})
    backend._ensure_root(sftp)  # type: ignore[arg-type]
    assert sftp.made == ["/home/matt/Vault"]


def test_missing_vault_root_with_missing_parent_fails_clearly() -> None:
    backend = make_backend()
    with pytest.raises(VaultPathError, match="remote_path"):
        backend._ensure_root(FakeSftp(set()))  # type: ignore[arg-type]


@pytest.mark.parametrize("subfolder", ["", "/", ".", "*"])
def test_whole_vault_writable_on_remote(subfolder: str) -> None:
    host = SshHost(host="10.0.0.5", user="matt", port=22, key_path="/tmp/key")
    backend = RemoteVaultBackend(host, remote_root="/media/pool/vault/Obsidian Vault/Dave's PC", writable_subfolder=subfolder)
    expected = "/media/pool/vault/Obsidian Vault/Dave's PC/CryptoNote.md"
    assert backend._resolve("CryptoNote.md", for_write=True) == expected
    assert backend._resolve("/CryptoNote.md", for_write=True) == expected
    assert backend._resolve("new/dir/a.md", for_write=True).endswith("/Dave's PC/new/dir/a.md")
    with pytest.raises(VaultPathError):
        backend._resolve("../x.md", for_write=True)
    with pytest.raises(VaultPathError):
        backend._resolve("/", for_write=True)


class LocalShellClient:
    """Runs the 'remote' commands in a local shell, so the real grep/find flags are exercised."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def exec_command(self, command: str, timeout: int = 0):
        import io
        import subprocess
        from types import SimpleNamespace

        self.commands.append(command)
        done = subprocess.run(["sh", "-c", command], capture_output=True)  # noqa: S603,S607
        channel = SimpleNamespace(recv_exit_status=lambda: done.returncode)
        return (
            None,
            SimpleNamespace(channel=channel, read=lambda: done.stdout),
            SimpleNamespace(read=lambda: done.stderr),
        )

    def close(self) -> None: ...


def test_search_runs_grep_on_the_server_and_returns_relative_paths(tmp_path, monkeypatch) -> None:
    vault = tmp_path / "Dave's PC"
    (vault / "Crypto").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / "Crypto" / "FigAxe2.md").write_text("FigAxe2 is a crypto miner\nweb UI http://192.168.50.57/\n")
    (vault / "notes.md").write_text("nothing relevant\n")
    (vault / "miner-setup.md").write_text("how to set up\n")
    (vault / ".obsidian" / "hidden.md").write_text("crypto miner in a hidden folder\n")
    (vault / "image.png").write_text("crypto miner")

    client = LocalShellClient()
    monkeypatch.setattr("figai.ssh.remote_vault.connect", lambda _h: client)
    host = SshHost(host="10.0.0.5", user="u", port=22, key_path="/k")
    backend = RemoteVaultBackend(host, remote_root=str(vault), writable_subfolder="")

    by_path = {r["path"]: r["snippet"] for r in backend.search("crypto MINER")}
    assert by_path == {"Crypto/FigAxe2.md": "FigAxe2 is a crypto miner"}

    by_path = {r["path"]: r["snippet"] for r in backend.search("miner")}
    assert set(by_path) == {"Crypto/FigAxe2.md", "miner-setup.md"}
    assert by_path["miner-setup.md"] == "(file name matches)"
    assert len(client.commands) == 4  # grep + find per search, no per-file downloads


def test_search_quotes_hostile_queries(tmp_path, monkeypatch) -> None:
    (tmp_path / "a.md").write_text("plain\n")
    monkeypatch.setattr("figai.ssh.remote_vault.connect", lambda _h: LocalShellClient())
    host = SshHost(host="h", user="u", port=22, key_path="/k")
    backend = RemoteVaultBackend(host, remote_root=str(tmp_path), writable_subfolder="")
    assert backend.search("x'; touch /tmp/pwned_by_fig; echo '") == []
    import os

    assert not os.path.exists("/tmp/pwned_by_fig")


def test_search_falls_back_to_downloading_when_grep_unavailable(monkeypatch) -> None:
    from types import SimpleNamespace

    class NoGrep(LocalShellClient):
        def exec_command(self, command: str, timeout: int = 0):
            channel = SimpleNamespace(recv_exit_status=lambda: 127)
            return None, SimpleNamespace(channel=channel, read=lambda: b""), SimpleNamespace(read=lambda: b"sh: grep: not found")

    monkeypatch.setattr("figai.ssh.remote_vault.connect", lambda _h: NoGrep())
    host = SshHost(host="h", user="u", port=22, key_path="/k")
    backend = RemoteVaultBackend(host, remote_root="/v", writable_subfolder="")
    monkeypatch.setattr(backend, "_search_by_download", lambda q: [{"path": "slow.md", "snippet": q}])
    assert backend.search("q") == [{"path": "slow.md", "snippet": "q"}]


def test_missing_xargs_or_grep_is_detected_not_reported_as_no_results(monkeypatch) -> None:
    from types import SimpleNamespace

    class NoXargs(LocalShellClient):
        def exec_command(self, command: str, timeout: int = 0):
            import subprocess

            command = command.replace("command -v xargs", "command -v definitely_not_installed_cmd")
            done = subprocess.run(["sh", "-c", command], capture_output=True)  # noqa: S603,S607
            channel = SimpleNamespace(recv_exit_status=lambda: done.returncode)
            return None, SimpleNamespace(channel=channel, read=lambda: done.stdout), SimpleNamespace(read=lambda: done.stderr)

    monkeypatch.setattr("figai.ssh.remote_vault.connect", lambda _h: NoXargs())
    host = SshHost(host="h", user="u", port=22, key_path="/k")
    backend = RemoteVaultBackend(host, remote_root="/tmp", writable_subfolder="")
    monkeypatch.setattr(backend, "_search_by_download", lambda q: [{"path": "slow.md", "snippet": q}])
    assert backend.search("q") == [{"path": "slow.md", "snippet": "q"}]
