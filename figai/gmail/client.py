"""Read-only Gmail access over IMAP (app password).

Safety: the mailbox is opened read-only and messages are fetched with
BODY.PEEK, so nothing is ever marked read, moved, or deleted, and there is no
send capability at all. Email content is untrusted input -- it is returned to
the model as data and may contain text that tries to give instructions."""

from __future__ import annotations

import html
import imaplib
import re
from contextlib import contextmanager
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator

IMAP_HOST = "imap.gmail.com"
IMAP_TIMEOUT_SECONDS = 20
MAX_RAW_BYTES = 2_000_000

ImapFactory = Callable[[], Any]


class GmailError(Exception):
    """A connection, login, or protocol failure. Never contains the password."""


def default_imap_factory() -> Any:
    return imaplib.IMAP4_SSL(IMAP_HOST, timeout=IMAP_TIMEOUT_SECONDS)


def sanitize_query(query: str) -> str:
    """Gmail search text goes inside an IMAP quoted string, so strip the
    characters that could break out of it."""
    return re.sub(r'[\\"\r\n]', " ", query).strip()


def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?s)<[^>]+>", " ", markup)
    return html.unescape(markup)


def normalize(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def parse_message(raw: bytes) -> dict[str, str]:
    msg = message_from_bytes(raw, policy=policy.default)
    text = ""
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is not None:
        try:
            content = body.get_content()
            text = html_to_text(content) if body.get_content_type() == "text/html" else content
        except Exception:  # noqa: BLE001 - undecodable body: leave the text empty
            text = ""

    date = str(msg["Date"] or "")
    try:
        date = parsedate_to_datetime(date).isoformat()
    except (TypeError, ValueError):
        pass
    return {
        "from": str(msg["From"] or ""),
        "to": str(msg["To"] or ""),
        "subject": str(msg["Subject"] or "(no subject)"),
        "date": date,
        "text": normalize(text),
    }


@contextmanager
def open_mailbox(address: str, password: str, factory: ImapFactory = default_imap_factory) -> Iterator[Any]:
    imap = factory()
    try:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        yield imap
    except (imaplib.IMAP4.error, OSError) as exc:
        message = str(exc).replace(password, "***") if password else str(exc)
        raise GmailError(f"Gmail IMAP error: {message}") from exc
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def search_uids(imap: Any, query: str, limit: int) -> list[str]:
    """Newest-first UIDs matching a Gmail search (X-GM-RAW supports the same
    syntax as the Gmail search box: is:unread, from:x, newer_than:2d, ...)."""
    _, data = imap.uid("SEARCH", None, "X-GM-RAW", f'"{sanitize_query(query)}"')
    uids = [u.decode() for u in (data[0] or b"").split()]
    return list(reversed(uids))[:limit]


def fetch_message(imap: Any, uid: str) -> tuple[bool, dict[str, str]]:
    """Returns (is_unread, parsed message). BODY.PEEK never sets \\Seen."""
    _, data = imap.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
    for item in data:
        if isinstance(item, tuple):
            header, raw = item
            unread = "\\Seen" not in header.decode(errors="ignore")
            if len(raw) > MAX_RAW_BYTES:
                return unread, {"from": "", "to": "", "subject": "(message too large to read)", "date": "", "text": ""}
            return unread, parse_message(raw)
    raise GmailError(f"Message {uid} not found")
