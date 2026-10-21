from __future__ import annotations

import json
from pathlib import Path

import pytest

from figai.agent.tools import ToolRegistry
from figai.skills_system.core_tools import register_skill_management_tools
from figai.skills_system.loader import SkillError, SkillLoader


def make_skill(root: Path, name: str, description: str, body: str, tools_py: str | None = None) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )
    if tools_py is not None:
        (skill_dir / "tools.py").write_text(tools_py, encoding="utf-8")


def test_discover_lists_only_approved_skills_with_valid_skill_md(tmp_path: Path) -> None:
    make_skill(tmp_path, "ssh", "SSH access", "instructions here")
    (tmp_path / "_pending" / "draft").mkdir(parents=True)
    (tmp_path / "_pending" / "draft" / "SKILL.md").write_text(
        "---\nname: draft\ndescription: not yet approved\n---\nbody", encoding="utf-8"
    )

    loader = SkillLoader(tmp_path)
    summaries = loader.discover()

    assert [s.name for s in summaries] == ["ssh"]
    assert summaries[0].description == "SSH access"


def test_get_summary_text_formats_as_bullet_list(tmp_path: Path) -> None:
    make_skill(tmp_path, "ssh", "SSH access", "body")
    loader = SkillLoader(tmp_path)

    assert loader.get_summary_text() == "- ssh: SSH access"


def test_get_summary_text_empty_when_no_skills(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    assert loader.get_summary_text() == ""


def test_get_instructions_returns_body_without_frontmatter(tmp_path: Path) -> None:
    make_skill(tmp_path, "ssh", "SSH access", "Full instructions go here.")
    loader = SkillLoader(tmp_path)

    assert loader.get_instructions("ssh") == "Full instructions go here."


def test_get_instructions_unknown_skill_raises(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    with pytest.raises(SkillError):
        loader.get_instructions("nope")


def test_get_instructions_cannot_reach_into_pending(tmp_path: Path) -> None:
    (tmp_path / "_pending" / "draft").mkdir(parents=True)
    (tmp_path / "_pending" / "draft" / "SKILL.md").write_text(
        "---\nname: draft\ndescription: d\n---\nsecret body", encoding="utf-8"
    )
    loader = SkillLoader(tmp_path)

    with pytest.raises(SkillError):
        loader.get_instructions("_pending")
    with pytest.raises(SkillError):
        loader.get_instructions("draft")


def test_draft_skill_writes_to_pending_only_and_does_not_activate(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)

    loader.draft_skill("New Skill", "does a thing", "step one\nstep two")

    assert loader.discover() == []
    assert "new_skill" in loader.list_pending()
    assert not (tmp_path / "new_skill").exists()


def test_draft_skill_rejects_unsafe_name(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    with pytest.raises(SkillError):
        loader.draft_skill("../escape", "d", "i")


def test_approve_moves_pending_skill_into_live_skills(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    loader.draft_skill("helper", "helps", "instructions")

    loader.approve("helper")

    assert [s.name for s in loader.discover()] == ["helper"]
    assert "helper" not in loader.list_pending()


def test_approve_unknown_pending_skill_raises(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    with pytest.raises(SkillError):
        loader.approve("nope")


def test_approve_overwrites_existing_revision(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    make_skill(tmp_path, "helper", "old description", "old body")
    loader.draft_skill("helper", "new description", "new body")

    loader.approve("helper")

    summaries = loader.discover()
    assert summaries[0].description == "new description"
    assert loader.get_instructions("helper") == "new body"


def test_register_tools_loads_and_calls_register_function(tmp_path: Path) -> None:
    tools_py = (
        "from figai.agent.tools import Tool\n\n"
        "def register(registry):\n"
        "    registry.register(Tool(name='skill_tool', description='d', "
        "parameters={'type': 'object', 'properties': {}}, handler=lambda a: 'ok'))\n"
    )
    make_skill(tmp_path, "ssh", "SSH access", "body", tools_py=tools_py)
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()

    loader.register_all_tools(registry)

    assert "skill_tool" in registry
    assert registry.execute("skill_tool", "{}") == "ok"


def test_register_tools_no_tools_py_is_a_noop(tmp_path: Path) -> None:
    make_skill(tmp_path, "ssh", "SSH access", "body")
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()

    loader.register_all_tools(registry)  # must not raise

    assert registry.to_openai_tools() == []


def test_skill_management_tools_round_trip(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()
    register_skill_management_tools(registry, loader)

    draft_result = json.loads(
        registry.execute(
            "draft_skill",
            json.dumps({"name": "note-taking", "description": "takes notes", "instructions": "do X"}),
        )
    )
    assert draft_result["status"] == "drafted"
    assert "note-taking" not in [s.name for s in loader.discover()]

    loader.approve("note-taking")
    loaded = registry.execute("load_skill_instructions", json.dumps({"skill_name": "note-taking"}))
    assert loaded == "do X"


def test_load_skill_instructions_unknown_skill_returns_error_not_raise(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()
    register_skill_management_tools(registry, loader)

    result = registry.execute("load_skill_instructions", json.dumps({"skill_name": "does-not-exist"}))

    assert "error" in json.loads(result)
