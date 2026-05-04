"""交互式对话组件（Textual-only 入口）。

本模块提供两个异步函数，供工具层（权限检查器、AskUserQuestion 工具）
在需要 UI 交互时调用。所有交互都必须通过活动中的 Textual App 完成，
不支持纯终端模式。
"""

from typing import Any, Dict, List, Optional

from codo.cli.tui.runtime import get_active_app

async def prompt_permission_dialog(
    tool_name: str,
    tool_info: str,
    message: str = "",
) -> Optional[str]:
    """
    弹出权限确认对话框，等待用户选择。

    [Workflow]
    1. 获取当前活动的 Textual App
    2. 调用 app.request_permission() 发起交互请求
    3. 阻塞等待用户选择，返回选择结果

    参数:
        tool_name: 请求权限的工具名称，如 "Bash"
        tool_info: 工具操作详情，如 "rm -rf /tmp/test"
        message: 附加说明信息（可选）

    返回:
        str | None: 用户选择结果，如：
            "allow_once"    - 本次允许
            "allow_always"  - 本会话始终允许
            "deny"          - 拒绝
            "abort"         - 中止整个任务
            None            - 用户取消或 App 不可用

    异常:
        RuntimeError: 当前没有活动的 Textual App 时抛出
    """
    app = get_active_app()
    if app is not None and hasattr(app, "request_permission"):
        return await app.request_permission(
            tool_name=tool_name,
            tool_info=tool_info,
            message=message,
        )

    raise RuntimeError("Textual app is required for permission dialogs")

async def collect_user_answers_dialog(questions: List[Any]) -> Optional[Dict[str, str]]:
    """
    弹出多问题交互对话框，等待用户逐题回答。

    [Workflow]
    1. 获取当前活动的 Textual App
    2. 调用 app.request_questions() 发起多问题交互
    3. 阻塞等待用户完成所有问题，返回答案字典

    参数:
        questions: 问题列表，每个问题结构如：
            {
                "header": "选择操作",
                "question": "你想怎么处理这个文件？",
                "options": [
                    {"label": "覆盖", "description": "直接覆盖原文件"},
                    {"label": "备份", "description": "先备份再覆盖"},
                ],
                "multi_select": False,
            }

    返回:
        Dict[str, str] | None: 问题到答案的映射，如：
            {"你想怎么处理这个文件？": "覆盖"}
            None 表示用户取消

    异常:
        RuntimeError: 当前没有活动的 Textual App 时抛出
    """
    app = get_active_app()
    if app is not None and hasattr(app, "request_questions"):
        return await app.request_questions(questions)

    raise RuntimeError("Textual app is required for question dialogs")
