"""
LSPTool 单元测试
"""

import pytest
from pathlib import Path
from codo.tools.lsp_tool import LSPTool, LSPToolInput
from codo.tools.base import ToolUseContext
from codo.types.permissions import PermissionAllowDecision, create_allow_decision

@pytest.fixture
def lsp_tool():
    """创建 LSPTool 实例"""
    return LSPTool()

@pytest.fixture
def context(tmp_path):
    """创建测试上下文"""
    return ToolUseContext(
        options={"cwd": str(tmp_path)},
        abort_controller=None,
        messages=[],
    )

@pytest.fixture
def test_python_file(tmp_path):
    """创建测试 Python 文件"""
    file_path = tmp_path / "test.py"
    content = """
def hello_world():
    \"\"\"Say hello\"\"\"
    return "Hello, World!"

def main():
    result = hello_world()
    print(result)

if __name__ == "__main__":
    main()
"""
    file_path.write_text(content)
    return file_path

class TestLSPToolValidation:
    """测试输入验证"""

    @pytest.mark.asyncio
    async def test_validate_file_not_exists(self, lsp_tool, context):
        """测试文件不存在"""
        input_data = LSPToolInput(
            operation="goToDefinition",
            file_path="/nonexistent/file.py",
            line=1,
            character=1,
        )

        result = await lsp_tool.validate_input(input_data, context)
        assert not result.result
        assert "does not exist" in result.message

    @pytest.mark.asyncio
    async def test_validate_file_too_large(self, lsp_tool, context, tmp_path):
        """测试文件过大"""
        # 创建一个超过 10MB 的文件
        large_file = tmp_path / "large.py"
        large_file.write_text("x" * (11 * 1024 * 1024))

        input_data = LSPToolInput(
            operation="goToDefinition",
            file_path=str(large_file),
            line=1,
            character=1,
        )

        result = await lsp_tool.validate_input(input_data, context)
        assert not result.result
        assert "too large" in result.message

    @pytest.mark.asyncio
    async def test_validate_invalid_position(self, lsp_tool, context, test_python_file):
        """测试无效位置 - Pydantic 应该在模型层面拒绝"""
        with pytest.raises(Exception):  # Pydantic ValidationError
            input_data = LSPToolInput(
                operation="goToDefinition",
                file_path=str(test_python_file),
                line=0,  # 无效：必须 >= 1
                character=1,
            )

    @pytest.mark.asyncio
    async def test_validate_workspace_symbol_without_query(
        self, lsp_tool, context, test_python_file
    ):
        """测试 workspaceSymbol 缺少 query"""
        input_data = LSPToolInput(
            operation="workspaceSymbol",
            file_path=str(test_python_file),
            line=1,
            character=1,
            # query 缺失
        )

        result = await lsp_tool.validate_input(input_data, context)
        assert not result.result
        assert "requires 'query'" in result.message

    @pytest.mark.asyncio
    async def test_validate_valid_input(self, lsp_tool, context, test_python_file):
        """测试有效输入"""
        input_data = LSPToolInput(
            operation="goToDefinition",
            file_path=str(test_python_file),
            line=1,
            character=1,
        )

        result = await lsp_tool.validate_input(input_data, context)
        assert result.result

class TestLSPToolPermissions:
    """测试权限检查"""

    @pytest.mark.asyncio
    async def test_check_permissions(self, lsp_tool, context, test_python_file):
        """测试权限检查（LSP 是只读操作，应该总是允许）"""
        input_data = LSPToolInput(
            operation="goToDefinition",
            file_path=str(test_python_file),
            line=1,
            character=1,
        )

        result = await lsp_tool.check_permissions(input_data, context)
        assert isinstance(result, PermissionAllowDecision)
        assert result == create_allow_decision()

class TestLSPToolProperties:
    """测试工具属性"""

    def test_is_concurrency_safe(self, lsp_tool):
        """测试并发安全性"""
        assert lsp_tool.is_concurrency_safe()

    def test_is_read_only(self, lsp_tool):
        """测试只读性"""
        assert lsp_tool.is_read_only()

class TestSymbolExtraction:
    """测试符号提取"""

    def test_extract_symbol_at_position(self):
        """测试符号提取"""
        from codo.tools.lsp_tool.symbol_context import extract_symbol_at_position

        content = "def hello_world():\n    pass"

        # 提取函数名
        symbol = extract_symbol_at_position(content, 1, 5)
        assert symbol == "hello_world"

        # 提取 def 关键字
        symbol = extract_symbol_at_position(content, 1, 1)
        assert symbol == "def"

        # 位于函数名后的空白/分隔位置时，会回退到最近符号
        symbol = extract_symbol_at_position(content, 1, 15)
        assert symbol == "hello_world"

    def test_extract_symbol_invalid_position(self):
        """测试无效位置"""
        from codo.tools.lsp_tool.symbol_context import extract_symbol_at_position

        content = "def hello():\n    pass"

        # 行号超出范围
        symbol = extract_symbol_at_position(content, 100, 1)
        assert symbol is None

        # 字符位置超出范围
        symbol = extract_symbol_at_position(content, 1, 1000)
        assert symbol is None

class TestFormatters:
    """测试结果格式化"""

    def test_format_definition_result_empty(self):
        """测试空定义结果"""
        from codo.tools.lsp_tool.formatters import format_definition_result

        result, count, files = format_definition_result(None, "/tmp")
        assert "No definition found" in result
        assert count == 0
        assert files == 0

    def test_format_references_result_empty(self):
        """测试空引用结果"""
        from codo.tools.lsp_tool.formatters import format_references_result

        result, count, files = format_references_result(None, "/tmp")
        assert "No references found" in result
        assert count == 0
        assert files == 0

    def test_format_hover_result_empty(self):
        """测试空悬停结果"""
        from codo.tools.lsp_tool.formatters import format_hover_result

        result, count, files = format_hover_result(None, "/tmp")
        assert "No hover information" in result
        assert count == 0
        assert files == 0

@pytest.mark.asyncio
async def test_lsp_tool_cleanup(lsp_tool):
    """测试清理资源"""
    await lsp_tool.cleanup()
    assert lsp_tool._manager is None

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
