"""Turns the raw Sonarr / Radarr / SABnzbd JSON into short, readable text ("Dune (2021) -- 45%, 12 min left").
The raw payloads are huge (a library dump can be megabytes), so the model and the user only ever see
this compact form."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MAX_ITEMS = 10


def _gb(megabytes: float) -> str:
    return f"{megabytes / 1024:.1f} GB" if megabytes >= 1024 else f"{megabytes:.0f} MB"


def _bytes(value: float) -> str:
    return _gb(value / (1024 * 1024))


def _arr_title(record: dict[str, Any]) -> str:
    series, episode, movie = record.get("series"), record.get("episode"), record.get("movie")
    if series and episode:
        return f"{series.get('title', '?')} S{episode.get('seasonNumber', 0):02d}E{episode.get('episodeNumber', 0):02d}"
    if series:
        return str(series.get("title", record.get("title", "?")))
    if movie:
        year = movie.get("year")
        return f"{movie.get('title', '?')}" + (f" ({year})" if year else "")
    return str(record.get("sourceTitle") or record.get("title") or "?")


def _ago(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = ((now or datetime.now(timezone.utc)) - when).total_seconds()
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def sab_queue(data: dict[str, Any]) -> str:
    queue = data.get("queue", data)
    slots = queue.get("slots") or []
    paused = " (PAUSED)" if queue.get("paused") else ""
    if not slots:
        return f"SABnzbd{paused}: nothing downloading."
    speed = queue.get("speed") or queue.get("kbpersec") or "?"
    lines = [f"SABnzbd{paused}: {len(slots)} in the queue, speed {speed}, {queue.get('timeleft', '?')} left overall"]
    for slot in slots[:MAX_ITEMS]:
        lines.append(f"  - {slot.get('filename', '?')} -- {slot.get('percentage', '?')}%, {slot.get('timeleft', '?')} left [{slot.get('status', '')}]")
    if len(slots) > MAX_ITEMS:
        lines.append(f"  ...and {len(slots) - MAX_ITEMS} more")
    return "\n".join(lines)


def sab_history(data: dict[str, Any], now: datetime | None = None) -> str:
    slots = (data.get("history", data)).get("slots") or []
    if not slots:
        return "SABnzbd: nothing in the recent history."
    lines = ["SABnzbd recent:"]
    for slot in slots[:MAX_ITEMS]:
        done = slot.get("completed")
        when = _ago(datetime.fromtimestamp(done, timezone.utc).isoformat(), now) if isinstance(done, (int, float)) and done else ""
        lines.append(f"  - {slot.get('name', '?')} -- {slot.get('status', '?')}, {slot.get('size', '?')}" + (f", {when}" if when else ""))
    return "\n".join(lines)


def arr_queue(service: str, data: Any) -> str:
    records = data.get("records", []) if isinstance(data, dict) else data
    label = service.capitalize()
    if not records:
        return f"{label}: nothing in the download queue."
    lines = [f"{label}: {len(records)} downloading/queued"]
    for record in records[:MAX_ITEMS]:
        size, left = record.get("size") or 0, record.get("sizeleft") or 0
        pct = f"{100 * (size - left) / size:.0f}%" if size else "?"
        eta = record.get("timeleft") or record.get("status", "")
        lines.append(f"  - {_arr_title(record)} -- {pct}, {eta}")
    if len(records) > MAX_ITEMS:
        lines.append(f"  ...and {len(records) - MAX_ITEMS} more")
    return "\n".join(lines)


GRABBED, IMPORTED = "grabbed", "downloadFolderImported"


def arr_history(service: str, data: Any, now: datetime | None = None) -> str:
    records = data.get("records", []) if isinstance(data, dict) else data
    label = service.capitalize()
    wanted = [r for r in records if r.get("eventType") in (IMPORTED, GRABBED)]
    if not wanted:
        return f"{label}: no recent downloads."
    lines = [f"{label} recent:"]
    for record in wanted[:MAX_ITEMS]:
        verb = "downloaded" if record.get("eventType") == IMPORTED else "grabbed (downloading)"
        lines.append(f"  - {_arr_title(record)} -- {verb} {_ago(record.get('date'), now)}".rstrip())
    return "\n".join(lines)


def library_summary(service: str, data: Any) -> dict[str, Any]:
    items = data if isinstance(data, list) else []
    titles = sorted(str(i.get("title", "?")) for i in items)
    return {"service": service, "count": len(items), "titles": titles[:60], "truncated": max(0, len(titles) - 60)}
