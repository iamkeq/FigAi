from __future__ import annotations

import json

import pytest

from figai.agent.tools import ToolRegistry
from figai.config import SshHost
from figai.ssh import run as ssh_run_module
from figai.ssh.client import CommandResult
from figai.ssh.run import register_ssh_run_tools
from figai.tools.confirm import ConfirmationStore


class FakeSettings:
    def __init__(self) -> None:
        self.ssh_hosts = {"nas": SshHost(host="10.0.0.5", user="matt", port=22, key_path="/tmp/key")}


class FakeConfig:
    def __init__(self) -> None:
        self.settings = FakeSettings()


def build_registry(timeout_seconds: int = 60) -> tuple[ToolRegistry, ConfirmationStore]:
    registry = ToolRegistry()
    confirmations = ConfirmationStore(timeout_seconds=timeout_seconds)
    register_ssh_run_tools(registry, FakeConfig(), confirmations)
    return registry, confirmations


def test_propose_unknown_host_alias_returns_error() -> None:
    registry, _ = build_registry()

    result = json.loads(
        registry.execute("propose_ssh_command", json.dumps({"host_alias": "nope", "command": "ls"}))
    )

    assert "error" in result


def test_propose_known_host_returns_awaiting_confirmation_with_token() -> None:
    registry, _ = build_registry()

    result = json.loads(
        registry.execute("propose_ssh_command", json.dumps({"host_alias": "NAS", "command": "ls -la"}))
    )

    assert result["status"] == "awaiting_confirmation"
    assert "token" in result
    assert "ls -la" in result["preview"]


def test_resolve_without_prior_proposal_returns_error() -> None:
    registry, _ = build_registry()

    result = json.loads(registry.execute("resolve_ssh_command", json.dumps({"token": "madeup"})))

    assert "error" in result


def test_resolve_executes_the_proposed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, _ = build_registry()

    monkeypatch.setattr(
        ssh_run_module,
        "run_command",
        lambda host, command, timeout: CommandResult(stdout="hi\n", stderr="", exit_code=0, timed_out=False),
    )

    propose_result = json.loads(
        registry.execute("propose_ssh_command", json.dumps({"host_alias": "nas", "command": "echo hi"}))
    )
    token = propose_result["token"]

    resolve_result = json.loads(registry.execute("resolve_ssh_command", json.dumps({"token": token})))

    assert resolve_result == {"exit_code": 0, "timed_out": False, "stdout": "hi\n", "stderr": ""}


def test_resolve_cannot_reuse_the_same_token_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, _ = build_registry()
    monkeypatch.setattr(
        ssh_run_module,
        "run_command",
        lambda host, command, timeout: CommandResult(stdout="", stderr="", exit_code=0, timed_out=False),
    )

    propose_result = json.loads(
        registry.execute("propose_ssh_command", json.dumps({"host_alias": "nas", "command": "echo hi"}))
    )
    token = propose_result["token"]
    registry.execute("resolve_ssh_command", json.dumps({"token": token}))

    second_result = json.loads(registry.execute("resolve_ssh_command", json.dumps({"token": token})))
    assert "error" in second_result


def test_resolve_expired_confirmation_returns_error() -> None:
    registry, _ = build_registry(timeout_seconds=0)

    propose_result = json.loads(
        registry.execute("propose_ssh_command", json.dumps({"host_alias": "nas", "command": "echo hi"}))
    )
    import time

    time.sleep(0.01)

    result = json.loads(registry.execute("resolve_ssh_command", json.dumps({"token": propose_result["token"]})))
    assert "error" in result
