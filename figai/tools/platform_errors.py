"""Shared error for tools that only have a real implementation on Windows
(Fig's deployment target) and report a clear, honest failure elsewhere."""

from __future__ import annotations


class UnsupportedPlatformError(Exception):
    pass
