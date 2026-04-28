"""SkillTool 单元测试"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from codo.tools.skill_tool import (
    SkillTool,
    SkillInput,
    SkillOutputInline,
)
from codo.tools.base import ToolUseContext

@pytest.fixture
def tool():
    """创建 SkillTool 实例"""
    return SkillTool()

@pytest.fixture
def context():
    """创建测试上下文"""
    return ToolUseContext(
        options={"cwd": "/test"},
        abort_controller=None,
        messages=[]
    )

class TestSkillTool:
    """SkillTool 测试套件"""

    @pytest.mark.asyncio
    async def test_validate_empty_skill(self, tool, context):
        """测试空技能名验证"""
        input_data = SkillInput(skill="")
        result = await tool.validate_input(input_data, context)
        assert result.result is False
        assert "cannot be empty" in result.message

    @pytest.mark.asyncio
    async def test_validate_skill_not_found(self, tool, context):
        """测试技能不存在验证"""
        input_data = SkillInput(skill="nonexistent")
        result = await tool.validate_input(input_data, context)
        assert result.result is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_validate_with_slash(self, tool, context):
        """测试带斜杠的技能名"""
        # 注册一个测试技能
        async def test_handler(args, ctx):
            return {"allowedTools": ["Bash"]}

        tool.register_skill("test", test_handler)

        input_data = SkillInput(skill="/test")
        result = await tool.validate_input(input_data, context)
        assert result.result is True

    @pytest.mark.asyncio
    async def test_call_skill(self, tool, context):
        """测试调用技能"""
        # 注册一个测试技能
        async def test_handler(args, ctx):
            return {"allowedTools": ["Bash"], "model": "opus"}

        tool.register_skill("test", test_handler)

        input_data = SkillInput(skill="test", args="arg1 arg2")
        result = await tool.call(input_data, context, None, None, None)

        assert result.data is not None
        assert result.data.success is True
        assert result.data.commandName == "test"
        assert result.data.allowedTools == ["Bash"]
        assert result.data.model == "opus"
        assert result.data.prompt is None

    @pytest.mark.asyncio
    async def test_call_skill_with_slash(self, tool, context):
        """测试调用带斜杠的技能"""
        async def test_handler(args, ctx):
            return {}

        tool.register_skill("commit", test_handler)

        input_data = SkillInput(skill="/commit", args="-m 'test'")
        result = await tool.call(input_data, context, None, None, None)

        assert result.data is not None
        assert result.data.commandName == "commit"

    @pytest.mark.asyncio
    async def test_call_nonexistent_skill(self, tool, context):
        """测试调用不存在的技能"""
        input_data = SkillInput(skill="nonexistent")
        result = await tool.call(input_data, context, None, None, None)

        assert result.error is not None
        assert "not found" in result.error

    def test_tool_properties(self, tool):
        """测试工具属性"""
        assert tool.name == "Skill"
        assert tool.is_read_only() is False
        assert tool.is_concurrency_safe() is True

    def test_map_tool_result_inline(self, tool):
        """测试工具结果映射（inline）"""
        output = SkillOutputInline(
            success=True,
            commandName="test",
            prompt="Follow the skill",
            status="inline"
        )

        result = tool.map_tool_result_to_tool_result_block_param(output, "test_id")
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "test_id"
        assert "Loaded skill: test" in result["content"]
        assert "Follow the skill" in result["content"]

    def test_load_all_skills_supports_flat_and_nested_files(self, tool, tmp_path):
        """支持 flat.md 和 nested/SKILL.md 两种 skill 布局。"""
        flat_dir = tmp_path / ".codo" / "skills"
        flat_dir.mkdir(parents=True)
        (flat_dir / "commit.md").write_text(
            "---\nname: commit\ndescription: Commit helper\nallowedTools: [Bash, Read]\nmodel: claude-opus\n---\n\nDo commit work.\n",
            encoding="utf-8",
        )

        nested_dir = tmp_path / ".codex" / "skills" / "review"
        nested_dir.mkdir(parents=True)
        (nested_dir / "SKILL.md").write_text(
            "---\ndescription: Review helper\nuserInvocable: true\n---\n\nReview the patch carefully.\n",
            encoding="utf-8",
        )

        loaded = tool.load_all_skills(str(tmp_path))
        assert loaded >= 2

        skills = {item.name: item for item in tool.list_skills(user_invocable_only=True)}
        assert "commit" in skills
        assert "review" in skills
        assert skills["commit"].allowed_tools == ["Bash", "Read"]
        assert skills["commit"].model == "claude-opus"
        assert skills["review"].source_path.endswith(str(Path("review") / "SKILL.md"))

    def test_load_all_skills_ignores_legacy_claude_directory(self, tool, tmp_path):
        """.claude/skills 不应再作为默认技能扫描目录。"""
        legacy_dir = tmp_path / ".claude" / "skills"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "legacy.md").write_text(
            "---\nname: legacy\ndescription: Legacy helper\n---\n\nLegacy skill.\n",
            encoding="utf-8",
        )

        real_expanduser = os.path.expanduser
        with patch("os.path.expanduser", side_effect=lambda value: str(tmp_path) if value == "~" else real_expanduser(value)):
            loaded = tool.load_all_skills(str(tmp_path))

        assert loaded == 0
        assert not tool.has_skill("legacy")

    @pytest.mark.asyncio
    async def test_prompt_skill_returns_expanded_prompt(self, tool, context, tmp_path):
        """Markdown skill 执行后应把展开后的 prompt 返回给模型链路。"""
        skills_dir = tmp_path / ".codo" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "review.md").write_text(
            "---\ndescription: Review helper\nallowedTools: [Read, Grep]\n---\n\nInspect files before commenting.\n",
            encoding="utf-8",
        )
        tool.load_all_skills(str(tmp_path))

        result = await tool.call(SkillInput(skill="/review", args="src/app.py"), context, None, None, None)
        assert result.data is not None
        assert result.data.prompt is not None
        assert "<command-name>/review</command-name>" in result.data.prompt
        assert "src/app.py" in result.data.prompt
        assert result.data.allowedTools == ["Read", "Grep"]
