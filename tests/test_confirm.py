from __future__ import annotations

import time

import pytest

from figai.tools.confirm import ConfirmationStore


def test_propose_then_resolve_returns_payload() -> None:
    store = ConfirmationStore(timeout_seconds=60)
    token = store.propose("ssh_run", {"command": "ls"}, "Run: ls")

    payload = store.resolve(token, "ssh_run")

    assert payload == {"command": "ls"}


def test_resolve_unknown_token_raises_key_error() -> None:
    store = ConfirmationStore(timeout_seconds=60)
    with pytest.raises(KeyError):
        store.resolve("doesnotexist", "ssh_run")


def test_resolve_consumes_token_so_it_cannot_be_reused() -> None:
    store = ConfirmationStore(timeout_seconds=60)
    token = store.propose("ssh_run", {"command": "ls"}, "Run: ls")

    store.resolve(token, "ssh_run")

    with pytest.raises(KeyError):
        store.resolve(token, "ssh_run")


def test_resolve_wrong_action_type_raises_key_error() -> None:
    store = ConfirmationStore(timeout_seconds=60)
    token = store.propose("ssh_run", {"command": "ls"}, "Run: ls")

    with pytest.raises(KeyError):
        store.resolve(token, "send_file")


def test_resolve_expired_token_raises_timeout_error() -> None:
    store = ConfirmationStore(timeout_seconds=0)
    token = store.propose("ssh_run", {"command": "ls"}, "Run: ls")
    time.sleep(0.01)

    with pytest.raises(TimeoutError):
        store.resolve(token, "ssh_run")
