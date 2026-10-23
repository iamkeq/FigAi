"""Key-based SSH connections only -- no passwords, no interactive host-key prompts."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import paramiko

from figai.config import SshHost

CONNECT_TIMEOUT_SECONDS = 10
BANNER_TIMEOUT_SECONDS = 30
CONNECT_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


def _new_client() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    # Reject unknown host keys instead of prompting -- equivalent to
    # StrictHostKeyChecking=yes; the host must already be in known_hosts.
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.load_system_host_keys()
    return client


def connect(
    host: SshHost, attempts: int = CONNECT_ATTEMPTS, sleep: Callable[[float], None] = time.sleep
) -> paramiko.SSHClient:
    """Connects with a few retries for transient drops (sshd rate-limiting rapid new
    connections closes them before the banner: 'Error reading SSH protocol banner').
    Every failure is raised as ConnectionError (an OSError) so callers can fall back
    cleanly instead of crashing on paramiko's own exception types."""
    last: Exception | None = None
    for attempt in range(attempts):
        client = _new_client()
        try:
            client.connect(
                hostname=host.host,
                port=host.port,
                username=host.user,
                key_filename=host.key_path,
                allow_agent=False,
                look_for_keys=False,
                timeout=CONNECT_TIMEOUT_SECONDS,
                banner_timeout=BANNER_TIMEOUT_SECONDS,
                auth_timeout=BANNER_TIMEOUT_SECONDS,
            )
            return client
        except paramiko.AuthenticationException as exc:
            raise ConnectionError(f"SSH login to {host.host} was rejected (is the key installed?): {exc}") from exc
        except paramiko.BadHostKeyException as exc:
            raise ConnectionError(f"SSH host key for {host.host} doesn't match known_hosts: {exc}") from exc
        except paramiko.SSHException as exc:
            if "known_hosts" in str(exc):
                raise ConnectionError(
                    f"{host.host} isn't in known_hosts; run ssh to it once by hand to accept its key: {exc}"
                ) from exc
            last = exc
        except (ConnectionResetError, EOFError) as exc:
            last = exc
        except OSError as exc:  # timeouts, host down: retrying just delays the fallback
            raise ConnectionError(f"Couldn't reach {host.host}: {exc}") from exc
        client.close()
        if attempt < attempts - 1:
            sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    raise ConnectionError(f"SSH to {host.host} kept dropping the connection: {last}") from last


def run_command(host: SshHost, command: str, timeout: int = 30) -> CommandResult:
    client = connect(host)
    try:
        return execute(client, command, timeout)
    finally:
        client.close()


def run_command_with_input(host: SshHost, command: str, stdin_text: str, timeout: int = 30) -> CommandResult:
    """Like run_command, but feeds `stdin_text` to the command (e.g. `crontab -`)."""
    client = connect(host)
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        stdin.write(stdin_text)
        stdin.channel.shutdown_write()
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
