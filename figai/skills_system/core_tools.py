"""Core, always-available tools for using and extending the skills system itself."""

from __future__ import annotations

import json
from typing import Any

from figai.agent.tools import Tool, ToolRegistry
from figai.skills_system.loader import SkillError, SkillLoader


def register_skill_management_tools(registry: ToolRegistry, loader: SkillLoader) -> None:
    def _load_instructions(arguments: dict[str, Any]) -> str:
        try:
            return loader.get_instructions(arguments["skill_name"])
        except SkillError as exc:
            return json.dumps({"error": str(exc)})

    registry.register(
        Tool(
            name="load_skill_instructions",
            description=(
                "Load the full instructions for one of Fig's available skills, by name, "
                "when it is relevant to the current request."
            ),
            parameters={
                "type": "object",
                "properties": {"skill_name": {"type": "string"}},
                "required": ["skill_name"],
            },
            handler=_load_instructions,
        )
    )

    def _draft_skill(arguments: dict[str, Any]) -> str:
        try:
            path = loader.draft_skill(
                name=arguments["name"],
                description=arguments["description"],
                instructions=arguments["instructions"],
            )
        except SkillError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(
            {
                "status": "drafted",
                "path": str(path),
                "note": "Saved to _pending only. It will not run until the user approves it with !approve.",
            }
        )

    registry.register(
        Tool(
            name="draft_skill",
            description=(
                "Draft a new skill, or a revision of an existing one, when the user asks Fig to learn "
                "a reusable procedure. Always writes to a pending area requiring the user's explicit "
                "approval (!approve <name>) before it takes effect -- it never enables itself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["name", "description", "instructions"],
            },
            handler=_draft_skill,
        )
    )
