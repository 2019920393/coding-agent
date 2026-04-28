"""
SkillTool 实现

[Workflow]
1. 提供 skill 注册、发现、执行接口
2. 支持从多种约定目录加载 Markdown skill
3. 统一把 skill 定义暴露给 runtime / slash command / model tool schema
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult
from codo.types.permissions import PermissionAskDecision, create_ask_decision
from .constants import (
    ERROR_CODE_PARSE_ERROR,
    ERROR_CODE_SKILL_NOT_FOUND,
    SKILL_FILE_NAME,
    SKILL_TOOL_NAME,
)
from .prompt import DESCRIPTION, PROMPT
from .types import SkillDefinition, SkillInput, SkillOutputForked, SkillOutputInline

logger = logging.getLogger(__name__)

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

class SkillTool(Tool[SkillInput, Union[SkillOutputInline, SkillOutputForked], None]):
    """在主对话中执行 skill。"""

    def __init__(self):
        self.name = SKILL_TOOL_NAME
        self.max_result_size_chars = 100_000
        self._skills: Dict[str, Any] = {}
        self._skill_definitions: Dict[str, SkillDefinition] = {}
        self._file_skill_names: set[str] = set()

    @property
    def input_schema(self) -> type[SkillInput]:
        return SkillInput

    @property
    def output_schema(self) -> type[Union[SkillOutputInline, SkillOutputForked]]:
        return SkillOutputInline

    async def description(self, input_data: SkillInput, options: Dict[str, Any]) -> str:
        return DESCRIPTION

    async def prompt(self, options: Dict[str, Any]) -> str:
        loaded = self.list_skills()
        if not loaded:
            return f"{PROMPT}\n\nAvailable skills: (none loaded)"

        lines = [PROMPT, "", "Available skills:"]
        for skill in loaded[:100]:
            suffix = f" - {skill.description}" if skill.description else ""
            lines.append(f"- /{skill.name}{suffix}")
        if len(loaded) > 100:
            lines.append(f"- ... and {len(loaded) - 100} more")
        return "\n".join(lines)

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return True

    @staticmethod
    def _normalize_skill_name(name: str) -> str:
        normalized = str(name or "").strip()
        if normalized.startswith("/"):
            normalized = normalized[1:]
        return normalized

    def register_skill(
        self,
        name: str,
        handler: Any,
        *,
        description: str = "",
        allowed_tools: Optional[List[str]] = None,
        model: Optional[str] = None,
        user_invocable: bool = True,
    ) -> None:
        normalized = self._normalize_skill_name(name)
        self._skills[normalized] = handler
        self._skill_definitions[normalized] = SkillDefinition(
            name=normalized,
            prompt="",
            description=description.strip(),
            allowed_tools=list(allowed_tools or []),
            model=model,
            user_invocable=user_invocable,
            source_path="",
        )

    def has_skill(self, name: str) -> bool:
        return self._normalize_skill_name(name) in self._skills

    def get_skill_definition(self, name: str) -> Optional[SkillDefinition]:
        return self._skill_definitions.get(self._normalize_skill_name(name))

    def list_skills(self, *, user_invocable_only: bool = False) -> List[SkillDefinition]:
        skills = sorted(self._skill_definitions.values(), key=lambda item: item.name.lower())
        if user_invocable_only:
            return [skill for skill in skills if skill.user_invocable]
        return skills

    def render_skill_prompt(self, name: str, args: Optional[str] = None) -> str:
        definition = self.get_skill_definition(name)
        if definition is None:
            raise KeyError(name)

        lines = [
            f"<command-message>{definition.name}</command-message>",
            f"<command-name>/{definition.name}</command-name>",
        ]
        if args and args.strip():
            lines.append(f"<command-args>{args.strip()}</command-args>")
        lines.append(
            f"<system-reminder>The /{definition.name} skill has been loaded for this turn. "
            "Follow the skill instructions below before continuing.</system-reminder>"
        )
        if definition.allowed_tools:
            lines.append(
                "<system-reminder>Preferred tools for this skill: "
                + ", ".join(definition.allowed_tools)
                + "</system-reminder>"
            )
        if definition.model:
            lines.append(
                f"<system-reminder>Preferred model for this skill: {definition.model}</system-reminder>"
            )
        lines.append(definition.prompt.strip())
        return "\n".join(line for line in lines if line).strip()

    async def validate_input(
        self,
        args: SkillInput,
        context: ToolUseContext,
    ) -> ValidationResult:
        skill = self._normalize_skill_name(args.skill)
        if not skill:
            return ValidationResult(
                result=False,
                message="Skill name cannot be empty",
                error_code=ERROR_CODE_PARSE_ERROR,
            )
        if skill not in self._skills:
            available = ", ".join(self._skills.keys()) if self._skills else "(none)"
            return ValidationResult(
                result=False,
                message=f"Skill '{skill}' not found. Available skills: {available}",
                error_code=ERROR_CODE_SKILL_NOT_FOUND,
            )
        return ValidationResult(result=True)

    async def check_permissions(
        self,
        args: SkillInput,
        context: ToolUseContext,
    ) -> PermissionAskDecision:
        skill = self._normalize_skill_name(args.skill)
        return create_ask_decision(
            message=f"Execute skill '{skill}'?",
            updated_input=args.model_dump(),
        )

    async def call(
        self,
        args: SkillInput,
        context: ToolUseContext,
        can_use_tool,
        parent_message,
        on_progress=None,
    ) -> ToolResult[Union[SkillOutputInline, SkillOutputForked]]:
        skill = self._normalize_skill_name(args.skill)
        handler = self._skills.get(skill)
        definition = self._skill_definitions.get(skill)
        if not handler or definition is None:
            return ToolResult(error=f"Skill '{skill}' not found")

        if isinstance(handler, str):
            return ToolResult(
                data=SkillOutputInline(
                    success=True,
                    commandName=skill,
                    allowedTools=definition.allowed_tools or None,
                    model=definition.model,
                    prompt=self.render_skill_prompt(skill, args.args),
                    description=definition.description or None,
                    sourcePath=definition.source_path or None,
                    status="inline",
                )
            )

        try:
            result = await handler(args.args, context)
            payload = result if isinstance(result, dict) else {}
            prompt_text = payload.get("prompt")
            if isinstance(prompt_text, str) and prompt_text.strip():
                if "<command-name>" not in prompt_text:
                    prompt_text = self.render_skill_prompt(skill, args.args)
            elif definition.prompt:
                prompt_text = self.render_skill_prompt(skill, args.args)

            return ToolResult(
                data=SkillOutputInline(
                    success=True,
                    commandName=skill,
                    allowedTools=payload.get("allowedTools") or definition.allowed_tools or None,
                    model=payload.get("model") or definition.model,
                    prompt=prompt_text,
                    description=payload.get("description") or definition.description or None,
                    sourcePath=definition.source_path or None,
                    status="inline",
                )
            )
        except Exception as exc:
            return ToolResult(error=f"Skill execution failed: {str(exc)}")

    def map_tool_result_to_tool_result_block_param(
        self,
        content: Union[SkillOutputInline, SkillOutputForked],
        tool_use_id: str,
    ) -> Dict[str, Any]:
        if isinstance(content, SkillOutputForked):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": (
                    f"Skill '{content.commandName}' executed in forked mode.\n\n"
                    f"Agent ID: {content.agentId}\n\n"
                    f"Result:\n{content.result}"
                ),
            }

        lines = [f"Loaded skill: {content.commandName}"]
        if content.description:
            lines.append(content.description)
        if content.allowedTools:
            lines.append(f"Preferred tools: {', '.join(content.allowedTools)}")
        if content.model:
            lines.append(f"Preferred model: {content.model}")
        if content.prompt:
            lines.append(content.prompt)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": "\n\n".join(line for line in lines if line).strip(),
        }

    @staticmethod
    def _parse_frontmatter_value(key: str, raw_value: str) -> Any:
        value = raw_value.strip().strip('"').strip("'")
        lower = value.lower()
        if lower in {"true", "false"}:
            return lower == "true"
        if key in {"allowedTools", "allowed_tools"}:
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if not inner:
                    return []
                return [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
            if "," in value:
                return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def _parse_markdown_skill_file(self, path: Path) -> Optional[SkillDefinition]:
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("加载技能文件失败 %s: %s", path, exc)
            return None

        frontmatter: Dict[str, Any] = {}
        body = raw
        if raw.startswith("---"):
            end_idx = raw.find("---", 3)
            if end_idx != -1:
                fm_text = raw[3:end_idx].strip()
                body = raw[end_idx + 3 :].strip()
                if yaml is not None:
                    try:
                        parsed = yaml.safe_load(fm_text) or {}
                        if isinstance(parsed, dict):
                            frontmatter = dict(parsed)
                    except Exception:
                        frontmatter = {}
                if not frontmatter:
                    for line in fm_text.splitlines():
                        if ":" not in line:
                            continue
                        key, _, value = line.partition(":")
                        frontmatter[key.strip()] = self._parse_frontmatter_value(key.strip(), value)

        fallback_name = path.parent.name if path.name.upper() == SKILL_FILE_NAME.upper() else path.stem
        skill_name = self._normalize_skill_name(frontmatter.get("name", fallback_name))
        prompt_body = body.strip()
        if not skill_name or not prompt_body:
            return None

        description = str(frontmatter.get("description", "") or "").strip()
        allowed_tools = frontmatter.get("allowedTools") or frontmatter.get("allowed_tools") or []
        if isinstance(allowed_tools, str):
            allowed_tools = [item.strip() for item in allowed_tools.split(",") if item.strip()]
        elif not isinstance(allowed_tools, list):
            allowed_tools = []
        model = frontmatter.get("model")
        user_invocable = frontmatter.get("userInvocable", frontmatter.get("user_invocable", True))
        if not isinstance(user_invocable, bool):
            user_invocable = str(user_invocable).strip().lower() not in {"false", "0", "no"}

        return SkillDefinition(
            name=skill_name,
            prompt=prompt_body,
            description=description,
            allowed_tools=[str(item).strip() for item in allowed_tools if str(item).strip()],
            model=str(model).strip() if model else None,
            user_invocable=user_invocable,
            source_path=str(path),
        )

    @staticmethod
    def _iter_markdown_skill_files(skills_dir: Path) -> List[Path]:
        candidates: Dict[str, Path] = {}
        for path in skills_dir.glob("*.md"):
            if path.is_file():
                candidates[str(path.resolve())] = path
        for path in skills_dir.rglob(SKILL_FILE_NAME):
            if path.is_file():
                candidates[str(path.resolve())] = path
        return sorted(candidates.values(), key=lambda item: str(item).lower())

    def _clear_file_skills(self) -> None:
        for name in list(self._file_skill_names):
            self._skills.pop(name, None)
            self._skill_definitions.pop(name, None)
        self._file_skill_names.clear()

    def load_skills_from_dir(self, skills_dir: str) -> int:
        skills_path = Path(skills_dir)
        if not skills_path.exists() or not skills_path.is_dir():
            return 0

        loaded = 0
        for skill_file in self._iter_markdown_skill_files(skills_path):
            definition = self._parse_markdown_skill_file(skill_file)
            if definition is None:
                continue
            self._skills[definition.name] = definition.prompt
            self._skill_definitions[definition.name] = definition
            self._file_skill_names.add(definition.name)
            loaded += 1
        return loaded

    def load_all_skills(self, cwd: str) -> int:
        self._clear_file_skills()
        home = os.path.expanduser("~")
        roots = [
            os.path.join(home, ".codo", "skills"),
            os.path.join(home, ".codex", "skills"),
            os.path.join(cwd, ".codo", "skills"),
            os.path.join(cwd, ".codex", "skills"),
        ]

        loaded = 0
        seen: set[str] = set()
        for root in roots:
            normalized = str(Path(root).resolve())
            if normalized in seen:
                continue
            seen.add(normalized)
            loaded += self.load_skills_from_dir(root)
        return loaded

skill_tool = SkillTool()
