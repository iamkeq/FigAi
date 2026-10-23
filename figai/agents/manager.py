"""Talking to, and troubleshooting, other AI agents (Hermes, OpenClaw, ...) that run on this PC or
on a server. "Talk" means running the agent's own one-shot command (e.g. `openclaw agent --message`)
and returning what it prints. "Diagnose" runs a bundle of READ-ONLY checks -- processes, ports, systemd,
docker, the agent's own status commands, recent logs, machine load -- and hands the raw findings back
so the model can work out why it's down. Commands come from config.yaml, never from the model."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

from figai.config import AgentConfig, Config
from figai.logging_setup import get_logger
from figai.process_utils import quiet_run
from figai.ssh.client import CommandResult, run_command

logger = get_logger("agents")

Runner = Callable[[str, str, int], CommandResult]  # (host, command, timeout seconds)
SECTION_CHARS = 1500
ASK_OUTPUT_CHARS = 4000

# Best-effort defaults from each project's CLI docs. Override any of them per agent in config.yaml.
KIND_DEFAULTS: dict[str, dict] = {
    "openclaw": {
        "ask_command": "openclaw agent --message {message}",
        "status_commands": ["openclaw status", "openclaw gateway status"],
        "restart_command": "openclaw gateway restart",
        "services": ["openclaw-gateway", "openclaw"],
    },
    "hermes": {
        "ask_command": "hermes chat -q {message}",
        "status_commands": ["hermes doctor"],
        "restart_command": None,
        "services": ["hermes-gateway", "hermes"],
    },
    "custom": {"ask_command": None, "status_commands": [], "restart_command": None, "services": []},
}
SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|xox[abprs]-[A-Za-z0-9\-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._\-]{16,}"
    r"|(?i:api[_-]?key|token|secret|password)\s*[=:]\s*\S+)"
)


class AgentError(Exception):
    pass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    kind: str
    host: str
    ask_command: str | None
    status_commands: tuple[str, ...]
    restart_command: str | None
    services: tuple[str, ...]
    log_paths: tuple[str, ...]
    keyword: str
    port: int | None
    env_setup: str | None = None

    @property
    def is_local(self) -> bool:
        return self.host.lower() == "local"


def build_specs(agents: list[AgentConfig]) -> dict[str, AgentSpec]:
    specs: dict[str, AgentSpec] = {}
    for agent in agents:
        defaults = KIND_DEFAULTS[agent.kind]
        specs[agent.name.lower()] = AgentSpec(
            name=agent.name,
            kind=agent.kind,
            host=agent.host,
            ask_command=agent.ask_command or defaults["ask_command"],
            status_commands=tuple(agent.status_commands if agent.status_commands is not None else defaults["status_commands"]),
            restart_command=agent.restart_command or defaults["restart_command"],
            services=tuple(agent.services if agent.services is not None else defaults["services"]),
            log_paths=tuple(agent.log_paths),
            keyword=(agent.process_keyword or (agent.kind if agent.kind != "custom" else agent.name)).lower(),
            port=agent.port,
            env_setup=agent.env_setup,
        )
    return specs


def redact(text: str) -> str:
    return SECRET_PATTERN.sub("[redacted]", text)


def quote_for(host: str, text: str) -> str:
    if host.lower() == "local" and sys.platform == "win32":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def make_runner(config: Config) -> Runner:
    def run(host: str, command: str, timeout: int) -> CommandResult:
        if host.lower() == "local":
            try:
                done = quiet_run(
                    command, shell=True, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
                )
            except subprocess.TimeoutExpired:
                return CommandResult(stdout="", stderr="", exit_code=-1, timed_out=True)
            return CommandResult(done.stdout[:20000], done.stderr[:20000], done.returncode, False)
        ssh_host = config.settings.ssh_hosts.get(host.lower())
        if ssh_host is None:
            raise AgentError(
                f"SSH host alias '{host}' isn't configured in .env (add an SSH_HOST_{host.upper()} line); "
                f"known hosts: {sorted(config.settings.ssh_hosts)}"
            )
        return run_command(ssh_host, command, timeout=timeout)

    return run


def _wrap_remote(spec: AgentSpec, command: str) -> str:
    """Servers' non-interactive SSH shells skip the login profile, where node/npm/pipx put their
    binaries -- run through a login shell so `openclaw` and `hermes` are found."""
    setup = f"{spec.env_setup}; " if spec.env_setup else ""
    return f"bash -lc {shlex.quote(setup + command)}"


@dataclass
class AgentManager:
    specs: dict[str, AgentSpec]
    runner: Runner
    local_processes: Callable[[str], list[str]] = field(default=lambda keyword: _local_processes(keyword))

    def names(self) -> list[str]:
        return [s.name for s in self.specs.values()]

    def get(self, name: str) -> AgentSpec:
        spec = self.specs.get(name.strip().lower())
        if spec is None:
            raise AgentError(f"I don't know an agent called '{name}'. Configured: {self.names() or 'none'}")
        return spec

    def _run(self, spec: AgentSpec, command: str, timeout: int) -> CommandResult:
        return self.runner(spec.host, command if spec.is_local else _wrap_remote(spec, command), timeout)

    def ask(self, name: str, message: str, timeout: int = 120) -> dict:
        spec = self.get(name)
        if not spec.ask_command:
            raise AgentError(f"No ask_command is configured for {spec.name} (set `ask_command` in config.yaml).")
        command = spec.ask_command.replace("{message}", quote_for(spec.host, message))
        result = self._run(spec, command, timeout)
        output = redact(result.stdout.strip() or result.stderr.strip())
        if result.timed_out:
            return {"error": f"{spec.name} didn't answer within {timeout}s.", "partial": output[:ASK_OUTPUT_CHARS]}
        if result.exit_code != 0:
            return {
                "error": f"{spec.name}'s command failed (exit {result.exit_code}). If the command itself is wrong for your "
                f"install, fix `ask_command` in config.yaml.",
                "output": output[:ASK_OUTPUT_CHARS],
            }
        return {"agent": spec.name, "reply": output[:ASK_OUTPUT_CHARS] or "(it printed nothing)"}

    def restart(self, spec: AgentSpec, timeout: int = 90) -> CommandResult:
        if not spec.restart_command:
            raise AgentError(f"No restart_command is configured for {spec.name} (set `restart_command` in config.yaml).")
        return self._run(spec, spec.restart_command, timeout)

    # -- diagnosis ---------------------------------------------------------------------------

    def diagnose(self, name: str) -> dict:
        spec = self.get(name)
        sections = self._diagnose_local(spec) if spec.is_local else self._diagnose_remote(spec)
        return {
            "agent": spec.name,
            "host": spec.host,
            "findings": {title: redact(text.strip())[-SECTION_CHARS:] or "(nothing)" for title, text in sections.items()},
            "note": "Read-only checks. Work out what's wrong from these findings; propose a restart only if it's down.",
        }

    def _diagnose_local(self, spec: AgentSpec) -> dict[str, str]:
        sections: dict[str, str] = {}
        procs = self.local_processes(spec.keyword)
        sections["processes"] = "\n".join(procs) if procs else f"no running process matching '{spec.keyword}'"
        for command in spec.status_commands:
            sections[f"$ {command}"] = self._run_text(spec, command)
        for path in spec.log_paths:
            sections[f"log: {path}"] = self._run_text(spec, f'powershell -NoProfile -Command "Get-Content -Tail 40 -Path \'{path}\'"')
        return sections

    def _diagnose_remote(self, spec: AgentSpec) -> dict[str, str]:
        script = _remote_script(spec)
        result = self.runner(spec.host, _wrap_remote(spec, script), 60)
        if result.timed_out:
            raise AgentError(f"The diagnostic checks on {spec.host} timed out.")
        return _split_sections(result.stdout) or {"output": result.stdout + result.stderr}

    def _run_text(self, spec: AgentSpec, command: str) -> str:
        result = self._run(spec, command, 60)
        text = (result.stdout + result.stderr).strip()
        return f"(exit {result.exit_code}) {text}" if result.exit_code != 0 else text


def _local_processes(keyword: str) -> list[str]:
    import psutil

    found: list[str] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            haystack = f"{proc.info['name']} {' '.join(proc.info['cmdline'] or [])}".lower()
            if keyword in haystack and "figai" not in haystack:
                found.append(f"pid {proc.info['pid']}: {haystack[:120]}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found[:10]


def _remote_script(spec: AgentSpec) -> str:
    kw = shlex.quote(spec.keyword)
    parts = [
        ("processes", f"pgrep -af {kw} | grep -v pgrep | head -n 10"),
        ("listening ports", f"ss -ltnp 2>/dev/null | grep -i {kw} | head -n 10"),
        ("docker", f"docker ps -a --filter name={kw} --format '{{{{.Names}}}}: {{{{.Status}}}}' 2>&1 | head -n 10"),
        ("machine", "uptime; free -m | head -n 2; df -h / | tail -n 1"),
    ]
    if spec.port:
        parts.insert(2, (f"port {spec.port}", f"ss -ltn 2>/dev/null | grep -E ':{int(spec.port)}\\b' || echo 'nothing is listening'"))
    for unit in spec.services:
        u = shlex.quote(unit)
        parts.append((f"systemd user: {unit}", f"systemctl --user is-active {u} 2>&1; systemctl --user status {u} --no-pager -n 12 2>&1 | head -n 20"))
        parts.append((f"systemd system: {unit}", f"systemctl is-active {u} 2>&1; systemctl status {u} --no-pager -n 12 2>&1 | head -n 20"))
        parts.append((f"journal: {unit}", f"journalctl --user -u {u} -n 30 --no-pager 2>&1 | tail -n 30; journalctl -u {u} -n 30 --no-pager 2>&1 | tail -n 30"))
    for command in spec.status_commands:
        parts.append((f"$ {command}", f"timeout 40 {command} 2>&1 | head -n 40"))
    for path in spec.log_paths:
        parts.append((f"log: {path}", f"tail -n 40 {shlex.quote(path)} 2>&1"))
    return "; ".join(f"echo '=== {title} ==='; {command}" for title, command in parts)


def _split_sections(output: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    title: str | None = None
    for line in output.splitlines():
        match = re.fullmatch(r"=== (.*) ===", line)
        if match:
            title = match.group(1)
            sections[title] = ""
        elif title is not None:
            sections[title] += line + "\n"
    return sections
