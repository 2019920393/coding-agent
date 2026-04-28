"""
工具系统基础测试

测试工具基类、类型定义和 build_tool 装饰器的功能。
"""

import pytest
from pydantic import BaseModel
from anthropic.types import ToolResultBlockParam

from codo.tools import (
    Tool,
    ToolResult,
    ToolUseContext,
    build_tool,
    ValidationResult,
)
from codo.types.permissions import PermissionResult, create_passthrough_result

# ============================================================================
# 测试用的 Schema 定义
# ============================================================================

class SampleInput(BaseModel):
    """测试工具输入"""
    message: str
    count: int = 1

class SampleOutput(BaseModel):
    """测试工具输出"""
    result: str

class SampleProgress(BaseModel):
    """测试工具进度"""
    current: int
    total: int

# ============================================================================
# 测试工具实现
# ============================================================================

@build_tool(
    name="TestTool",
    max_result_size_chars=1000,
    input_schema=SampleInput,
    output_schema=SampleOutput,
)
class TestTool(Tool[SampleInput, SampleOutput, SampleProgress]):
    """测试工具实现"""

    async def call(self, args, context, can_use_tool, parent_message, on_progress):
        """执行测试工具"""
        result = f"{args.message} x {args.count}"
        return ToolResult(data=SampleOutput(result=result))

    async def description(self, input, options):
        """工具描述"""
        return "A test tool"

    async def prompt(self, options):
        """系统提示"""
        return "This is a test tool for unit testing."

    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        """转换为 API 格式"""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content.result,
        }

@build_tool(
    name="ContextAwareTool",
    max_result_size_chars=1000,
    input_schema=SampleInput,
    output_schema=SampleOutput,
)
class ContextAwareTool(Tool[SampleInput, SampleOutput, SampleProgress]):
    """用于测试 execute() 上下文包装行为的工具。"""

    async def call(self, args, context, can_use_tool, parent_message, on_progress):
        assert isinstance(context, ToolUseContext)
        options = context.get_options()
        options["mutated"] = True
        context["session_id"] = "session-updated"
        return ToolResult(data=SampleOutput(result=args.message))

    async def description(self, input, options):
        return "Context aware"

    async def prompt(self, options):
        return "Context aware prompt"

    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content.result,
        }

# ============================================================================
# 测试：工具基本属性
# ============================================================================

def test_tool_basic_attributes():
    """测试工具基本属性设置"""
    tool = TestTool()

    assert tool.name == "TestTool"
    assert tool.max_result_size_chars == 1000
    assert tool.input_schema == SampleInput
    assert tool.output_schema == SampleOutput

def test_tool_default_attributes():
    """测试工具默认属性"""
    tool = TestTool()

    assert tool.aliases is None
    assert tool.search_hint is None
    assert tool.strict is False
    assert tool.should_defer is False
    assert tool.always_load is False
    assert tool.is_mcp is False
    assert tool.mcp_info is None

# ============================================================================
# 测试：工具默认方法
# ============================================================================

def test_tool_is_enabled():
    """测试 is_enabled 默认返回 True"""
    tool = TestTool()
    assert tool.is_enabled() is True

def test_tool_is_concurrency_safe():
    """测试 is_concurrency_safe 默认返回 False（fail-closed）"""
    tool = TestTool()
    input_data = SampleInput(message="test")
    assert tool.is_concurrency_safe(input_data) is False

def test_tool_is_read_only():
    """测试 is_read_only 默认返回 False（fail-closed）"""
    tool = TestTool()
    input_data = SampleInput(message="test")
    assert tool.is_read_only(input_data) is False

def test_tool_is_destructive():
    """测试 is_destructive 默认返回 False"""
    tool = TestTool()
    input_data = SampleInput(message="test")
    assert tool.is_destructive(input_data) is False

@pytest.mark.asyncio
async def test_tool_check_permissions():
    """测试 check_permissions 默认返回 passthrough"""
    tool = TestTool()
    input_data = SampleInput(message="test")
    context = ToolUseContext(options={}, abort_controller=None, messages=[])

    result = await tool.check_permissions(input_data, context)

    assert isinstance(result, PermissionResult)
    assert result == create_passthrough_result()

@pytest.mark.asyncio
async def test_tool_validate_input():
    """测试 validate_input 默认返回通过"""
    tool = TestTool()
    input_data = SampleInput(message="test")
    context = ToolUseContext(options={}, abort_controller=None, messages=[])

    result = await tool.validate_input(input_data, context)

    assert isinstance(result, ValidationResult)
    assert result.result is True
    assert result.message is None
    assert result.error_code is None

def test_tool_to_auto_classifier_input():
    """测试 to_auto_classifier_input 默认返回空字符串"""
    tool = TestTool()
    input_data = SampleInput(message="test")
    assert tool.to_auto_classifier_input(input_data) == ""

def test_tool_user_facing_name():
    """测试 user_facing_name 默认返回工具名称"""
    tool = TestTool()
    assert tool.user_facing_name() == "TestTool"
    assert tool.user_facing_name(SampleInput(message="test")) == "TestTool"

def test_tool_get_tool_use_summary():
    """测试 get_tool_use_summary 默认返回 None"""
    tool = TestTool()
    assert tool.get_tool_use_summary() is None

def test_tool_get_activity_description():
    """测试 get_activity_description 默认返回 None"""
    tool = TestTool()
    assert tool.get_activity_description() is None

def test_tool_get_path():
    """测试 get_path 默认返回 None"""
    tool = TestTool()
    input_data = SampleInput(message="test")
    assert tool.get_path(input_data) is None

@pytest.mark.asyncio
async def test_tool_prepare_permission_matcher():
    """测试 prepare_permission_matcher 默认返回不匹配任何模式的匹配器"""
    tool = TestTool()
    input_data = SampleInput(message="test")

    matcher = await tool.prepare_permission_matcher(input_data)

    assert callable(matcher)
    assert matcher("any_pattern") is False

# ============================================================================
# 测试：工具核心方法
# ============================================================================

@pytest.mark.asyncio
async def test_tool_call():
    """测试工具 call 方法"""
    tool = TestTool()
    input_data = SampleInput(message="hello", count=3)
    context = ToolUseContext(options={}, abort_controller=None, messages=[])

    result = await tool.call(input_data, context, None, None, None)

    assert isinstance(result, ToolResult)
    assert isinstance(result.data, SampleOutput)
    assert result.data.result == "hello x 3"
    assert result.new_messages is None
    assert result.context_modifier is None
    assert result.mcp_meta is None

@pytest.mark.asyncio
async def test_tool_execute_coerces_dict_context_and_writes_through():
    """execute() 应统一把字典包装成 ToolUseContext，并把变更同步回原始上下文。"""
    tool = ContextAwareTool()
    raw_context = {
        "cwd": "/tmp/project",
        "session_id": "session-original",
        "options": {"model": "claude-test"},
    }

    result = await tool.execute({"message": "hello"}, raw_context)

    assert isinstance(result, ToolResult)
    assert raw_context["options"]["mutated"] is True
    assert raw_context["session_id"] == "session-updated"

@pytest.mark.asyncio
async def test_tool_description():
    """测试工具 description 方法"""
    tool = TestTool()
    input_data = SampleInput(message="test")

    description = await tool.description(input_data, {})

    assert description == "A test tool"

@pytest.mark.asyncio
async def test_tool_prompt():
    """测试工具 prompt 方法"""
    tool = TestTool()

    prompt = await tool.prompt({})

    assert prompt == "This is a test tool for unit testing."

def test_tool_map_result():
    """测试工具结果转换为 API 格式"""
    tool = TestTool()
    output = SampleOutput(result="test result")

    result = tool.map_tool_result_to_tool_result_block_param(output, "tool_use_123")

    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "tool_use_123"
    assert result["content"] == "test result"

# ============================================================================
# 测试：build_tool 装饰器覆盖默认方法
# ============================================================================

@build_tool(
    name="CustomTool",
    max_result_size_chars=5000,
    input_schema=SampleInput,
    output_schema=SampleOutput,
    aliases=["custom", "test"],
    search_hint="custom tool for testing",
    strict=True,
    is_enabled=lambda: False,
    is_concurrency_safe=lambda input: True,
    is_read_only=lambda input: True,
    is_destructive=lambda input: True,
    to_auto_classifier_input=lambda input: input.message,
    user_facing_name=lambda input: "Custom Tool Name",
)
class CustomTool(Tool[SampleInput, SampleOutput, SampleProgress]):
    """自定义工具，覆盖默认方法"""

    async def call(self, args, context, can_use_tool, parent_message, on_progress):
        return ToolResult(data=SampleOutput(result="custom"))

    async def description(self, input, options):
        return "Custom tool"

    async def prompt(self, options):
        return "Custom prompt"

    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content.result}

def test_build_tool_with_overrides():
    """测试 build_tool 装饰器覆盖默认方法"""
    tool = CustomTool()

    # 测试基本属性
    assert tool.name == "CustomTool"
    assert tool.max_result_size_chars == 5000
    assert tool.aliases == ["custom", "test"]
    assert tool.search_hint == "custom tool for testing"
    assert tool.strict is True

    # 测试覆盖的方法
    assert tool.is_enabled() is False
    assert tool.is_concurrency_safe(SampleInput(message="test")) is True
    assert tool.is_read_only(SampleInput(message="test")) is True
    assert tool.is_destructive(SampleInput(message="test")) is True
    assert tool.to_auto_classifier_input(SampleInput(message="hello")) == "hello"
    assert tool.user_facing_name() == "Custom Tool Name"

# ============================================================================
# 测试：ToolResult
# ============================================================================

def test_tool_result_basic():
    """测试 ToolResult 基本功能"""
    output = SampleOutput(result="test")
    result = ToolResult(data=output)

    assert result.data == output
    assert result.new_messages is None
    assert result.context_modifier is None
    assert result.mcp_meta is None

def test_tool_result_with_metadata():
    """测试 ToolResult 带元数据"""
    output = SampleOutput(result="test")
    new_messages = [{"role": "user", "content": "test"}]
    mcp_meta = {"_meta": {"key": "value"}}

    result = ToolResult(
        data=output,
        new_messages=new_messages,
        mcp_meta=mcp_meta,
    )

    assert result.data == output
    assert result.new_messages == new_messages
    assert result.mcp_meta == mcp_meta

# ============================================================================
# 测试：ValidationResult
# ============================================================================

def test_validation_result_success():
    """测试验证成功"""
    result = ValidationResult(result=True)

    assert result.result is True
    assert result.message is None
    assert result.error_code is None

def test_validation_result_failure():
    """测试验证失败"""
    result = ValidationResult(
        result=False,
        message="Invalid input",
        error_code=400,
    )

    assert result.result is False
    assert result.message == "Invalid input"
    assert result.error_code == 400

# ============================================================================
# 测试：PermissionResult
# ============================================================================

def test_permission_result_allow():
    """测试权限允许"""
    result = PermissionResult(behavior="allow")

    assert result.behavior == "allow"
    assert result.updated_input is None
    assert result.message is None

def test_permission_result_deny():
    """测试权限拒绝"""
    result = PermissionResult(
        behavior="deny",
        message="Access denied",
    )

    assert result.behavior == "deny"
    assert result.message == "Access denied"

def test_permission_result_with_updated_input():
    """测试权限结果带更新的输入"""
    updated_input = {"message": "modified", "count": 2}
    result = PermissionResult(
        behavior="allow",
        updated_input=updated_input,
    )

    assert result.behavior == "allow"
    assert result.updated_input == updated_input
