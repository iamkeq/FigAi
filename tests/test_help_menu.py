from __future__ import annotations

import json
from pathlib import Path

from figai import help as help_menu
from figai.agent.tools import Tool, ToolRegistry

SHEET = """intro

## Commands
- `!help` -- menu
- `!tools` -- tools

## Email
- "summarize my emails" -- do it
- second

## This PC
- "open spotify"
"""


def test_menu_lists_commands_and_one_example_per_topic() -> None:
    menu = help_menu.menu(SHEET)
    assert "• *Email* -- \"summarize my emails\" -- do it" in menu
    assert "• *This PC* -- \"open spotify\"" in menu
    assert "`!help` -- menu" in menu and "second" not in menu


def test_topic_matches_headings_then_text_and_all() -> None:
    assert "second" in help_menu.topic(SHEET, "email")
    assert "open spotify" in help_menu.topic(SHEET, "spotify")  # found by a word inside a section
    everything = help_menu.topic(SHEET, "all")
    assert "*Email*" in everything and "*This PC*" in everything and "*Commands*" in everything
    assert "No help matches" in help_menu.topic(SHEET, "zzz")


def test_tools_list_is_generated_and_filterable() -> None:
    registry = ToolRegistry()
    for name, desc in [("add_check", "Start monitoring a device. Alerts go to the channel."), ("list_cron_jobs", "List cron jobs.")]:
        registry.register(Tool(name=name, description=desc, parameters={"type": "object", "properties": {}}, handler=lambda a: "{}"))
    listing = help_menu.tools_list(help_menu.describe_registry(registry))
    assert "`add_check` -- Start monitoring a device." in listing and "Alerts go to" not in listing
    assert "2 tool(s)" in listing
    only = help_menu.tools_list(help_menu.describe_registry(registry), "cron")
    assert "list_cron_jobs" in only and "add_check" not in only
    assert "No tool matches" in help_menu.tools_list([], "x")


def test_real_sheet_has_a_topic_for_every_feature_area() -> None:
    body = (Path(__file__).resolve().parent.parent / "skills" / "help" / "SKILL.md").read_text()
    headings = [h.lower() for h, _ in help_menu.sections(body)]
    for expected in ["commands", "updates", "email (read-only)", "vault notes", "reminders", "voice"]:
        assert expected in headings, expected
    menu = help_menu.menu(body)
    assert len(menu) < 3500  # a menu, not a wall of text (Slack truncates long messages)
    assert "!tools" in menu


def test_every_real_tool_has_a_description_for_the_tools_list(tmp_path: Path) -> None:
    from figai.agent.session import AgentSession
    from figai.config import Config

    (tmp_path / "config.yaml").write_text(
        f'models: {{default_chat: "a/b", vision: "c/d"}}\nvault: {{ssh_host_alias: "v", remote_path: "/r", local_path: "{tmp_path / "lv"}"}}\n'
        f'state_path: "{tmp_path / "state.json"}"\n'
    )
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=k\nSLACK_BOT_TOKEN=x\nSLACK_APP_TOKEN=y\nALLOWED_SLACK_USER_ID=U1\n")
    session = AgentSession(Config.load(tmp_path / ".env", tmp_path / "config.yaml"))
    described = help_menu.describe_registry(session.loop.tools)
    assert len(described) > 60
    assert all(desc.strip() for _name, desc in described), [n for n, d in described if not d.strip()]
    assert {"add_check", "post_slack_message", "memory_search", "set_briefing", "health_check"} <= {n for n, _ in described}
