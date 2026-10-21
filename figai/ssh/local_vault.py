"""A fully independent local Markdown vault, used as a fallback when the remote
Obsidian vault is unreachable over SSH. Not synced or merged with the remote vault."""

from __future__ import annotations

from pathlib import Path

from figai.ssh.vault_common import VaultPathError, snippet


class LocalVaultBackend:
    def __init__(self, root: Path, writable_subfolder: str) -> None:
        self.root = root
        self.writable_subfolder = writable_subfolder
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str, *, for_write: bool) -> Path:
        root_resolved = self.root.resolve()
        candidate = (self.root / relative_path).resolve()
        if candidate != root_resolved and root_resolved not in candidate.parents:
            raise VaultPathError(f"Path escapes the vault: {relative_path}")

        if for_write:
            writable_root = (self.root / self.writable_subfolder).resolve()
            if candidate != writable_root and writable_root not in candidate.parents:
                raise VaultPathError(
                    f"Writes are restricted to '{self.writable_subfolder}/'; got '{relative_path}'"
                )
        return candidate

    def read(self, relative_path: str) -> str:
        path = self._resolve(relative_path, for_write=False)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8")

    def write(self, relative_path: str, content: str) -> None:
        path = self._resolve(relative_path, for_write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def list_all_markdown(self) -> dict[str, str]:
        return {
            str(md_file.relative_to(self.root)): md_file.read_text(encoding="utf-8", errors="replace")
            for md_file in sorted(self.root.rglob("*.md"))
        }

    def search(self, query: str) -> list[dict[str, str]]:
        query_lower = query.lower()
        results: list[dict[str, str]] = []
        for md_file in sorted(self.root.rglob("*.md")):
            text = md_file.read_text(encoding="utf-8", errors="replace")
            if query_lower in text.lower() or query_lower in md_file.stem.lower():
                results.append(
                    {"path": str(md_file.relative_to(self.root)), "snippet": snippet(text, query_lower)}
                )
        return results
