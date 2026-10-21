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
