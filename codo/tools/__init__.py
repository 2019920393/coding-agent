"""
工具系统模块

[Workflow]
本模块是工具系统的入口点，导出核心类型和函数供其他模块使用。

主要导出：
- Tool: 工具基类
- ToolResult: 工具结果类型
- ToolUseContext: 工具使用上下文
- build_tool: 工具构建装饰器
- 类型定义：ValidationResult 等

使用示例：
```python
from codo.tools import Tool, ToolResult, build_tool
from pydantic import BaseModel

class MyToolInput(BaseModel):
    arg: str

class MyToolOutput(BaseModel):
    result: str

@build_tool(
    name="MyTool",
    max_result_size_chars=10000,
    input_schema=MyToolInput,
    output_schema=MyToolOutput,
)
class MyTool(Tool[MyToolInput, MyToolOutput, None]):
    async def call(self, args, context, can_use_tool, parent_message, on_progress):
        return ToolResult(data=MyToolOutput(result=f"Processed: {args.arg}"))

    async def description(self, input, options):
        return "My custom tool"

    async def prompt(self, options):
        return "This tool does something useful."

    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content.result
        }
```
"""

# 导出核心类型
from .types import (
    # 泛型类型变量
    InputT,
    OutputT,
    ProgressT,
    # 结果类型
    ToolResult,
    ValidationResult,
    # 进度类型
    ToolProgress,
    ToolCallProgress,
)

# 导出基类和装饰器
from .base import (
    Tool,
    Tools,
    ToolUseContext,
    build_tool,
)

# 导出内置工具
from .bash_tool import bash_tool, BashTool
from .read_tool import read_tool, ReadTool
from .edit_tool import edit_tool, EditTool
from .write_tool import write_tool, WriteTool
from .glob_tool import glob_tool, GlobTool
from .grep_tool import grep_tool, GrepTool
from .agent_tool import agent_tool, AgentTool
from .lsp_tool import LSPTool
from .todo_write_tool import todo_write_tool, TodoWriteTool
from .web_fetch_tool import web_fetch_tool, WebFetchTool
from .ask_user_question_tool import ask_user_question_tool, AskUserQuestionTool
from .plan_mode_tools import enter_plan_mode_tool, EnterPlanModeTool, exit_plan_mode_tool, ExitPlanModeTool
from .skill_tool import skill_tool, SkillTool
from .notebook_edit_tool import notebook_edit_tool, NotebookEditTool

# 创建 LSPTool 实例
lsp_tool = LSPTool()

# 工具注册表
BUILTIN_TOOLS = [
    bash_tool,
    read_tool,
    edit_tool,
    write_tool,
    glob_tool,
    grep_tool,
    agent_tool,
    lsp_tool,
    todo_write_tool,
    web_fetch_tool,
    ask_user_question_tool,
    enter_plan_mode_tool,
    exit_plan_mode_tool,
    skill_tool,
    notebook_edit_tool,
]

__all__ = [
    # 泛型类型变量
    "InputT",
    "OutputT",
    "ProgressT",
    # 结果类型
    "ToolResult",
    "ValidationResult",
    # 进度类型
    "ToolProgress",
    "ToolCallProgress",
    # 基类和装饰器
    "Tool",
    "Tools",
    "ToolUseContext",
    "build_tool",
    # 内置工具
    "bash_tool",
    "BashTool",
    "read_tool",
    "ReadTool",
    "edit_tool",
    "EditTool",
    "write_tool",
    "WriteTool",
    "glob_tool",
    "GlobTool",
    "grep_tool",
    "GrepTool",
    "agent_tool",
    "AgentTool",
    "web_fetch_tool",
    "WebFetchTool",
    "lsp_tool",
    "LSPTool",
    "todo_write_tool",
    "TodoWriteTool",
    "notebook_edit_tool",
    "NotebookEditTool",
    "BUILTIN_TOOLS",
]
