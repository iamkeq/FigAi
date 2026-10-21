"""Discovers skills under `skills/`, keeps `_pending/` drafts inert until approved,
and lazily exposes each approved skill's full instructions and tools."""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from figai.logging_setup import get_logger

logger = get_logger("skills")

PENDING_DIR_NAME = "_pending"
SKILL_FILENAME = "SKILL.md"
TOOLS_FILENAME = "tools.py"


class SkillError(Exception):
    """Raised for invalid skill names/content. Never crashes the agent loop --
    callers turn this into a tool-result error string instead."""


@dataclass
class SkillSummary:
    name: str
    description: str


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Splits a SKILL.md into its YAML frontmatter (name, description) and body."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            metadata = yaml.safe_load(parts[1]) or {}
            return metadata, parts[2].strip("\n")
    raise SkillError("SKILL.md must start with a '---' YAML frontmatter block (name, description)")


class SkillLoader:
    def __init__(self, skills_root: Path) -> None:
        self.skills_root = skills_root
        self.pending_root = skills_root / PENDING_DIR_NAME
        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.pending_root.mkdir(parents=True, exist_ok=True)

    def _approved_skill_dirs(self) -> list[Path]:
        return sorted(
            p
            for p in self.skills_root.iterdir()
            if p.is_dir() and p.name != PENDING_DIR_NAME and (p / SKILL_FILENAME).exists()
        )

    def discover(self) -> list[SkillSummary]:
        summaries: list[SkillSummary] = []
        for skill_dir in self._approved_skill_dirs():
            try:
                metadata, _ = parse_skill_md((skill_dir / SKILL_FILENAME).read_text(encoding="utf-8"))
                summaries.append(SkillSummary(name=metadata["name"], description=metadata["description"]))
            except (SkillError, KeyError) as exc:
                logger.error("Skipping invalid skill at %s: %s", skill_dir, exc)
        return summaries

    def get_summary_text(self) -> str:
        summaries = self.discover()
        if not summaries:
            return ""
        return "\n".join(f"- {s.name}: {s.description}" for s in summaries)

    def get_instructions(self, name: str) -> str:
        if name == PENDING_DIR_NAME:
            raise SkillError(f"No approved skill named '{name}'")
        skill_file = self.skills_root / name / SKILL_FILENAME
        if not skill_file.exists():
            raise SkillError(f"No approved skill named '{name}'")
        _, body = parse_skill_md(skill_file.read_text(encoding="utf-8"))
        return body

    def register_tools(self, name: str, registry: Any) -> None:
        tools_file = self.skills_root / name / TOOLS_FILENAME
        if not tools_file.exists():
            return
        spec = importlib.util.spec_from_file_location(f"figai_skill_{name}_tools", tools_file)
        if spec is None or spec.loader is None:
            raise SkillError(f"Could not load tools.py for skill '{name}'")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "register"):
            module.register(registry)

    def register_all_tools(self, registry: Any) -> None:
        for skill_dir in self._approved_skill_dirs():
            self.register_tools(skill_dir.name, registry)

    def draft_skill(self, name: str, description: str, instructions: str) -> Path:
        """Writes a drafted/revised skill into `_pending/` only. It never touches
        the live `skills/` directory and never runs until `approve()` is called."""
        safe_name = _validate_skill_name(name)
        skill_dir = self.pending_root / safe_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        content = f"---\nname: {safe_name}\ndescription: {description}\n---\n\n{instructions}\n"
        (skill_dir / SKILL_FILENAME).write_text(content, encoding="utf-8")
        logger.info("Drafted pending skill '%s'", safe_name)
        return skill_dir

    def list_pending(self) -> list[str]:
        return sorted(
            p.name for p in self.pending_root.iterdir() if p.is_dir() and (p / SKILL_FILENAME).exists()
        )

    def approve(self, name: str) -> None:
        pending_dir = self.pending_root / name
        if not (pending_dir / SKILL_FILENAME).exists():
            raise SkillError(f"No pending skill named '{name}'")
        target_dir = self.skills_root / name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(pending_dir), str(target_dir))
        logger.info("Approved skill '%s'", name)


def _validate_skill_name(name: str) -> str:
    safe = name.strip().lower().replace(" ", "_")
    if not safe or safe == PENDING_DIR_NAME or not all(c.isalnum() or c in "_-" for c in safe):
        raise SkillError(f"Invalid skill name: {name!r}")
    return safe
