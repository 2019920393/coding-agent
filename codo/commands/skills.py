"""Skill slash command 集成。"""

from __future__ import annotations

from typing import Any, Dict, List

from codo.tools.skill_tool import SkillDefinition, skill_tool

from .base import Command, CommandType
from . import BUILTIN_COMMANDS

def _reserved_command_names() -> set[str]:
    reserved: set[str] = set()
    for command in BUILTIN_COMMANDS:
        reserved.add(command.name)
        reserved.update(command.aliases)
    return reserved

def _skill_description(skill: SkillDefinition) -> str:
    return skill.description or f"执行 skill：/{skill.name}"

def _skill_argument_hint(skill: SkillDefinition) -> str:
    return "[args]"

def _make_prompt_loader(skill_name: str):
    async def _get_prompt(args: str, context: Dict[str, Any]) -> str:
        return skill_tool.render_skill_prompt(skill_name, args)

    return _get_prompt

def build_skill_commands(cwd: str) -> List[Command]:
    skill_tool.load_all_skills(cwd)
    reserved = _reserved_command_names()
    commands: List[Command] = []
    for skill in skill_tool.list_skills(user_invocable_only=True):
        if skill.name in reserved:
            continue
        commands.append(
            Command(
                name=skill.name,
                description=_skill_description(skill),
                type=CommandType.PROMPT,
                argument_hint=_skill_argument_hint(skill),
                source="skills",
                get_prompt=_make_prompt_loader(skill.name),
            )
        )
    return commands

def list_skill_summaries(cwd: str) -> List[SkillDefinition]:
    skill_tool.load_all_skills(cwd)
    return skill_tool.list_skills(user_invocable_only=True)
