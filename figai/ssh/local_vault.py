"""A fully independent local Markdown vault, used as a fallback when the remote
Obsidian vault is unreachable over SSH. Not synced or merged with the remote vault."""

from __future__ import annotations

from pathlib import Path

from figai.ssh.vault_common import VaultPathError, clean_relative, normalize_subfolder, snippet, trash_path


class LocalVaultBackend:
    def __init__(self, root: Path, writable_subfolder: str) -> None:
        self.root = root
        self.writable_subfolder = normalize_subfolder(writable_subfolder)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str, *, for_write: bool) -> Path:
        relative_path = clean_relative(relative_path)
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
            if candidate == root_resolved or candidate.is_dir():
                raise VaultPathError(f"'{relative_path}' is a folder; give a note path like 'folder/note.md'")
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

    def _move_resolved(self, source: Path, target: Path, shown_source: str, shown_target: str) -> None:
        if source == self.root.resolve():
            raise VaultPathError("The vault root can't be moved or deleted.")
        if not source.exists():
            raise FileNotFoundError(shown_source)
        if target.exists():
            raise VaultPathError(f"'{shown_target}' already exists; I won't overwrite it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    def move(self, source_path: str, target_path: str) -> None:
        self._move_resolved(
            self._resolve(source_path, for_write=True),
            self._resolve(target_path, for_write=True),
            source_path,
            target_path,
        )

    def trash(self, relative_path: str) -> str:
        """Recoverable delete: moves into .fig-trash/<timestamp>/. Returns the new location."""
        destination = trash_path(relative_path)
        self._move_resolved(
            self._resolve(relative_path, for_write=True), (self.root / destination).resolve(), relative_path, destination
        )
        return destination

    def list_dir(self, relative_path: str = "") -> list[str]:
        path = self._resolve(relative_path, for_write=False)
        if not path.is_dir():
            raise FileNotFoundError(relative_path)
        return sorted(
            (f"{entry.name}/" if entry.is_dir() else entry.name)
            for entry in path.iterdir()
            if not entry.name.startswith(".")
        )

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
        return results[:50]
