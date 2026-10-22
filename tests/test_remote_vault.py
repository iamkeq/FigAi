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
