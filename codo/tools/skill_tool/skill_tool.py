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
from typing import Any

from codo.types.permissions import PermissionAskDecision, create_ask_decision

from ..base import Tool
from ..types import ToolResult, ValidationResult
from .constants import (
    ERROR_CODE_PARSE_ERROR,
    ERROR_CODE_SKILL_NOT_FOUND,
    SKILL_FILE_NAME,
    SKILL_TOOL_NAME,
)
from .prompt import DESCRIPTION, PROMPT
from .types import SkillDefinition, SkillInput, SkillOutputForked, SkillOutputInline

logger = logging.getLogger(__name__)# 给当前文件创建一个专属、可追踪、全局唯一的日志对象。

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

class SkillTool(Tool[SkillInput, SkillOutputInline | SkillOutputForked, None]):
    """在主对话中执行 skill。"""

    def __init__(self):
        """初始化 SkillTool，设置工具名称、最大结果大小和内部 skill 注册表。"""
        self.name = SKILL_TOOL_NAME
        self.max_result_size_chars = 100_000
        self._skills: dict[str, Any] = {}
        self._skill_definitions: dict[str, SkillDefinition] = {}
        self._file_skill_names: set[str] = set()  # 记录从文件加载的 skill，可用于清除重新加载

    @property
    def input_schema(self) -> type[SkillInput]:
        """返回输入 schema 类 SkillInput。"""
        return SkillInput

    @property
    def output_schema(self) -> type[SkillOutputInline | SkillOutputForked]:
        """返回输出 schema 类（以 SkillOutputInline 为代表）。"""
        return SkillOutputInline

    async def description(self, input_data: SkillInput, options: dict[str, Any]) -> str:
        """返回工具简短描述。"""
        return DESCRIPTION

    async def prompt(self, options: dict[str, Any]) -> str:
        """
        生成系统提示词中的工具描述，动态列出所有用户可调用的 skill。

        [Workflow]
        1. 获取所有 user_invocable=True 的 skill 列表
        2. 若无 skill，返回基础描述 + "(none loaded)"
        3. 否则拼接 skill 名称和描述列表（最多 100 个）

        返回:
            str: 包含可用 skill 列表的提示词文本
        """
        loaded = self.list_skills(user_invocable_only=True)  # 返回 SkillDefinition 列表
        if not loaded:
            return f"{PROMPT}\n\nAvailable skills: (none loaded)"

        lines = [PROMPT, "", "Available skills:"]
        for skill in loaded[:100]:
            suffix = f" - {skill.description}" if skill.description else ""
            lines.append(f"- /{skill.name}{suffix}")
        if len(loaded) > 100:
            lines.append(f"- ... and {len(loaded) - 100} more")
        return "\n".join(lines) # 返回的是skill 名字加描述的字符串

    def is_read_only(self) -> bool:
        """Skill 执行可能触发写操作（通过子代理），返回 False。"""
        return False

    def is_concurrency_safe(self) -> bool:
        """Skill 执行是并发安全的，返回 True。"""
        return True

    @staticmethod
    def _normalize_skill_name(name: str) -> str:
        """
        规范化 skill 名称：去除首尾空格和前导斜杠。

        参数:
            name: 原始 skill 名称，如 "/my-skill" 或 "my-skill"

        返回:
            str: 规范化后的名称，如 "my-skill"
        """
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
        allowed_tools: list[str] | None = None,
        model: str | None = None,
        user_invocable: bool = True,
    ) -> None:
        """
        注册一个编程式 skill（通过代码注册，而非 Markdown 文件）。

        注意：编程 skill 目前是空壳能力，handler 在 call() 时执行。
        注册时 prompt 为空字符串，因为要等 call() 执行 handler 才能拿到实际 prompt。

        参数:
            name: skill 名称，如 "my-skill"
            handler: 可调用对象或字符串（字符串时直接作为 prompt）
            description: skill 描述
            allowed_tools: 推荐使用的工具列表
            model: 推荐使用的模型
            user_invocable: 是否可由用户直接调用（斜杠命令）
        """
        normalized = self._normalize_skill_name(name)
        self._skills[normalized] = handler
        self._skill_definitions[normalized] = SkillDefinition(
            name=normalized, 
            prompt="",   #注册时根本不知道 prompt 是什么，要等 call() 执行 handler 才能拿到。
            description=description.strip(),
            allowed_tools=list(allowed_tools or []),
            model=model,
            user_invocable=user_invocable,
            source_path="",
        )

    def has_skill(self, name: str) -> bool:
        """判断指定名称的 skill 是否已注册。"""
        return self._normalize_skill_name(name) in self._skills

    def get_skill_definition(self, name: str) -> SkillDefinition | None:
        """
        获取指定名称的 skill 定义对象。

        参数:
            name: skill 名称

        返回:
            SkillDefinition | None: skill 定义，未找到时返回 None
        """
        return self._skill_definitions.get(self._normalize_skill_name(name))

    def list_skills(self, *, user_invocable_only: bool = False) -> list[SkillDefinition]:
        """
        列出所有已注册的 skill，按名称字母序排列。

        参数:
            user_invocable_only: True 时只返回 user_invocable=True 的 skill

        返回:
            List[SkillDefinition]: skill 定义列表，如：
                [SkillDefinition(name="code-review", description="代码审查", ...)]
        """
        skills = sorted(self._skill_definitions.values(), key=lambda item: item.name.lower())
        if user_invocable_only:
            return [skill for skill in skills if skill.user_invocable]
        return skills

    def render_skill_prompt(self, name: str, args: str | None = None) -> str:
        """
        渲染 skill 的完整提示词（含命令名、参数、系统提醒和 skill 正文）。

        [Workflow]
        1. 获取 skill 定义，未找到则抛出 KeyError
        2. 拼接 command-name 标签
        3. 若有 args，追加 command-args 标签
        4. 追加 system-reminder（skill 已加载提示）
        5. 若有 allowed_tools，追加推荐工具提示
        6. 若有 model，追加推荐模型提示
        7. 追加 skill prompt 正文

        参数:
            name: skill 名称
            args: 用户传入的参数字符串（可选）

        返回:
            str: 完整的 skill 提示词文本
        """
        definition = self.get_skill_definition(name)
        if definition is None:
            raise KeyError(name)

        lines = [
#            f"<command-message>{definition.name}</command-message>",
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
        context: dict[str, Any],
    ) -> ValidationResult:
        """
        验证 skill 名称非空且已注册。

        返回:
            ValidationResult: 通过时 result=True，失败时附带错误码和可用 skill 列表
        """
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
        context: dict[str, Any],
    ) -> PermissionAskDecision:
        """
        检查权限：skill 执行需要用户确认。

        返回:
            PermissionAskDecision: 询问用户是否执行该 skill
        """
        skill = self._normalize_skill_name(args.skill)
        return create_ask_decision(
            message=f"Execute skill '{skill}'?",
            updated_input=args.model_dump(),
        )

    async def call(
        self,
        args: SkillInput,
        context: dict[str, Any],
        can_use_tool,
        parent_message,
        on_progress=None,
    ) -> ToolResult[SkillOutputInline | SkillOutputForked]:
        """
        执行 skill。

        [Workflow]
        1. 查找 skill handler 和 definition
        2. 若 handler 是字符串（Markdown skill），直接返回 SkillOutputInline（含渲染后的 prompt）
        3. 若 handler 是可调用对象，执行并从返回值中提取 prompt
        4. 返回 ToolResult（含 SkillOutputInline）

        返回:
            ToolResult[SkillOutputInline]: 包含 skill 名称、prompt、推荐工具和模型等信息
        """
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
        content: SkillOutputInline | SkillOutputForked,
        tool_use_id: str,
    ) -> dict[str, Any]:
        """
        将 skill 执行结果转换为 API tool_result 消息块格式。

        [Workflow]
        1. SkillOutputForked：返回代理 ID 和执行结果摘要
        2. SkillOutputInline：拼接 skill 名称、描述、推荐工具、推荐模型和 prompt 正文

        返回:
            dict: API 格式的 tool_result 块
        """
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
        """
        解析 Markdown frontmatter 中单个字段的值。

        [Workflow]
        1. 去除首尾空格和引号
        2. "true"/"false" 转换为 bool
        3. allowedTools/allowed_tools 字段：解析为字符串列表（支持 JSON 数组和逗号分隔）
        4. 其他字段：返回字符串

        参数:
            key: 字段名，如 "allowedTools"、"model"
            raw_value: 原始字符串值，如 "[Bash, Read]" 或 "claude-opus-4"

        返回:
            Any: 解析后的值，如 ["Bash", "Read"] 或 "claude-opus-4" 或 True
        """
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

    def _parse_markdown_skill_file(self, path: Path) -> SkillDefinition | None:
        """
        解析单个 Markdown skill 文件，返回 SkillDefinition 对象。

        [Workflow]
        1. 读取文件内容
        2. 若以 "---" 开头，提取 YAML frontmatter（优先用 yaml 库，降级用手动解析）
        3. 从 frontmatter 提取 name、description、allowedTools、model、userInvocable
        4. 文件名作为 name 的兜底（子目录中的 SKILL.md 用目录名）
        5. 返回 SkillDefinition，若 name 或 prompt 为空则返回 None

        参数:
            path: skill 文件路径

        返回:
            SkillDefinition | None: 解析成功时返回定义对象，失败时返回 None
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("加载技能文件失败 %s: %s", path, exc)
            return None

        frontmatter: dict[str, Any] = {}
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
#找到目录里所有能作为 skill 的 md 文件，去重，排好序返回。 支持"直接扔文件"和"建子目录放 SKILL.md"两种组织方式。
    @staticmethod
    def _iter_markdown_skill_files(skills_dir: Path) -> list[Path]:
        """
        遍历 skills 目录，收集所有 Markdown skill 文件路径（去重、排序）。

        支持两种组织方式：
        1. 直接在目录下放 *.md 文件
        2. 建子目录并在其中放 SKILL.md 文件

        参数:
            skills_dir: skills 目录路径

        返回:
            List[Path]: 去重后按路径字母序排列的 skill 文件列表
        """
        candidates: dict[str, Path] = {}
        for path in skills_dir.glob("*.md"):
            if path.is_file():
                candidates[str(path.resolve())] = path
        for path in skills_dir.rglob(SKILL_FILE_NAME):
            if path.is_file():
                candidates[str(path.resolve())] = path
        return sorted(candidates.values(), key=lambda item: str(item).lower())

    def _clear_file_skills(self) -> None:
        """清除所有从文件加载的 skill（保留编程式注册的 skill），用于重新加载前的清理。"""
        for name in list(self._file_skill_names):
            self._skills.pop(name, None)
            self._skill_definitions.pop(name, None)
        self._file_skill_names.clear()

    def load_skills_from_dir(self, skills_dir: str) -> int:
        """
        从指定目录加载所有 Markdown skill 文件。

        [Workflow]
        1. 检查目录是否存在
        2. 遍历所有 skill 文件（通过 _iter_markdown_skill_files）
        3. 解析每个文件为 SkillDefinition
        4. 注册到 _skills 和 _skill_definitions，并记录到 _file_skill_names

        参数:
            skills_dir: skills 目录路径字符串

        返回:
            int: 成功加载的 skill 数量
        """
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
        """
        清除旧的文件 skill 并从所有约定目录重新加载。

        [Workflow]
        1. 调用 _clear_file_skills() 清除旧的文件 skill
        2. 构建搜索路径列表：~/.codo/skills/ 和 <cwd>/.codo/skills/
        3. 对每个路径（去重）调用 load_skills_from_dir()
        4. 返回总加载数量

        参数:
            cwd: 当前工作目录，用于查找项目级 skill

        返回:
            int: 成功加载的 skill 总数量
        """
        self._clear_file_skills()
        home = os.path.expanduser("~")  # 把 ~ 替换为当前用户的主目录绝对路径
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
