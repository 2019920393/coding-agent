"""Skill slash command 集成。"""

from __future__ import annotations

from typing import Any

from codo.tools.skill_tool import SkillDefinition, skill_tool

from . import BUILTIN_COMMANDS
from .base import Command, CommandType


def _reserved_command_names() -> set[str]:
    """
    返回所有内置命令的名称和别名集合，用于防止 skill 命令与内置命令冲突。

    返回:
        set[str]: 保留名称集合，如 {"help", "clear", "sessions", "h", ...}
    """
    reserved: set[str] = set()
    for command in BUILTIN_COMMANDS:
        reserved.add(command.name)
        reserved.update(command.aliases)
    return reserved

def _skill_description(skill: SkillDefinition) -> str:
    """
    返回 skill 的命令描述文本，无描述时生成默认文本。

    参数:
        skill: skill 定义对象

    返回:
        str: 描述文本，如 "代码审查" 或 "执行 skill：/my-skill"
    """
    return skill.description or f"执行 skill：/{skill.name}"

def _skill_argument_hint(skill: SkillDefinition) -> str:
    """
    返回 skill 命令的参数提示文本（固定为 "[args]"）。

    参数:
        skill: skill 定义对象

    返回:
        str: 参数提示，如 "[args]"
    """
    return "[args]"

def _make_prompt_loader(skill_name: str):
    """
    创建指定 skill 的 prompt 加载器（闭包）。

    返回的 _get_prompt 函数在命令执行时被调用，
    通过 skill_tool.render_skill_prompt 生成完整的 skill 提示词。

    参数:
        skill_name: skill 名称，如 "code-review"

    返回:
        async callable: 接受 (args, context) 参数，返回渲染后的 prompt 字符串
    """
    async def _get_prompt(args: str, context: dict[str, Any]) -> str:
        """渲染 skill 提示词并返回。"""
        return skill_tool.render_skill_prompt(skill_name, args)

    return _get_prompt

def build_skill_commands(cwd: str) -> list[Command]:
    """
    从当前工作区加载所有 skill 并构建对应的斜杠命令列表。

    [Workflow]
    1. 调用 skill_tool.load_all_skills(cwd) 扫描并加载 skill 文件
    2. 获取保留命令名称集合（防止冲突）
    3. 遍历所有 user_invocable=True 的 skill
    4. 跳过与内置命令同名的 skill
    5. 为每个 skill 创建 CommandType.PROMPT 类型的 Command

    参数:
        cwd: 当前工作目录路径

    返回:
        List[Command]: skill 命令列表，如：
            [Command(name="code-review", type=CommandType.PROMPT, ...)]
    """
    skill_tool.load_all_skills(cwd)
    reserved = _reserved_command_names()
    commands: list[Command] = []
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

def list_skill_summaries(cwd: str) -> list[SkillDefinition]:
    """
    加载并返回当前工作区所有用户可调用的 skill 定义列表。

    参数:
        cwd: 当前工作目录路径

    返回:
        List[SkillDefinition]: skill 定义列表，按名称字母序排列
    """
    skill_tool.load_all_skills(cwd)
    return skill_tool.list_skills(user_invocable_only=True)
