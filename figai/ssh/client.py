"""Key-based SSH connections only -- no passwords, no interactive host-key prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import paramiko

from figai.config import SshHost

CONNECT_TIMEOUT_SECONDS = 10


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


def connect(host: SshHost) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    # Reject unknown host keys instead of prompting -- equivalent to
    # StrictHostKeyChecking=yes; the host must already be in known_hosts.
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.load_system_host_keys()
    client.connect(
        hostname=host.host,
        port=host.port,
        username=host.user,
        key_filename=host.key_path,
        allow_agent=False,
        look_for_keys=False,
        timeout=CONNECT_TIMEOUT_SECONDS,
    )
    return client


def run_command(host: SshHost, command: str, timeout: int = 30) -> CommandResult:
    client = connect(host)
    try:
        return execute(client, command, timeout)
    finally:
        client.close()


def execute(client: Any, command: str, timeout: int) -> CommandResult:
    """Runs `command` on an already-connected client. Split out from `run_command`
    so tests can exercise it against a fake client without any real network I/O."""
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    try:
        exit_code = stdout.channel.recv_exit_status()
    except Exception:
        return CommandResult(stdout="", stderr="", exit_code=-1, timed_out=True)
    return CommandResult(
        stdout=stdout.read().decode("utf-8", errors="replace")[:20000],
        stderr=stderr.read().decode("utf-8", errors="replace")[:20000],
        exit_code=exit_code,
        timed_out=False,
    )


def open_sftp(host: SshHost) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    client = connect(host)
    return client, client.open_sftp()
