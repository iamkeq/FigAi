"""Builds the "Fig is back online" message, including which PR was applied if
this restart followed a self-update."""

from __future__ import annotations

from typing import Any


def build_startup_announcement(pending: dict[str, Any] | None) -> str:
    """`pending` is the persisted marker left by an update (None for an
    ordinary start). Its "pr" is {"number": int, "labels": [str]} or None
    when the commit couldn't be matched to a pull request."""
    if not pending:
        return "Fig is back online."

    pr = pending.get("pr")
    if not pr:
        return "Fig is back online after a code update (couldn't determine which PR)."

    labels = pr.get("labels") or []
    label_text = ", ".join(labels) if labels else "no label"
    return f"Fig is back online after a code update: PR #{pr['number']} (label: {label_text})."
