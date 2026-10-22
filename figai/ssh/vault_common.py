"""Shared types/helpers for the remote and local vault backends."""

from __future__ import annotations


class VaultPathError(Exception):
    """A vault-relative path escapes the vault root, or a write targets outside
    the configured writable subfolder. Never falls back to another backend --
    it's a rejection, not a connectivity failure."""


def snippet(text: str, query_lower: str, context: int = 80) -> str:
    idx = text.lower().find(query_lower)
    if idx == -1:
        return text[:context].strip()
    start = max(0, idx - context // 2)
    end = min(len(text), idx + len(query_lower) + context // 2)
    return text[start:end].strip()


def normalize_subfolder(value: str) -> str:
    """'' , '/', '.' and '*' all mean "the whole vault is writable"."""
    cleaned = value.strip().replace("\\", "/").strip("/")
    return "" if cleaned in (".", "*") else cleaned


def clean_relative(path: str) -> str:
    """Vault paths are always relative to the vault root; tolerate a leading '/'."""
    return path.strip().replace("\\", "/").lstrip("/")
