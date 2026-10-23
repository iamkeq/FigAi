from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from figai.agent.auto_confirm import RESOLVERS
from figai.agent.tools import ToolRegistry
from figai.agents.manager import AgentError, AgentManager, build_specs, quote_for, redact, _split_sections
from figai.agents.tools import register_agent_tools
from figai.config import AgentConfig, SshHost
from figai.ssh import cron
from figai.ssh.client import CommandResult
from figai.ssh.cron_tools import register_cron_tools
from figai.tools.confirm import ConfirmationStore

EXISTING = """MAILTO=me@example.com
# backups
0 2 * * * /usr/local/bin/backup.sh
*/5 * * * * /opt/check.sh --quiet
"""


# -- pure crontab logic -------------------------------------------------------------------


def test_add_keeps_every_existing_line_untouched() -> None:
    out = cron.add_job(EXISTING, "nightly-update", "0 4 * * *", "/home/dave/update.sh")
    assert out.startswith(EXISTING)
    assert out.splitlines()[-1] == "0 4 * * * /home/dave/update.sh # fig:nightly-update"


def test_only_fig_jobs_are_managed_and_listed_correctly() -> None:
    text = cron.add_job(EXISTING, "mine", "@daily", "echo hi")
    jobs = cron.parse(text)
    assert [(j.name, j.managed) for j in jobs] == [(None, False), (None, False), ("mine", True)]
    assert jobs[0].schedule == "0 2 * * *" and jobs[0].command == "/usr/local/bin/backup.sh"
    assert jobs[2].schedule == "@daily" and jobs[2].command == "echo hi"


def test_cannot_touch_jobs_fig_did_not_create() -> None:
    with pytest.raises(cron.CronError, match="only edits jobs he created"):
        cron.remove_job(EXISTING, "backup.sh")
    with pytest.raises(cron.CronError):
        cron.set_enabled(EXISTING, "backup", False)


def test_disable_enable_remove_round_trip() -> None:
    text = cron.add_job(EXISTING, "mine", "*/10 * * * *", "echo hi")
    off = cron.set_enabled(text, "mine", False)
    assert "#DISABLED# */10 * * * * echo hi # fig:mine" in off
    assert [j.enabled for j in cron.parse(off) if j.name] == [False]
    assert cron.set_enabled(off, "mine", True) == text
    assert cron.remove_job(text, "MINE") == EXISTING


@pytest.mark.parametrize(
    "schedule", ["", "every day", "0 4 * *", "0 4 * * * *", "0 4 * * $(rm -rf /)", "0;4 * * * *"]
)
def test_bad_schedules_are_rejected(schedule: str) -> None:
    with pytest.raises(cron.CronError):
        cron.add_job("", "x", schedule, "echo hi")


def test_command_validation_and_percent_escaping() -> None:
    with pytest.raises(cron.CronError):
        cron.add_job("", "x", "@daily", "echo a\necho b")
    with pytest.raises(cron.CronError):
        cron.add_job("", "x", "@daily", "   ")
    with pytest.raises(cron.CronError):
        cron.add_job("", "bad name", "@daily", "echo")
    with pytest.raises(cron.CronError):
        cron.add_job(cron.add_job("", "dup", "@daily", "echo"), "DUP", "@daily", "echo")
    assert "date +\\%F" in cron.add_job("", "d", "@daily", "date +%F")


# -- tools over a fake server --------------------------------------------------------------


class FakeServer:
    def __init__(self, crontab: str = EXISTING) -> None:
        self.crontab = crontab
        self.installs: list[str] = []
        self.fail_install = False

    def __call__(self, host, command, stdin_text, timeout):
        if stdin_text is not None:
            if self.fail_install:
                return CommandResult("", "bad crontab", 1, False)
            self.installs.append(stdin_text)
            self.crontab = stdin_text
            return CommandResult("", "", 0, False)
        if command.startswith("crontab -l"):
            return CommandResult(self.crontab, "", 0, False)
        return CommandResult("Oct 23 04:00:01 CRON[1]: (dave) CMD (update.sh)", "", 0, False)


def make_cron(server: FakeServer | None = None):
    server = server or FakeServer()
    config = SimpleNamespace(
        settings=SimpleNamespace(ssh_hosts={"turboplex": SshHost(host="h", user="u", key_path="k")}),
        app=SimpleNamespace(cron_host_alias="turboplex", vault=SimpleNamespace(ssh_host_alias="vault")),
    )
    registry = ToolRegistry()
    register_cron_tools(registry, config, ConfirmationStore(), runner=server)

    def call(tool, **args):
        return json.loads(registry.execute(tool, json.dumps(args)))

    return call, server


def test_adding_a_job_asks_first_then_installs_with_a_backup_and_verifies() -> None:
    call, server = make_cron()
    proposed = call("propose_cron_change", action="add", name="nightly", schedule="0 4 * * *", command="/home/dave/update.sh")
    assert proposed["status"] == "awaiting_confirmation" and "0 4 * * *" in proposed["preview"]
    assert server.installs == []  # nothing yet
    done = call("resolve_cron_change", token=proposed["token"])
    assert done["status"] == "done" and done["backup"]
    assert server.installs[0].startswith(EXISTING) and "# fig:nightly" in server.installs[0]
    listed = call("list_cron_jobs")
    assert [j["name"] for j in listed["jobs"] if j["managed_by_fig"]] == ["nightly"]


def test_bad_requests_fail_at_the_proposal_not_after_the_yes() -> None:
    call, server = make_cron()
    assert "error" in call("propose_cron_change", action="add", name="x", schedule="whenever", command="echo")
    assert "error" in call("propose_cron_change", action="remove", name="backup")
    assert "error" in call("propose_cron_change", action="add", name="x", command="echo")  # no schedule
    assert "error" in call("propose_cron_change", action="add", name="x", schedule="@daily", command="echo", host="nowhere")
    assert server.installs == []


def test_crontab_failure_is_reported_and_tokens_are_single_use() -> None:
    call, server = make_cron()
    token = call("propose_cron_change", action="add", name="x", schedule="@daily", command="echo")["token"]
    server.fail_install = True
    assert "refused" in call("resolve_cron_change", token=token)["error"]
    assert "error" in call("resolve_cron_change", token=token)


def test_pause_resume_and_remove_only_fig_jobs() -> None:
    server = FakeServer(cron.add_job(EXISTING, "job", "@hourly", "echo hi"))
    call, _ = make_cron(server)
    for action in ("disable", "enable", "remove"):
        token = call("propose_cron_change", action=action, name="job")["token"]
        assert call("resolve_cron_change", token=token)["status"] == "done"
    assert server.crontab == EXISTING


def test_cron_log_and_never_auto_confirmed_by_voice() -> None:
    call, _ = make_cron()
    assert "CRON" in call("cron_log")["log"]
    assert "cron_change" not in RESOLVERS


# -- agents ---------------------------------------------------------------------------------


class FakeRunner:
    def __init__(self, result: CommandResult | None = None, output: str = "") -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.result = result or CommandResult(output, "", 0, False)

    def __call__(self, host, command, timeout):
        self.calls.append((host, command, timeout))
        return self.result


def make_agents(runner: FakeRunner, agents: list[AgentConfig] | None = None):
    agents = agents or [
        AgentConfig(name="OpenClaw", kind="openclaw", host="turboplex"),
        AgentConfig(name="Hermes", kind="hermes", host="local"),
    ]
    manager = AgentManager(build_specs(agents), runner, local_processes=lambda kw: [f"pid 1: {kw} gateway"])
    registry = ToolRegistry()
    register_agent_tools(registry, manager, ConfirmationStore())

    def call(tool, **args):
        return json.loads(registry.execute(tool, json.dumps(args)))

    return call, runner


def test_list_agents_and_unknown_agent() -> None:
    call, _ = make_agents(FakeRunner())
    assert [a["name"] for a in call("list_agents")["agents"]] == ["OpenClaw", "Hermes"]
    assert "don't know an agent" in call("ask_agent", name="bob", message="hi")["error"]


def test_ask_runs_the_cli_on_the_right_host_with_the_message_quoted() -> None:
    call, runner = make_agents(FakeRunner(output="All good, boss."))
    out = call("ask_agent", name="openclaw", message="what's up; rm -rf / `x`")
    assert out == {"agent": "OpenClaw", "reply": "All good, boss."}
    host, command, _ = runner.calls[0]
    assert host == "turboplex" and command.startswith("bash -lc ")
    assert "openclaw agent --message" in command and "rm -rf" in command and "'\"'\"'" in command  # quoted, not executed


def test_local_agents_run_without_a_login_shell() -> None:
    call, runner = make_agents(FakeRunner(output="hi"))
    call("ask_agent", name="hermes", message="hello")
    host, command, _ = runner.calls[0]
    assert host == "local" and not command.startswith("bash -lc") and command.startswith("hermes chat -q")


def test_ask_failures_explain_what_to_fix() -> None:
    call, _ = make_agents(FakeRunner(CommandResult("", "command not found", 127, False)))
    out = call("ask_agent", name="openclaw", message="hi")
    assert "ask_command" in out["error"] and "command not found" in out["output"]
    call2, _ = make_agents(FakeRunner(CommandResult("", "", -1, True)))
    assert "didn't answer" in call2("ask_agent", name="openclaw", message="hi")["error"]
    call3, _ = make_agents(FakeRunner(), [AgentConfig(name="x")])
    assert "ask_command" in call3("ask_agent", name="x", message="hi")["error"]


def test_diagnose_remote_runs_one_script_and_returns_sections_with_secrets_redacted() -> None:
    output = (
        "=== processes ===\n(nothing)\n=== systemd user: openclaw-gateway ===\ninactive\n"
        "Active: failed (Result: exit-code)\n=== journal: openclaw-gateway ===\n"
        "error: OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnop1234 missing port\n"
    )
    call, runner = make_agents(FakeRunner(output=output))
    report = call("diagnose_agent", name="OpenClaw")
    assert len(runner.calls) == 1  # one SSH connection, not one per check
    script = runner.calls[0][1]
    assert "pgrep -af openclaw" in script and "systemctl --user is-active" in script and "$ openclaw status" in script
    findings = report["findings"]
    assert "failed" in findings["systemd user: openclaw-gateway"]
    assert "sk-or-v1" not in json.dumps(report) and "[redacted]" in findings["journal: openclaw-gateway"]


def test_diagnose_local_uses_process_list_and_status_commands() -> None:
    call, runner = make_agents(FakeRunner(output="doctor: all checks passed"))
    report = call("diagnose_agent", name="hermes")
    assert report["findings"]["processes"] == "pid 1: hermes gateway"
    assert "all checks passed" in report["findings"]["$ hermes doctor"]


def test_restart_asks_first_and_is_allowed_by_voice_auto_confirm() -> None:
    call, runner = make_agents(FakeRunner(output="restarted"))
    proposed = call("propose_agent_restart", name="openclaw")
    assert proposed["status"] == "awaiting_confirmation" and runner.calls == []
    assert call("resolve_agent_restart", token=proposed["token"])["status"] == "restarted"
    assert "openclaw gateway restart" in runner.calls[0][1]
    assert RESOLVERS["agent_restart"] == "resolve_agent_restart"
    assert "restart_command" in call("propose_agent_restart", name="hermes")["error"]  # none configured by default


def test_config_overrides_beat_kind_defaults() -> None:
    specs = build_specs([AgentConfig(name="oc", kind="openclaw", ask_command="oc ask {message}", restart_command="x", services=[])])
    spec = specs["oc"]
    assert spec.ask_command == "oc ask {message}" and spec.restart_command == "x" and spec.services == ()


def test_helpers() -> None:
    assert "[redacted]" in redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    assert _split_sections("junk\n=== a ===\n1\n=== b ===\n2\n") == {"a": "1\n", "b": "2\n"}
    assert quote_for("turboplex", "it's") == "'it'\"'\"'s'"
    with pytest.raises(AgentError):
        AgentManager({}, FakeRunner()).get("none")


def test_run_command_with_input_feeds_stdin_and_reports_exit(monkeypatch) -> None:
    from figai.ssh import client as ssh_client

    written: list[str] = []
    closed: list[bool] = []

    class Chan:
        def __init__(self, code=0):
            self.code = code

        def shutdown_write(self):
            closed.append(True)

        def recv_exit_status(self):
            return self.code

    class Stream:
        def __init__(self, data=b"", code=0):
            self.channel, self._data = Chan(code), data

        def read(self):
            return self._data

        def write(self, text):
            written.append(text)

    class FakeClient:
        def exec_command(self, command, timeout=0):
            return Stream(), Stream(b"ok", 0), Stream(b"")

        def close(self):
            pass

    monkeypatch.setattr(ssh_client, "connect", lambda _h: FakeClient())
    result = ssh_client.run_command_with_input(SshHost(host="h", user="u", key_path="k"), "crontab -", "0 4 * * * x\n")
    assert written == ["0 4 * * * x\n"] and closed == [True]
    assert (result.stdout, result.exit_code) == ("ok", 0)


def test_agents_section_parses_from_yaml_config(tmp_path) -> None:
    from figai.config import AppConfig

    app = AppConfig.model_validate(
        {
            "models": {"default_chat": "x", "vision": "y"},
            "vault": {"ssh_host_alias": "v", "remote_path": "/v", "local_path": str(tmp_path)},
            "cron_host_alias": "turboplex",
            "agents": [{"name": "openclaw", "kind": "openclaw", "host": "turboplex"}, {"name": "hermes", "kind": "hermes"}],
        }
    )
    assert [a.host for a in app.agents] == ["turboplex", "local"] and app.cron_host_alias == "turboplex"
