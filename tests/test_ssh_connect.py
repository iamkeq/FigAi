from __future__ import annotations

import paramiko
import pytest

from figai.config import SshHost
from figai.ssh import client as ssh_client

HOST = SshHost(host="10.0.0.5", user="dave", key_path="/k")


class FakeClient:
    outcomes: list = []
    created = 0

    def __init__(self) -> None:
        FakeClient.created += 1

    def set_missing_host_key_policy(self, _p) -> None: ...
    def load_system_host_keys(self) -> None: ...
    def close(self) -> None: ...

    def connect(self, **_kwargs) -> None:
        outcome = FakeClient.outcomes.pop(0)
        if outcome is not None:
            raise outcome


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeClient.created = 0
    monkeypatch.setattr(paramiko, "SSHClient", FakeClient)


def test_retries_banner_drop_then_succeeds() -> None:
    FakeClient.outcomes = [paramiko.SSHException("Error reading SSH protocol banner"), ConnectionResetError(), None]
    waits: list[float] = []
    assert ssh_client.connect(HOST, sleep=waits.append) is not None
    assert FakeClient.created == 3 and len(waits) == 2


def test_gives_up_with_connection_error() -> None:
    FakeClient.outcomes = [EOFError()] * 3
    with pytest.raises(ConnectionError):
        ssh_client.connect(HOST, sleep=lambda _s: None)


@pytest.mark.parametrize(
    "error",
    [paramiko.AuthenticationException("no"), paramiko.SSHException("Server '10.0.0.5' not found in known_hosts"), TimeoutError("t")],
)
def test_permanent_failures_do_not_retry(error) -> None:
    FakeClient.outcomes = [error, None]
    with pytest.raises(ConnectionError):
        ssh_client.connect(HOST, sleep=lambda _s: None)
    assert FakeClient.created == 1
