from __future__ import annotations

"""
工具执行回执（Receipt）系统。

回执是工具执行完成后生成的结构化摘要，用于：
1. UI 层展示工具执行结果（侧边栏卡片、工具调用摘要）
2. 日志记录和审计
3. 模型可读的工具结果格式化

[回执类型]
- CommandReceipt: Shell 命令执行结果（exit_code、stdout、stderr）
- DiffReceipt: 文件变更 diff（path、diff_text）
- GenericReceipt: 通用文本结果
- AgentReceipt: 子代理执行结果（agent_id、status、result_preview）

[审计与变更追踪]
- ProposedFileChange: 待审阅的文件变更（用于 diff review 交互）
- AuditLogEvent: 审计日志事件（记录工具执行的关键操作）
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional, Union

@dataclass
class CommandReceipt:
    """
    Shell 命令执行回执。

    示例:
        CommandReceipt(
            kind="command",
            summary="已执行 git status",
            command="git status",
            exit_code=0,
            stdout="On branch main\nnothing to commit",
            stderr="",
        )
    """
    kind: Literal["command"]
    summary: str    # 用户可读摘要，如 "已执行 git status"
    command: str    # 实际执行的命令
    exit_code: int  # 退出码，0 表示成功
    stdout: str = ""  # 标准输出
    stderr: str = ""  # 标准错误

@dataclass
class DiffReceipt:
    """
    文件变更 diff 回执。

    示例:
        DiffReceipt(
            kind="diff",
            summary="已修改 src/main.py",
            path="src/main.py",
            diff_text="@@ -1,3 +1,4 @@\n+import os\n ...",
            change_id="chg_a1b2c3",
        )
    """
    kind: Literal["diff"]
    summary: str      # 用户可读摘要，如 "已修改 src/main.py"
    path: str         # 被修改的文件路径
    diff_text: str    # unified diff 格式的变更内容
    change_id: Optional[str] = None  # 变更 ID（用于 diff review 交互）

@dataclass
class GenericReceipt:
    """
    通用文本回执，用于不适合其他类型的工具结果。

    示例:
        GenericReceipt(
            kind="generic",
            summary="已读取 README.md",
            body="# 项目说明\n...",
        )
    """
    kind: Literal["generic"]
    summary: str  # 用户可读摘要
    body: str = ""  # 详细内容

@dataclass
class AgentReceipt:
    """
    子代理执行回执。

    示例:
        AgentReceipt(
            kind="agent",
            summary="子代理已完成任务",
            agent_id="agent_a1b2c3",
            agent_type="worker",
            mode="fork",
            task_id="task_001",
            background=False,
            status="completed",
            result_preview="已实现登录功能，共修改 3 个文件",
            total_tokens=2048,
        )
    """
    kind: Literal["agent"]
    summary: str          # 用户可读摘要
    agent_id: str = ""    # 子代理唯一 ID
    agent_type: str = ""  # 代理类型，如 "worker"、"explorer"
    mode: str = ""        # 执行模式，如 "fork"、"inline"
    task_id: Optional[str] = None  # 关联的任务 ID
    background: bool = False       # 是否后台执行
    status: str = "completed"      # 执行状态
    result_preview: str = ""       # 结果预览文本
    total_tokens: int = 0          # 消耗的 token 总数

@dataclass
class ProposedFileChange:
    """
    待审阅的文件变更（用于 diff review 交互）。

    当工具（Edit/Write）需要用户确认变更时，创建此对象并通过
    UIBridge.request_change_review() 发起交互请求。

    示例:
        ProposedFileChange(
            change_id="chg_a1b2c3",
            path="src/auth.py",
            original_content="def login():\n    pass",
            new_content="def login(username, password):\n    ...",
            diff_text="@@ -1 +1,2 @@\n-def login():\n+def login(username, password):\n ...",
            source_tool="Edit",
        )
    """
    change_id: str           # 变更唯一 ID
    path: str                # 文件路径
    original_content: str    # 原始文件内容
    new_content: str         # 变更后的文件内容
    diff_text: str           # unified diff 格式的变更
    source_tool: str         # 发起变更的工具名称，如 "Edit"
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外元数据

@dataclass
class AuditLogEvent:
    """
    审计日志事件，记录工具执行的关键操作。

    示例:
        AuditLogEvent(
            event_id="evt_001",
            agent_id="main",
            source="Bash",
            message="执行命令: rm -rf /tmp/test",
            created_at=1700000000.0,
            metadata={"exit_code": 0},
        )
    """
    event_id: str      # 事件唯一 ID
    agent_id: str      # 执行操作的代理 ID
    source: str        # 事件来源（工具名称）
    message: str       # 事件描述
    created_at: float  # 事件时间戳（Unix 秒）
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外元数据

ToolReceipt = Union[CommandReceipt, DiffReceipt, GenericReceipt, AgentReceipt]
"""工具回执联合类型，是四种回执类型的 Union。"""

def receipt_to_dict(receipt: ToolReceipt) -> dict[str, Any]:
    """
    将回执对象转换为字典，用于 JSON 序列化和 UI 传递。

    参数:
        receipt: 任意类型的回执对象

    返回:
        dict: 回执的字典表示，如：
            {
                "kind": "command",
                "summary": "已执行 git status",
                "command": "git status",
                "exit_code": 0,
                "stdout": "On branch main",
                "stderr": "",
            }
    """
    return asdict(receipt)

def render_receipt_for_model(receipt: ToolReceipt, tool_use_id: str) -> dict[str, Any]:
    """
    将回执格式化为模型可读的 tool_result 消息块。

    [Workflow]
    1. 根据 receipt.kind 选择格式化策略
    2. command: 拼接命令、退出码、stdout（stderr 用 XML 标签包裹）
    3. diff: 拼接摘要、路径、diff 文本
    4. agent: 拼接摘要、类型/状态、task_id、结果预览
    5. generic: 拼接摘要和 body
    6. 返回标准 tool_result 块格式

    参数:
        receipt: 工具回执对象
        tool_use_id: 对应的工具调用 ID

    返回:
        dict: 标准 tool_result 消息块，如：
            {
                "type": "tool_result",
                "tool_use_id": "toolu_abc123",
                "content": "已执行 git status\n$ git status\nexit_code=0\nOn branch main",
            }
    """
    if receipt.kind == "command":
        content = (
            f"{receipt.summary}\n"
            f"$ {receipt.command}\n"
            f"exit_code={receipt.exit_code}\n"
            f"{receipt.stdout}".strip()
        )
        if receipt.stderr:
            content = f"{content}\n<stderr>\n{receipt.stderr}\n</stderr>"
    elif receipt.kind == "diff":
        content = f"{receipt.summary}\n{receipt.path}\n{receipt.diff_text}"
    elif receipt.kind == "agent":
        lines = [receipt.summary]
        if receipt.agent_type or receipt.status:
            lines.append(
                " · ".join(
                    value
                    for value in [receipt.agent_type, receipt.mode, receipt.status]
                    if value
                )
            )
        if receipt.task_id:
            lines.append(f"task_id={receipt.task_id}")
        if receipt.result_preview:
            lines.append(receipt.result_preview)
        content = "\n".join(line for line in lines if line).strip()
    else:
        # generic
        content = f"{receipt.summary}\n{receipt.body}".strip()

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
