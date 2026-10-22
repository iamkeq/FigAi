"""Reads/writes the owner's real Obsidian vault over SFTP. Writes are restricted
to the configured writable subfolder; reads may cover the whole vault."""

from __future__ import annotations

import posixpath
import stat as stat_module

import paramiko

from figai.config import SshHost
from figai.ssh.client import connect
from figai.ssh.vault_common import VaultPathError, clean_relative, normalize_subfolder, snippet, trash_path


class RemoteVaultBackend:
    def __init__(self, host: SshHost, remote_root: str, writable_subfolder: str) -> None:
        self.host = host
        self.remote_root = remote_root.rstrip("/") or "/"
        self.writable_subfolder = normalize_subfolder(writable_subfolder)

    def _resolve(self, relative_path: str, *, for_write: bool) -> str:
        relative_path = clean_relative(relative_path)
        normalized = posixpath.normpath(posixpath.join(self.remote_root, relative_path))
        if normalized != self.remote_root and not normalized.startswith(self.remote_root + "/"):
            raise VaultPathError(f"Path escapes the vault: {relative_path}")

        if for_write:
            writable_root = posixpath.join(self.remote_root, self.writable_subfolder)
            if normalized != writable_root and not normalized.startswith(writable_root + "/"):
                raise VaultPathError(
                    f"Writes are restricted to '{self.writable_subfolder}/'; got '{relative_path}'"
                )
            if normalized == self.remote_root:
                raise VaultPathError(f"'{relative_path}' is a folder; give a note path like 'folder/note.md'")
        return normalized

    def _sftp(self) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
        client = connect(self.host)
        return client, client.open_sftp()

    def read(self, relative_path: str) -> str:
        path = self._resolve(relative_path, for_write=False)
        client, sftp = self._sftp()
        try:
            try:
                with sftp.open(path, "r") as handle:
                    return handle.read().decode("utf-8")
            except FileNotFoundError:
                raise FileNotFoundError(relative_path) from None
        finally:
            sftp.close()
            client.close()

    def write(self, relative_path: str, content: str) -> None:
        path = self._resolve(relative_path, for_write=True)
        client, sftp = self._sftp()
        try:
            self._mkdirs(sftp, posixpath.dirname(path))
            with sftp.open(path, "w") as handle:
                handle.write(content.encode("utf-8"))
        finally:
            sftp.close()
            client.close()

    def _mkdirs(self, sftp: paramiko.SFTPClient, path: str) -> None:
        if not path or path == self.remote_root or path == "/":
            return
        try:
            sftp.stat(path)
            return
        except FileNotFoundError:
            pass
        self._mkdirs(sftp, posixpath.dirname(path))
        try:
            sftp.mkdir(path)
        except OSError:
            pass  # created concurrently, or already exists

    def _move_resolved(self, source: str, target: str, shown_source: str, shown_target: str) -> None:
        if source == self.remote_root:
            raise VaultPathError("The vault root can't be moved or deleted.")
        client, sftp = self._sftp()
        try:
            try:
                sftp.stat(source)
            except FileNotFoundError:
                raise FileNotFoundError(shown_source) from None
            try:
                sftp.stat(target)
            except FileNotFoundError:
                pass
            else:
                raise VaultPathError(f"'{shown_target}' already exists; I won't overwrite it.")
            self._mkdirs(sftp, posixpath.dirname(target))
            sftp.posix_rename(source, target)
        finally:
            sftp.close()
            client.close()

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
            self._resolve(relative_path, for_write=True),
            self._resolve(destination, for_write=False),
            relative_path,
            destination,
        )
        return destination

    def list_dir(self, relative_path: str = "") -> list[str]:
        path = self._resolve(relative_path, for_write=False)
        client, sftp = self._sftp()
        try:
            try:
                entries = sftp.listdir_attr(path)
            except FileNotFoundError:
                raise FileNotFoundError(relative_path) from None
            return sorted(
                f"{e.filename}/" if stat_module.S_ISDIR(e.st_mode or 0) else e.filename
                for e in entries
                if not e.filename.startswith(".")
            )
        finally:
            sftp.close()
            client.close()

    def list_all_markdown(self) -> dict[str, str]:
        client, sftp = self._sftp()
        try:
            result: dict[str, str] = {}
            for remote_path in self._walk_markdown(sftp, self.remote_root):
                with sftp.open(remote_path, "r") as handle:
                    result[posixpath.relpath(remote_path, self.remote_root)] = handle.read().decode(
                        "utf-8", errors="replace"
                    )
            return result
        finally:
            sftp.close()
            client.close()

    def search(self, query: str) -> list[dict[str, str]]:
        client, sftp = self._sftp()
        try:
            query_lower = query.lower()
            results: list[dict[str, str]] = []
            for remote_path in self._walk_markdown(sftp, self.remote_root):
                with sftp.open(remote_path, "r") as handle:
                    text = handle.read().decode("utf-8", errors="replace")
                if query_lower in text.lower() or query_lower in posixpath.basename(remote_path).lower():
                    results.append(
                        {
                            "path": posixpath.relpath(remote_path, self.remote_root),
                            "snippet": snippet(text, query_lower),
                        }
                    )
            return results
        finally:
            sftp.close()
            client.close()

    def _walk_markdown(self, sftp: paramiko.SFTPClient, path: str) -> list[str]:
        found: list[str] = []
        for entry in sftp.listdir_attr(path):
            if entry.filename.startswith("."):
                continue
            full = posixpath.join(path, entry.filename)
            if stat_module.S_ISDIR(entry.st_mode or 0):
                found.extend(self._walk_markdown(sftp, full))
            elif entry.filename.endswith(".md"):
                found.append(full)
        return found
