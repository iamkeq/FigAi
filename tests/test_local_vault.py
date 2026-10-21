from __future__ import annotations

from pathlib import Path

import pytest

from figai.ssh.local_vault import LocalVaultBackend
from figai.ssh.vault_common import VaultPathError


def make_vault(tmp_path: Path) -> LocalVaultBackend:
    return LocalVaultBackend(tmp_path / "vault", writable_subfolder="AI")


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)

    vault.write("AI/index.md", "# Index\nhello")

    assert vault.read("AI/index.md") == "# Index\nhello"


def test_write_outside_writable_subfolder_rejected(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)

    with pytest.raises(VaultPathError):
        vault.write("notes/other.md", "content")


def test_read_outside_writable_subfolder_allowed(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    (vault.root / "notes").mkdir(parents=True)
    (vault.root / "notes" / "other.md").write_text("preexisting", encoding="utf-8")

    assert vault.read("notes/other.md") == "preexisting"


def test_read_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    with pytest.raises(FileNotFoundError):
        vault.read("AI/nope.md")


def test_path_traversal_escape_rejected_on_read(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    with pytest.raises(VaultPathError):
        vault.read("../../etc/passwd")


def test_path_traversal_escape_rejected_on_write(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    with pytest.raises(VaultPathError):
        vault.write("AI/../../escape.md", "pwned")


def test_search_finds_matching_note_by_content(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.write("AI/index.md", "This mentions the secret project codename.")

    results = vault.search("codename")

    assert len(results) == 1
    assert results[0]["path"] == "AI/index.md"
    assert "codename" in results[0]["snippet"]


def test_search_no_match_returns_empty_list(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    vault.write("AI/index.md", "nothing relevant here")

    assert vault.search("zzz-no-match") == []
