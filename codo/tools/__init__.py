"""
工具系统模块

[Workflow]
本模块是工具系统的入口点，导出核心类型和函数供其他模块使用。

主要导出：
- Tool: 工具基类
- ToolResult: 工具结果类型
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
from .agent_tool import AgentTool, agent_tool
from .ask_user_question_tool import AskUserQuestionTool, ask_user_question_tool

# 导出基类和装饰器
from .base import (
    Tool,
    Tools,
    ToolUseContext,
    build_tool,
)

# 导出内置工具
from .bash_tool import BashTool, bash_tool
from .edit_tool import EditTool, edit_tool
from .glob_tool import GlobTool, glob_tool
from .grep_tool import GrepTool, grep_tool
from .lsp_tool import LSPTool
from .notebook_edit_tool import NotebookEditTool, notebook_edit_tool
from .plan_mode_tools import (
    EnterPlanModeTool,
    ExitPlanModeTool,
    enter_plan_mode_tool,
    exit_plan_mode_tool,
)
from .read_tool import ReadTool, read_tool
from .skill_tool import SkillTool, skill_tool
from .todo_write_tool import TodoWriteTool, todo_write_tool
from .types import (
    # 泛型类型变量
    InputT,
    OutputT,
    ProgressT,
    ToolCallProgress,
    # 进度类型
    ToolProgress,
    # 结果类型
    ToolResult,
    ValidationResult,
)
from .web_fetch_tool import WebFetchTool, web_fetch_tool
from .write_tool import WriteTool, write_tool

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
    "ToolUseContext",
    "Tools",
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
    "ask_user_question_tool",
    "AskUserQuestionTool",
    "web_fetch_tool",
    "WebFetchTool",
    "lsp_tool",
    "LSPTool",
    "todo_write_tool",
    "TodoWriteTool",
    "enter_plan_mode_tool",
    "EnterPlanModeTool",
    "exit_plan_mode_tool",
    "ExitPlanModeTool",
    "skill_tool",
    "SkillTool",
    "notebook_edit_tool",
    "NotebookEditTool",
    "BUILTIN_TOOLS",
]
