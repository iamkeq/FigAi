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


def decide_pending_announcement(
    pending: dict[str, Any] | None,
    head_sha: str | None,
    last_announced_sha: str | None,
    lookup_pr: Any,
) -> dict[str, Any] | None:
    """Decides whether this startup follows a code change, however the new code
    arrived (self-update, update script, or a manual git pull).

    An explicit marker left by the self-update tool wins. Otherwise, if the
    current commit differs from the last one announced, look up its PR. With no
    previous commit on record (first run of this feature) there is nothing to
    compare against, so it is treated as an ordinary start."""
    if pending:
        return pending
    if head_sha and last_announced_sha and head_sha != last_announced_sha:
        return {"sha": head_sha, "pr": lookup_pr(head_sha)}
    return None
