"""UIBridge - 把核心引擎事件聚合为 Textual 可消费快照。"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from codo.team import TaskStatus, get_task_manager, get_team_manager
from codo.tools.receipts import ProposedFileChange
from codo.types.permissions import PermissionMode, PermissionRuleSource
from codo.utils.config import get_global_config, save_global_config

from .interaction_types import InteractionOption, InteractionQuestion, InteractionRequest

def _now() -> float:
    """返回当前 Unix 时间戳（秒），用于记录快照的创建/更新时间。"""
    return time.time()

def _truncate(text: str, limit: int = 280) -> str:
    """
    截断文本到指定长度，超出部分用 "..." 替代。

    参数:
        text: 原始文本
        limit: 最大字符数，默认 280

    返回:
        str: 截断后的文本，如 "这是一段很长的文字..." 或原文（未超出时）
    """
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."

def _collapse_inline_text(text: str) -> str:
    """
    将多行 Markdown 文本折叠为单行纯文本摘要。

    [Workflow]
    1. 去除回车符，按换行分割
    2. 对每行去除 Markdown 标记（列表符、标题符、引用符等）
    3. 去除反引号，合并为单行
    4. 压缩多余空白

    参数:
        text: 原始 Markdown 文本

    返回:
        str: 单行纯文本，如 "实现登录功能 添加 JWT 验证"
    """
    raw = str(text or "").replace("\r", "")
    if not raw.strip():
        return ""
    lines: list[str] = []
    for piece in raw.split("\n"):
        cleaned = re.sub(r"^\s*(?:[-*+#>]+|\d+[.)])\s*", "", piece.strip())
        cleaned = cleaned.replace("`", "").strip()
        if cleaned:
            lines.append(cleaned)
    value = " ".join(lines) if lines else raw
    return re.sub(r"\s+", " ", value).strip()

_REQUEST_ID_RE = re.compile(r"request id:\s*([a-z0-9-]+)", re.IGNORECASE)

def _humanize_runtime_error(error_message: str, error_type: str = "") -> str:
    """
    将原始 API 错误信息转换为用户友好的中文提示。

    [Workflow]
    1. 规范化错误文本（去多余空白）
    2. 识别 user_interrupted 类型，返回固定中断提示
    3. 识别限流错误（429/rate limit），返回限流提示
    4. 识别消息格式错误，返回历史记录损坏提示
    5. 其他错误直接返回原始文本

    参数:
        error_message: 原始错误信息字符串
        error_type: 错误类型标识，如 "user_interrupted"、"runtime_error"

    返回:
        str: 用户友好的中文错误提示，如：
            "请求过于频繁，模型暂时在限流。\n请稍等片刻后重试..."
            "已中断当前生成。"
    """
    raw = " ".join(str(error_message or "").split())
    lower = raw.lower()
    request_id_match = _REQUEST_ID_RE.search(raw)
    request_id = request_id_match.group(1) if request_id_match else ""

    if error_type == "user_interrupted":
        return "已中断当前生成。"

    if any(token in lower for token in ("429", "too many requests", "rate limit", "exceeded retry limit")):
        lines = [
            "请求过于频繁，模型暂时在限流。",
            "请稍等片刻后重试；如果反复出现，可以稍后继续或切换模型。",
        ]
        if request_id:
            lines.append(f"请求 ID: {request_id}")
        return "\n".join(lines)

    if (
        "message format" in lower
        or ("messages" in lower and "content" in lower and any(token in lower for token in ("invalid", "unexpected", "must be")))
    ):
        lines = [
            "历史消息里有一条格式异常，当前轮次没有完整送进模型。",
            "损坏的历史记录会被尽量跳过；如仍反复出现，可以切换到上一条正常会话继续。",
        ]
        if request_id:
            lines.append(f"请求 ID: {request_id}")
        return "\n".join(lines)

    return raw or "发生了未知错误。"

def _permission_mode_display_label(mode_or_label: Any) -> str:
    """
    将权限模式枚举值或字符串转换为用户可读的中文标签。

    参数:
        mode_or_label: PermissionMode 枚举值或字符串，如 "bypass"、"default"

    返回:
        str: 中文标签，如 "询问" 或 "直通"
    """
    normalized = str(mode_or_label or "").strip().lower()
    mapping = {
        PermissionMode.DEFAULT.value: "询问",
        PermissionMode.BYPASS_PERMISSIONS.value: "直通",
        "ask": "询问",
        "default": "询问",
        "bypass": "直通",
        "bypasspermissions": "直通",
    }
    return mapping.get(normalized, str(mode_or_label or "").strip() or "询问")

def _friendly_agent_label(agent_id: str, label: str = "", agent_type: str = "") -> str:
    """
    生成协作代理的用户友好显示名称。

    优先级：agent_type 映射 > label 主段 > agent_id 后缀 > 默认值

    参数:
        agent_id: 代理唯一 ID，如 "agent_a1b2c3d4"
        label: 代理标签，如 "探索代理 > 文件分析"
        agent_type: 代理类型，如 "worker"、"explorer"

    返回:
        str: 友好名称，如 "执行代理"、"探索代理"、"协作代理 c3d4"
    """
    kind = _collapse_inline_text(agent_type)
    if kind:
        mapped_kind = {
            "default": "协作代理",
            "worker": "执行代理",
            "explorer": "探索代理",
        }.get(kind.lower())
        if mapped_kind:
            return mapped_kind
        return _truncate(kind, 24)
    source = _collapse_inline_text(label)
    if source and source != agent_id:
        primary = re.split(r"\s*[>·|/]\s*", source, maxsplit=1)[0].strip()
        if primary and primary != agent_id:
            return _truncate(primary, 24)
        return _truncate(source, 24)
    normalized_agent_id = str(agent_id or "").strip()
    if normalized_agent_id.startswith("agent_") and len(normalized_agent_id) > 6:
        return f"协作代理 {normalized_agent_id[-4:]}"
    return _truncate(normalized_agent_id or "协作代理", 24)

def _friendly_task_preview(*sources: str, limit: int = 96) -> str:
    """
    从多个候选文本中提取第一个非空的任务摘要。

    按参数顺序尝试每个 source，折叠为单行后截断到 limit 字符。

    参数:
        *sources: 候选文本，按优先级排列
        limit: 最大字符数，默认 96

    返回:
        str: 第一个非空的折叠摘要，如 "实现用户登录功能"，全部为空则返回 ""
    """
    for source in sources:
        value = _collapse_inline_text(source)
        if value:
            return _truncate(value, limit)
    return ""

def _extract_attr(obj: Any, name: str, default: Any = None) -> Any:
    """
    统一从字典或对象中提取属性值。

    参数:
        obj: 字典或任意对象
        name: 属性名
        default: 未找到时的默认值

    返回:
        属性值，或 default
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)

def _tool_key(name: str) -> str:
    """
    将工具名称规范化为小写无空格的 key，用于工具类型匹配。

    参数:
        name: 工具名称，如 "Read"、"Bash"

    返回:
        str: 规范化 key，如 "read"、"bash"
    """
    return str(name or "").strip().lower()

def _basename(path: str) -> str:
    """
    提取路径的文件名部分（兼容 Windows 反斜杠）。

    参数:
        path: 文件路径，如 "/home/user/project/main.py" 或 "C:\\Users\\test.py"

    返回:
        str: 文件名，如 "main.py"，路径为空时返回 ""
    """
    normalized = str(path or "").replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    base = os.path.basename(normalized)
    return base or normalized

def _parse_preview_payload(preview: str) -> dict[str, Any]:
    """
    尝试将工具输入预览字符串解析为字典。

    先尝试 JSON 解析，失败后尝试 ast.literal_eval，
    对不完整的 JSON（缺少右括号）也会尝试补全后解析。

    参数:
        preview: 工具输入的 JSON 字符串，如 '{"file_path": "/tmp/test.py"}'

    返回:
        dict: 解析成功时返回字典，如 {"file_path": "/tmp/test.py"}
              解析失败时返回空字典 {}
    """
    text = str(preview or "").strip()
    if not text:
        return {}
    candidates = [text]
    # 对缺少右括号的不完整 JSON 尝试补全
    if text.startswith("{") and text.count("{") > text.count("}"):
        candidates.append(text + ("}" * (text.count("{") - text.count("}"))))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}

def _extract_number(patterns: list[str], *sources: str) -> Optional[int]:
    """
    从多个文本来源中用正则提取第一个匹配的整数。

    参数:
        patterns: 正则模式列表，每个模式的第一个捕获组应为数字
        *sources: 待搜索的文本，按顺序尝试

    返回:
        int | None: 第一个匹配的整数，如 42，未找到则返回 None
    """
    for source in sources:
        text = str(source or "")
        if not text:
            continue
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except Exception:
                    continue
    return None

def _truncate_command(command: str, limit: int = 48) -> str:
    """
    截断命令字符串，压缩多余空白后限制长度。

    参数:
        command: 原始命令字符串，如 "git commit -m 'feat: add login'"
        limit: 最大字符数，默认 48

    返回:
        str: 截断后的命令，如 "git commit -m 'feat: add lo..."
    """
    value = " ".join(str(command or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."

def _live_tool_summary(tool_name: str, input_preview: str) -> Optional[str]:
    """
    生成工具执行中（running 状态）的实时摘要文本。

    根据工具类型和输入参数生成"正在做什么"的描述，
    用于 UI 侧边栏的实时状态展示。

    参数:
        tool_name: 工具名称，如 "Read"、"Bash"、"Glob"
        input_preview: 工具输入的 JSON 字符串，如 '{"file_path": "/tmp/test.py"}'

    返回:
        str | None: 实时摘要，如 "读取 test.py"、"执行 git status"，
                    无法生成时返回 None
    """
    payload = _parse_preview_payload(input_preview)
    key = _tool_key(tool_name)

    if key == "read":
        filename = _basename(str(payload.get("file_path", "") or payload.get("filePath", "")))
        if filename:
            partial = bool(payload.get("offset") is not None or payload.get("limit") is not None)
            return f"读取 {filename}{'（局部）' if partial else ''}"
        return "读取文件"

    if key == "glob":
        pattern = str(payload.get("pattern", "") or "").strip()
        search_path = str(payload.get("path", "") or "").strip()
        target = _basename(search_path) or "工作区"
        if pattern:
            return f"扫描 {target}，匹配 {pattern}"
        return f"扫描 {target}"

    if key == "grep":
        pattern = str(payload.get("pattern", "") or "").strip()
        search_path = str(payload.get("path", "") or "").strip()
        target = _basename(search_path) or "工作区"
        if pattern:
            return f"搜索 {target}，查找 {pattern}"
        return f"搜索 {target}"

    if key == "bash":
        description = str(payload.get("description", "") or "").strip()
        command = str(payload.get("command", "") or "").strip()
        snippet = _truncate_command(description or command)
        if snippet:
            return f"执行 {snippet}"
        return "执行命令"

    if str(input_preview or "").strip().startswith("{"):
        return f"准备 {tool_name}"
    return None

def _completed_tool_summary(
    tool_name: str,
    input_preview: str,
    result: str,
    receipt: Optional[dict[str, Any]],
) -> Optional[str]:
    """
    生成工具执行完成后的结果摘要文本。

    优先使用 receipt 中的 summary 字段，其次根据工具类型和结果生成摘要。

    参数:
        tool_name: 工具名称，如 "Read"、"Bash"
        input_preview: 工具输入的 JSON 字符串
        result: 工具执行结果文本
        receipt: 工具执行回执字典，结构如：
            {
                "kind": "command",       # 回执类型：command/diff/agent
                "summary": "已执行 git status",  # 摘要文本
                "body": "...",           # 详细内容
            }

    返回:
        str | None: 完成摘要，如 "已读取 main.py"、"找到 3 处匹配"，
                    无法生成时返回 None
    """
    key = _tool_key(tool_name)
    payload = _parse_preview_payload(input_preview)
    receipt_data = dict(receipt or {})
    receipt_kind = str(receipt_data.get("kind", "") or "")
    receipt_summary = str(receipt_data.get("summary", "") or "").strip()
    receipt_body = str(receipt_data.get("body", "") or "").strip()
    combined_sources = [receipt_summary, receipt_body, str(result or "")]

    # receipt 有明确摘要时优先使用
    if receipt_kind in {"command", "diff", "agent"} and receipt_summary:
        return receipt_summary

    if key == "read":
        filename = _basename(str(payload.get("file_path", "") or payload.get("filePath", "")))
        if filename:
            partial = bool(payload.get("offset") is not None or payload.get("limit") is not None)
            return f"已读取 {filename}{'（局部）' if partial else ''}"
        return "已读取文件"

    if key == "glob":
        num_files = _extract_number(
            [r"numFiles\s*[:=]\s*(\d+)", r"Found\s+(\d+)\s+files", r"Matched\s+(\d+)\s+files"],
            *combined_sources,
        )
        if num_files is not None:
            return f"匹配到 {num_files} 个文件"
        return "已匹配文件"

    if key == "grep":
        num_matches = _extract_number(
            [r"numMatches\s*[:=]\s*(\d+)", r"Found\s+(\d+)\s+matches"],
            *combined_sources,
        )
        if num_matches is not None:
            return f"找到 {num_matches} 处匹配"
        return "已找到匹配"

    if key == "bash":
        command = str(payload.get("command", "") or "").strip()
        if receipt_summary:
            return receipt_summary
        if command:
            return f"已执行 {_truncate_command(command)}"
        return "命令已完成"

    if key == "todowrite":
        return "已更新待办"

    if receipt_summary and not receipt_summary.startswith("{"):
        return _truncate(receipt_summary, 120)
    return None

def _interaction_request_from_payload(payload: Any) -> Optional[InteractionRequest]:
    """
    从任意 payload 构建 InteractionRequest 对象。

    [Workflow]
    1. 若 payload 已是 InteractionRequest，直接返回
    2. 若 payload 是字典，提取 questions 和 options 字段并构建对象
    3. 其他类型返回 None

    参数:
        payload: 原始 payload，可能是 InteractionRequest 对象或字典，如：
            {
                "request_id": "perm_001",
                "kind": "permission",
                "label": "等待权限：Bash",
                "tool_name": "Bash",
                "tool_info": "rm -rf /tmp",
                "options": [
                    {"value": "allow_once", "label": "本次允许"},
                    {"value": "deny", "label": "拒绝"},
                ],
            }

    返回:
        InteractionRequest | None: 构建的交互请求对象，或 None（payload 无效时）
    """
    if isinstance(payload, InteractionRequest):
        return payload
    if not isinstance(payload, dict):
        return None

    questions: list[InteractionQuestion] = []
    for raw_question in list(payload.get("questions", []) or []):
        if not isinstance(raw_question, dict):
            continue
        options = [
            InteractionOption(
                value=str(option.get("value", "") or ""),
                label=str(option.get("label", "") or ""),
                description=str(option.get("description", "") or ""),
                preview=str(option.get("preview", "") or ""),
            )
            for option in list(raw_question.get("options", []) or [])
            if isinstance(option, dict)
        ]
        questions.append(
            InteractionQuestion(
                question_id=str(raw_question.get("question_id", "") or ""),
                header=str(raw_question.get("header", "") or ""),
                question=str(raw_question.get("question", "") or ""),
                options=options,
                multi_select=bool(raw_question.get("multi_select", False)),
            )
        )

    options = [
        InteractionOption(
            value=str(option.get("value", "") or ""),
            label=str(option.get("label", "") or ""),
            description=str(option.get("description", "") or ""),
            preview=str(option.get("preview", "") or ""),
        )
        for option in list(payload.get("options", []) or [])
        if isinstance(option, dict)
    ]

    return InteractionRequest(
        request_id=str(payload.get("request_id", "") or ""),
        kind=str(payload.get("kind", "") or "question"),
        label=str(payload.get("label", "") or ""),
        tool_name=str(payload.get("tool_name", "") or ""),
        tool_info=str(payload.get("tool_info", "") or ""),
        message=str(payload.get("message", "") or ""),
        questions=questions,
        options=options,
        initial_value=payload.get("initial_value"),
        validation_rules=dict(payload.get("validation_rules", {}) or {}),
        payload=dict(payload.get("payload", {}) or {}),
    )

@dataclass
class ToastSnapshot:
    """
    单条 Toast 通知的快照。

    Toast 是短暂显示在屏幕角落的提示信息，有自动过期时间。

    示例:
        ToastSnapshot(
            id="toast_001",
            message="已压缩上下文：8000 -> 2000 令牌",
            level="info",
            expires_at=1700000003.0,
        )
    """
    id: str             # 唯一 ID，用于去重和删除
    message: str        # 显示文本
    level: str = "info" # 级别：info / warning / error
    expires_at: float = field(default_factory=lambda: _now() + 3.0)  # 过期时间戳（秒）

@dataclass
class HistoryHydrationReport:
    """
    历史消息恢复报告，记录从磁盘加载历史时的统计信息。

    示例:
        HistoryHydrationReport(raw_count=10, restored_count=9, skipped_count=1)
    """
    raw_count: int = 0       # 原始消息总数
    restored_count: int = 0  # 成功恢复的消息数
    skipped_count: int = 0   # 跳过（损坏/无效）的消息数

@dataclass
class ToolCallSnapshot:
    """
    单次工具调用的 UI 快照，追踪工具从启动到完成的完整状态。

    示例:
        ToolCallSnapshot(
            tool_use_id="toolu_abc123",
            name="Bash",
            status="completed",
            input_preview='{"command": "git status"}',
            result="On branch main\nnothing to commit",
            receipt={"kind": "command", "summary": "已执行 git status"},
            is_error=False,
        )
    """
    tool_use_id: str   # 工具调用唯一 ID
    name: str          # 工具名称，如 "Bash"、"Read"
    status: str = "starting"   # 状态：starting / running / completed / error
    input_preview: str = ""    # 工具输入的 JSON 字符串预览
    result: str = ""           # 工具执行结果文本
    receipt: Optional[dict[str, Any]] = None  # 结构化回执字典
    audit_events: list[dict[str, Any]] = field(default_factory=list)  # 审计事件列表
    is_error: bool = False     # 是否执行出错
    updated_at: float = field(default_factory=_now)  # 最后更新时间戳

    @property
    def summary(self) -> str:
        """
        生成工具调用的可读摘要文本，供侧边栏展示。

        优先级：执行中用实时摘要 → 完成后用结果摘要 → receipt.summary → 结果截断 → 输入截断 → 工具名
        """
        receipt_summary = str((self.receipt or {}).get("summary", "") or "").strip()
        if self.status in {"starting", "running"}:
            live_summary = _live_tool_summary(self.name, self.input_preview)
            if live_summary:
                return live_summary
        completed_summary = _completed_tool_summary(self.name, self.input_preview, self.result, self.receipt)
        if completed_summary:
            return completed_summary
        if receipt_summary:
            return receipt_summary
        live_result = _truncate(self.result, 120)
        if live_result:
            return live_result
        preview = _truncate(self.input_preview, 120)
        if preview:
            return preview
        return self.name

@dataclass
class TodoLineSnapshot:
    """单条 TODO 项的快照。"""
    content: str      # TODO 内容文本
    status: str       # 状态：pending / in_progress / completed
    active_form: str  # 活动状态下的显示文本（可能与 content 不同）

    @property
    def marker(self) -> str:
        """返回对应状态的进度标记符号：● 完成 / ◍ 进行中 / ○ 待办"""
        if self.status == "completed":
            return "●"
        if self.status == "in_progress":
            return "◍"
        return "○"

@dataclass
class TodoSummarySnapshot:
    """
    TODO 列表的摘要快照，用于侧边栏进度展示。

    当 total_count > 4 时，只保留最近完成的、进行中的和第一个待办，
    其余折叠到 hidden_count。
    """
    key: str                    # 关联的 session_id 或 agent_id
    items: List[TodoLineSnapshot]  # 当前可见的 TODO 项列表
    completed_count: int        # 已完成数量
    total_count: int            # 总数量
    hidden_count: int = 0       # 被折叠隐藏的数量

    @property
    def active(self) -> Optional[TodoLineSnapshot]:
        """
        返回当前最值得关注的 TODO 项。

        优先级：进行中 > 第一个待办 > 最后一项
        """
        for item in self.items:
            if item.status == "in_progress":
                return item
        for item in self.items:
            if item.status == "pending":
                return item
        return self.items[-1] if self.items else None

    @property
    def progress_text(self) -> str:
        """返回进度文本，如 "进度 3/10 · 30%"。"""
        total = max(self.total_count, 1)
        percent = int((self.completed_count / total) * 100)
        return f"进度 {self.completed_count}/{total} · {percent}%"

@dataclass
class AgentSnapshot:
    """
    协作代理的侧边栏快照，用于侧边栏代理列表展示。
    """
    agent_id: str    # 代理唯一 ID
    label: str       # 显示名称，如 "执行代理"
    status: str      # 状态：idle / active / thinking / waiting / error
    agent_type: str = ""
    current_task: str = ""  # 当前任务描述
    todo_summary: Optional[TodoSummarySnapshot] = None
    updated_at: float = field(default_factory=_now)

    @property
    def status_light(self) -> str:
        """返回对应状态的彩色圆点 emoji，用于侧边栏状态指示。"""
        if self.status == "error":
            return "🔴"
        if self.status == "waiting":
            return "🟡"
        if self.status == "thinking":
            return "🔵"
        if self.status == "active":
            return "🟢"
        return "⚪"

@dataclass
class AgentChildSnapshot:
    agent_id: str
    label: str
    status: str = "running"
    agent_type: str = ""
    mode: str = ""
    task_id: str = ""
    background: bool = False
    content: str = ""
    thinking: str = ""
    tool_calls: List[ToolCallSnapshot] = field(default_factory=list)
    todo_summary: Optional[TodoSummarySnapshot] = None
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    updated_at: float = field(default_factory=_now)

@dataclass
class MessageSnapshot:
    """
    单条消息的完整 UI 快照，包含文本、思考过程、工具调用和子代理信息。
    """
    id: str           # 消息唯一 ID（UUID）
    role: str         # 角色：user / assistant / info / warning / error
    content: str = ""         # 消息文本内容
    thinking: str = ""        # 扩展思考内容（Extended Thinking）
    thinking_collapsed: bool = False  # 思考内容是否折叠显示
    tool_calls: List[ToolCallSnapshot] = field(default_factory=list)  # 工具调用列表
    agent_children: List[AgentChildSnapshot] = field(default_factory=list)  # 子代理列表
    todo_summary: Optional[TodoSummarySnapshot] = None  # TODO 进度摘要
    interrupted: bool = False   # 是否被中断
    completed: bool = False     # 是否已完成（流式结束）
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    kind: str = "message"  # 消息类型：message / info / warning / error

    @property
    def duration_seconds(self) -> int:
        """返回消息从创建到最后更新的耗时（秒），最小值为 1。"""
        return max(1, int(self.updated_at - self.created_at))

@dataclass
class StatusSnapshot:
    model_name: str
    top_status: str
    sub_status: str
    permission_mode: str
    token_count: int
    context_window: int
    effective_context_window: int
    remaining_tokens: int
    model_visible_message_count: int
    session_message_count: int
    session_title: str = "未命名会话"

@dataclass
class UISnapshot:
    messages: List[MessageSnapshot]
    status: StatusSnapshot
    global_todos: TodoSummarySnapshot
    agents: List[AgentSnapshot]
    active_entity_label: str
    active_task_snippet: str
    is_generating: bool
    sidebar_mode: str
    auto_follow: bool
    toasts: List[ToastSnapshot]
    last_retry_prompt: Optional[str]
    interaction: Optional[InteractionRequest]

class UIBridge:
    """
    把 QueryEngine / Todo / BackgroundTask 聚合成 UI 快照。

    [核心职责]
    1. 监听 QueryEngine 的流式事件，转换为 MessageSnapshot 列表
    2. 管理交互请求（权限确认、问题问答、diff 审阅）的生命周期
    3. 维护侧边栏状态（协作代理、TODO 进度、自动跟随）
    4. 提供 get_snapshot() 方法，供 Textual App 定时拉取最新 UI 状态
    5. 通过 subscribe() 注册监听器，在状态变化时主动推送快照

    [数据流]
    QueryEngine.submit_message_stream()
        → 流式事件（text_delta, tool_result, ...）
        → UIBridge.apply_stream_event()
        → 更新 MessageSnapshot 列表
        → notify() → 推送 UISnapshot 给 Textual App
    """
    STREAM_NOTIFY_INTERVAL = 1 / 15  # 流式事件节流间隔（秒），约 15fps
    AUTO_FOLLOW_DWELL_SECONDS = 1.0  # 自动跟随切换的最小停留时间（秒）

    def __init__(self, engine: Any):
        """
        初始化 UIBridge，绑定引擎并恢复历史状态。

        [Workflow]
        1. 初始化所有内部状态（消息列表、工具映射、代理映射等）
        2. 注册 TaskManager 和 TeamManager 的回调，监听后台任务状态变化
        3. 调用 _hydrate_existing_messages() 从引擎消息历史恢复 UI 快照
        4. 调用 _hydrate_persisted_runtime_state() 从磁盘恢复运行时状态

        参数:
            engine: QueryEngine 实例
        """
        self.engine = engine
        self.messages: List[MessageSnapshot] = []
        self.is_generating = False
        self.last_retry_prompt: Optional[str] = None
        self.sidebar_mode = "auto"
        self.auto_follow = True
        self._listeners: List[Callable[[UISnapshot], None]] = []
        self._current_assistant_id: Optional[str] = None
        self._tool_owner: Dict[str, str] = {}
        self._tool_name: Dict[str, str] = {}
        self._agent_owner: Dict[str, str] = {}
        self._last_visible_agent_id: Optional[str] = None
        self._last_visible_agent_at: float = 0.0
        self._pending_visible_agent_id: Optional[str] = None
        self._toasts: List[ToastSnapshot] = []
        self._pending_interactions: Dict[str, asyncio.Future[Any]] = {}
        self._active_interaction: Optional[InteractionRequest] = None
        self._runtime_phase: Optional[str] = None
        self._runtime_resume_target: Optional[str] = None
        self._last_checkpoint_id: Optional[str] = None
        self._retry_checkpoint_id: Optional[str] = None
        self._notify_handle: Optional[asyncio.Handle] = None
        self._last_notify_monotonic: float = 0.0
        self._cached_context_stats: Optional[Dict[str, Any]] = None
        self._cached_context_stats_key: Optional[tuple[Any, ...]] = None
        self._cached_context_stats_at: float = 0.0
        self._task_manager = get_task_manager()
        self._team_manager = get_team_manager()
        self._task_manager.register_status_callback(self._on_task_status_update)
        self._task_manager.register_notification_callback(self._on_task_status_update)
        self._team_manager.mailbox.register_listener(self._on_mailbox_message)
        self._hydrate_existing_messages()
        self._hydrate_persisted_runtime_state()

    def close(self) -> None:
        """
        释放外部回调，取消定时器。

        在 Textual App 卸载时调用，防止悬空回调导致异常。
        """
        if self._notify_handle is not None:
            self._notify_handle.cancel()
            self._notify_handle = None
        try:
            self._task_manager.unregister_status_callback(self._on_task_status_update)
        except Exception:
            pass
        try:
            self._task_manager.unregister_notification_callback(self._on_task_status_update)
        except Exception:
            pass
        try:
            self._team_manager.mailbox.unregister_listener(self._on_mailbox_message)
        except Exception:
            pass

    def subscribe(self, callback: Callable[[UISnapshot], None]) -> None:
        """
        注册 UI 快照监听器。

        每次调用 notify() 时，所有已注册的 callback 都会收到最新的 UISnapshot。

        参数:
            callback: 接收 UISnapshot 的回调函数，如 Textual App 的更新方法
        """
        self._listeners.append(callback)

    def _hydrate_existing_messages(self) -> HistoryHydrationReport:
        """
        从 QueryEngine 的消息历史中恢复 UI 消息快照列表。

        [Workflow]
        1. 读取 engine.messages 中的原始消息列表
        2. 对每条消息尝试合并工具结果（tool_result 类型）
        3. 对其他消息调用 _snapshot_from_record 转换为 MessageSnapshot
        4. 跳过无法解析的消息，记录到 report
        5. 返回恢复报告

        返回:
            HistoryHydrationReport: 恢复统计，如：
                HistoryHydrationReport(raw_count=10, restored_count=9, skipped_count=1)
        """
        raw_messages = list(getattr(self.engine, "messages", []) or [])
        report = HistoryHydrationReport(raw_count=len(raw_messages))
        if not raw_messages:
            return report

        for raw_message in raw_messages:
            try:
                if self._merge_historical_tool_results(raw_message):
                    report.restored_count += 1
                    continue
                snapshot = self._snapshot_from_record(raw_message)
                if snapshot is None:
                    report.skipped_count += 1
                    continue
                self.messages.append(snapshot)
                report.restored_count += 1
                if snapshot.role == "assistant":
                    self._current_assistant_id = snapshot.id
            except Exception:
                report.skipped_count += 1
                continue
        return report

    def _hydrate_persisted_runtime_state(self) -> None:
        """
        从磁盘持久化的 runtime_state 恢复 UI 运行时状态。

        [Workflow]
        1. 获取 session_storage 并调用 load_runtime_state()
        2. 恢复 last_retry_prompt（上次提交的 prompt）
        3. 恢复 app_state（TODO 数据等）
        4. 重放 replay_events（恢复工具调用状态）
        5. 恢复 checkpoint_id、runtime_phase、resume_target
        6. 恢复未完成的 pending_interaction（如权限确认）
        """
        session_storage = getattr(self.engine, "session_storage", None)
        if session_storage is None:
            return
        load_runtime_state = getattr(session_storage, "load_runtime_state", None)
        if not callable(load_runtime_state):
            return
        try:
            runtime_state = load_runtime_state()
        except Exception:
            return
        if not isinstance(runtime_state, dict):
            return

        current_last_prompt = str(getattr(session_storage, "current_last_prompt", "") or "")
        if current_last_prompt and not self.last_retry_prompt:
            self.last_retry_prompt = current_last_prompt
        self._restore_app_state(runtime_state.get("app_state"))
        for event in list(runtime_state.get("replay_events", []) or []):
            if isinstance(event, dict):
                self.apply_stream_event(dict(event), notify=False)

        checkpoint_id = str(runtime_state.get("last_checkpoint_id", "") or "")
        if checkpoint_id and not self._last_checkpoint_id:
            self._last_checkpoint_id = checkpoint_id
        retry_checkpoint_id = str(runtime_state.get("retry_checkpoint_id", "") or "")
        if retry_checkpoint_id and not self._retry_checkpoint_id:
            self._retry_checkpoint_id = retry_checkpoint_id
        runtime_phase = str(runtime_state.get("runtime_phase", "") or "")
        if runtime_phase and not self._runtime_phase:
            self._runtime_phase = runtime_phase
        resume_target = str(runtime_state.get("resume_target", "") or "")
        if resume_target and not self._runtime_resume_target:
            self._runtime_resume_target = resume_target
        self._restore_pending_interaction(runtime_state.get("pending_interaction"))

    def _restore_app_state(self, app_state_payload: Any) -> None:
        """
        将持久化的 app_state 恢复到 execution_context["options"]["app_state"]。

        主要恢复 todos 数据，让引擎在恢复会话后保留上次的 TODO 列表。

        参数:
            app_state_payload: 从磁盘加载的 app_state 字典，如：
                {"todos": {"session_abc": [{"content": "实现登录", "status": "pending"}]}}
        """
        execution_context = getattr(self.engine, "execution_context", None)
        if not isinstance(execution_context, dict):
            return
        options = execution_context.setdefault("options", {})
        if not isinstance(options, dict):
            options = {}
            execution_context["options"] = options

        current_app_state = dict(options.get("app_state", {}) or {})
        restored_app_state = dict(app_state_payload or {}) if isinstance(app_state_payload, dict) else {}
        todos = restored_app_state.get("todos")
        if isinstance(todos, dict):
            current_app_state["todos"] = {
                str(key): [dict(item) for item in value if isinstance(item, dict)]
                for key, value in todos.items()
                if isinstance(value, list)
            }
        options["app_state"] = current_app_state

    def _restore_pending_interaction(self, payload: Any) -> None:
        """
        恢复会话时处理未完成的交互请求。

        若恢复时发现有未处理的交互（如权限确认），在最近的 assistant 消息中
        追加提示文本，并显示 Toast 通知用户。

        参数:
            payload: 持久化的交互请求字典，结构同 InteractionRequest
        """
        request = _interaction_request_from_payload(payload)
        if request is None:
            return

        if self._runtime_phase == "wait_interaction":
            self._runtime_phase = "interrupted"

        detail = str(request.label or request.message or request.kind or "input").strip()
        detail = detail[0].lower() + detail[1:] if detail else "输入"
        can_retry = bool(self._retry_checkpoint_id or self.last_retry_prompt)
        note = (
            f"恢复会话时仍有待处理交互：{detail}。"
            + ("可以点击重试继续。" if can_retry else "如需继续，请重新提交该请求。")
        )

        assistant: Optional[MessageSnapshot] = None
        for message in reversed(self.messages):
            if message.role == "assistant":
                assistant = message
                break

        if assistant is not None:
            body = str(assistant.content or "").strip()
            if note not in body:
                assistant.content = f"{body}\n\n{note}".strip()
            assistant.completed = True
            assistant.interrupted = can_retry
            assistant.updated_at = _now()
            self._current_assistant_id = assistant.id
        else:
            self.add_info_message(note, level="warning")

        self.add_toast(note, level="warning", duration=5.0)

    def _track_visible_agent(self, agent_id: Optional[str], *, immediate: bool = False) -> None:
        """
        更新当前可见代理，实现自动跟随的防抖逻辑。

        [Workflow]
        1. 若 immediate=True 或满足停留时间条件，立即切换可见代理
        2. 否则将 agent_id 存入 _pending_visible_agent_id，等待停留时间到期后提升
        3. 错误状态（immediate=True）时立即切换，不等待停留时间

        参数:
            agent_id: 要跟随的代理 ID
            immediate: True 表示立即切换（如代理出错时）
        """
        normalized = str(agent_id or "").strip()
        if not normalized:
            return

        now = _now()
        if (
            immediate
            or self._last_visible_agent_id is None
            or normalized == self._last_visible_agent_id
            or (now - self._last_visible_agent_at) >= self.AUTO_FOLLOW_DWELL_SECONDS
        ):
            self._last_visible_agent_id = normalized
            self._last_visible_agent_at = now
            if immediate or self._pending_visible_agent_id == normalized:
                self._pending_visible_agent_id = None
            return

        self._pending_visible_agent_id = normalized

    def _promote_pending_visible_agent(self) -> None:
        """
        将待定的可见代理提升为当前可见代理（若停留时间已到期）。

        在每次 get_snapshot() 时调用，实现自动跟随的延迟切换效果。
        """
        pending = str(self._pending_visible_agent_id or "").strip()
        if not pending:
            return
        if pending == self._last_visible_agent_id:
            self._pending_visible_agent_id = None
            return

        now = _now()
        if self._last_visible_agent_id is None or (now - self._last_visible_agent_at) >= self.AUTO_FOLLOW_DWELL_SECONDS:
            self._last_visible_agent_id = pending
            self._last_visible_agent_at = now
            self._pending_visible_agent_id = None

    def _merge_historical_tool_results(self, record: Any) -> bool:
        """
        将历史消息中的 tool_result 块合并到对应的 assistant 消息快照中。

        [Workflow]
        1. 检查 record 是否为 role=user 且包含 tool_result 块
        2. 找到当前 assistant 消息快照
        3. 对每个 tool_result 块，找到或创建对应的 ToolCallSnapshot
        4. 填充 result、receipt、audit_events、is_error 等字段
        5. 若 receipt 是 agent 类型，同步更新子代理快照

        参数:
            record: 原始消息记录（字典或对象）

        返回:
            bool: True 表示成功合并（是 tool_result 消息），False 表示不是
        """
        role = str(_extract_attr(record, "role", "") or "")
        if role != "user":
            return False

        content = _extract_attr(record, "content", [])
        if not isinstance(content, list):
            return False

        tool_result_blocks = [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        if not tool_result_blocks:
            return False

        message = self.current_assistant
        if message is None:
            return False

        for block in tool_result_blocks:
            tool_use_id = str(_extract_attr(block, "tool_use_id", "") or "")
            call = self._tool_call_for_id(message, tool_use_id)
            if call is None:
                call = ToolCallSnapshot(
                    tool_use_id=tool_use_id,
                    name=self._tool_name.get(tool_use_id, "工具"),
                    status="completed",
                )
                message.tool_calls.append(call)
            call.result = str(_extract_attr(block, "content", "") or "")
            receipt = _extract_attr(block, "receipt", None)
            call.receipt = receipt if isinstance(receipt, dict) else None
            raw_audit_events = _extract_attr(block, "audit_events", []) or []
            call.audit_events = [item for item in raw_audit_events if isinstance(item, dict)]
            call.is_error = bool(_extract_attr(block, "is_error", False))
            call.status = "error" if call.is_error else "completed"
            call.updated_at = _now()
            if isinstance(call.receipt, dict) and call.receipt.get("kind") == "agent":
                agent_id = str(call.receipt.get("agent_id", "") or "")
                if agent_id:
                    child = self._ensure_agent_child(
                        agent_id,
                        label=str(call.receipt.get("summary", "") or agent_id),
                        status=str(call.receipt.get("status", "") or "completed"),
                        agent_type=str(call.receipt.get("agent_type", "") or ""),
                        mode=str(call.receipt.get("mode", "") or ""),
                        task_id=str(call.receipt.get("task_id", "") or ""),
                        background=bool(call.receipt.get("background", False)),
                    )
                    preview = str(call.receipt.get("result_preview", "") or "")
                    if preview:
                        child.content = preview
                    child.total_tokens = int(call.receipt.get("total_tokens", 0) or 0)
                    child.audit_events.extend(call.audit_events[-3:])
                    child.audit_events = child.audit_events[-5:]
            message.updated_at = _now()

        return True

    def _snapshot_from_record(self, record: Any) -> Optional[MessageSnapshot]:
        """
        将原始消息记录转换为 MessageSnapshot。

        [Workflow]
        1. 提取 role 字段，不支持的 role 返回 None
        2. user 消息：提取文本内容（跳过 tool_result 块）
        3. assistant 消息：解析 content_blocks（text/thinking/tool_use）
        4. 返回构建好的 MessageSnapshot

        参数:
            record: 原始消息记录（字典或对象）

        返回:
            MessageSnapshot | None: 转换结果，不支持的消息类型返回 None
        """
        role = str(_extract_attr(record, "role", "") or "")
        if role == "user":
            content = _extract_attr(record, "content", "")
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if not item:
                        continue
                    if isinstance(item, dict):
                        if str(item.get("type", "") or "") == "tool_result":
                            continue
                        block_text = str(item.get("text", "") or item.get("content", "") or "").strip()
                        if block_text:
                            parts.append(block_text)
                    else:
                        rendered = str(item).strip()
                        if rendered:
                            parts.append(rendered)
                content = "\n".join(parts)
            return MessageSnapshot(
                id=str(_extract_attr(record, "uuid", str(uuid4()))),
                role="user",
                content=str(content or ""),
                completed=True,
            )

        if role != "assistant":
            return None

        content_blocks = _extract_attr(record, "content", [])
        message = MessageSnapshot(
            id=str(_extract_attr(record, "uuid", str(uuid4()))),
            role="assistant",
            completed=True,
        )

        if isinstance(content_blocks, str):
            message.content = content_blocks
            return message

        for block in content_blocks if isinstance(content_blocks, list) else []:
            block_type = str(_extract_attr(block, "type", "") or "")
            if block_type == "text":
                message.content += str(_extract_attr(block, "text", "") or "")
            elif block_type == "thinking":
                message.thinking += str(_extract_attr(block, "thinking", "") or "")
                message.thinking_collapsed = True
            elif block_type == "tool_use":
                tool_use_id = str(_extract_attr(block, "id", str(uuid4())))
                tool_name = str(_extract_attr(block, "name", "工具"))
                input_data = _extract_attr(block, "input", {})
                preview = input_data if isinstance(input_data, str) else str(input_data)
                call = ToolCallSnapshot(
                    tool_use_id=tool_use_id,
                    name=tool_name,
                    status="completed",
                    input_preview=preview,
                )
                message.tool_calls.append(call)
                self._tool_owner[tool_use_id] = message.id
                self._tool_name[tool_use_id] = tool_name

        return message

    async def _on_task_status_update(self, task: Any) -> None:
        """
        后台任务状态变化回调，更新对应子代理的 UI 快照。

        由 TaskManager 在任务状态变化时调用（RUNNING/FAILED/COMPLETED）。

        参数:
            task: 任务对象，包含 agent_id、status、current_action 等属性
        """
        agent_id = getattr(task, "agent_id", "") or ""
        if agent_id:
            latest_status = getattr(task, "status", None)
            self._track_visible_agent(agent_id, immediate=latest_status == TaskStatus.FAILED)
            child = self._ensure_agent_child(
                agent_id,
                label=agent_id,
                task_id=str(getattr(task, "task_id", "") or ""),
            )
            if latest_status == TaskStatus.RUNNING:
                child.status = "active"
            elif latest_status == TaskStatus.FAILED:
                child.status = "error"
            elif latest_status == TaskStatus.COMPLETED:
                child.status = "completed"
            current_action = str(
                getattr(task, "current_action", None)
                or getattr(task, "description", "")
                or ""
            )
            if current_action:
                child.content = current_action
            child.updated_at = _now()
        self.notify()

    async def _on_mailbox_message(self, message: Any) -> None:
        """
        团队邮箱消息回调，更新目标代理的活跃时间戳。

        由 TeamManager.mailbox 在收到新消息时调用，触发侧边栏刷新。

        参数:
            message: 邮箱消息对象，包含 to_agent 属性
        """
        agent_id = str(getattr(message, "to_agent", "") or "")
        if agent_id:
            self._track_visible_agent(agent_id)
            child = self._ensure_agent_child(agent_id, label=agent_id)
            child.updated_at = _now()
        self.notify()

    @property
    def current_assistant(self) -> Optional[MessageSnapshot]:
        """
        返回当前活动的 assistant 消息快照。

        从消息列表末尾向前查找，返回 ID 匹配 _current_assistant_id 的消息。
        若无活动 assistant 消息则返回 None。
        """
        if self._current_assistant_id is None:
            return None
        for message in reversed(self.messages):
            if message.id == self._current_assistant_id:
                return message
        return None

    def _ensure_assistant_message(self) -> MessageSnapshot:
        """
        获取或创建当前 assistant 消息快照。

        若当前已有活动 assistant 消息则直接返回，否则创建新的并追加到列表。

        返回:
            MessageSnapshot: 当前活动的 assistant 消息快照
        """
        current = self.current_assistant
        if current is not None:
            return current
        assistant = MessageSnapshot(
            id=str(uuid4()),
            role="assistant",
        )
        self.messages.append(assistant)
        self._current_assistant_id = assistant.id
        return assistant

    def _message_for_agent(self, agent_id: str) -> Optional[MessageSnapshot]:
        """
        根据代理 ID 查找其所属的 assistant 消息快照。

        参数:
            agent_id: 代理唯一 ID

        返回:
            MessageSnapshot | None: 所属消息，未找到则返回 None
        """
        owner_id = self._agent_owner.get(agent_id)
        if owner_id is None:
            return None
        for message in self.messages:
            if message.id == owner_id:
                return message
        return None

    def _ensure_agent_child(
        self,
        agent_id: str,
        *,
        label: Optional[str] = None,
        status: Optional[str] = None,
        agent_type: Optional[str] = None,
        mode: Optional[str] = None,
        task_id: Optional[str] = None,
        background: Optional[bool] = None,
    ) -> AgentChildSnapshot:
        """
        获取或创建指定代理的子代理快照，并更新其属性。

        [Workflow]
        1. 查找代理所属的 assistant 消息（若无则创建）
        2. 在消息的 agent_children 中查找已有的子代理快照
        3. 若不存在则创建新的 AgentChildSnapshot 并追加
        4. 更新传入的非 None 字段
        5. 更新时间戳并返回

        参数:
            agent_id: 代理唯一 ID
            label: 显示名称（可选，不传则保留原值）
            status: 状态（可选）
            agent_type: 代理类型（可选）
            mode: 执行模式（可选）
            task_id: 关联任务 ID（可选）
            background: 是否后台执行（可选）

        返回:
            AgentChildSnapshot: 更新后的子代理快照
        """
        message = self._message_for_agent(agent_id)
        if message is None:
            message = self._ensure_assistant_message()
            self._agent_owner[agent_id] = message.id

        for child in message.agent_children:
            if child.agent_id == agent_id:
                target = child
                break
        else:
            target = AgentChildSnapshot(
                agent_id=agent_id,
                label=label or agent_id,
                status=status or "running",
                agent_type=agent_type or "",
                mode=mode or "",
                task_id=task_id or "",
                background=bool(background),
            )
            message.agent_children.append(target)

        if label:
            target.label = label
        if status:
            target.status = status
        if agent_type is not None:
            target.agent_type = agent_type
        if mode is not None:
            target.mode = mode
        if task_id is not None:
            target.task_id = task_id
        if background is not None:
            target.background = background
        target.updated_at = _now()
        message.updated_at = _now()
        return target

    def add_toast(self, message: str, level: str = "info", duration: float = 3.0) -> None:
        """
        添加一条 Toast 通知（短暂显示后自动消失）。

        参数:
            message: 通知文本
            level: 级别，"info" / "warning" / "error"
            duration: 显示时长（秒），默认 3.0
        """
        self._toasts.append(
            ToastSnapshot(
                id=str(uuid4()),
                message=message,
                level=level,
                expires_at=_now() + duration,
            )
        )

    def prune_toasts(self) -> None:
        """清理已过期的 Toast 通知。"""
        now = _now()
        self._toasts = [toast for toast in self._toasts if toast.expires_at > now]

    def begin_user_turn(self, prompt: str) -> None:
        """
        开始新的用户轮次，重置运行时状态并追加用户消息。

        [Workflow]
        1. 记录 prompt 到 last_retry_prompt（供重试使用）
        2. 重置运行时阶段和检查点状态
        3. 追加用户消息快照到 messages 列表
        4. 清空当前 assistant 指针，准备接收新的 assistant 消息
        5. 通知 UI 更新

        参数:
            prompt: 用户输入的消息文本
        """
        self.last_retry_prompt = prompt
        self._runtime_phase = "prepare_turn"
        self._runtime_resume_target = None
        self._last_checkpoint_id = None
        self._retry_checkpoint_id = None
        self.messages.append(
            MessageSnapshot(
                id=str(uuid4()),
                role="user",
                content=prompt,
                completed=True,
            )
        )
        self._current_assistant_id = None
        self.notify()

    def add_info_message(self, content: str, level: str = "info") -> None:
        """
        添加系统信息消息（非对话消息），用于命令输出、错误提示等。

        参数:
            content: 消息内容文本
            level: 消息级别，"info" / "warning" / "error"
        """
        self.messages.append(
            MessageSnapshot(
                id=str(uuid4()),
                role=level,
                content=content,
                completed=True,
                kind=level,
            )
        )
        self.notify()

    def _ensure_permission_context(self) -> Any:
        """
        获取或创建权限上下文对象。

        若 execution_context 中没有 permission_context，则创建默认的并写回。

        返回:
            ToolPermissionContext: 权限上下文对象
        """
        execution_context = getattr(self.engine, "execution_context", None)
        if not isinstance(execution_context, dict):
            execution_context = {}
            self.engine.execution_context = execution_context

        permission_context = execution_context.get("permission_context")
        if permission_context is None:
            from codo.services.tools.permission_checker import create_default_permission_context

            cwd = str(getattr(self.engine, "cwd", execution_context.get("cwd", ".")))
            permission_context = create_default_permission_context(cwd)
            execution_context["permission_context"] = permission_context
        return permission_context

    @staticmethod
    def _permission_mode_label(mode: Any) -> str:
        """
        将权限模式枚举转换为机器可读标签。

        返回:
            str: "bypass" 或 "ask"
        """
        return "bypass" if mode == PermissionMode.BYPASS_PERMISSIONS else "ask"

    def _derive_session_title(self) -> str:
        """
        推导当前会话的显示标题。

        优先级：session_storage.current_title > user_title > ai_title >
                first_prompt > 第一条用户消息 > "未命名会话"

        返回:
            str: 会话标题，最长 72 字符
        """
        session_storage = getattr(self.engine, "session_storage", None)

        current_title = str(getattr(session_storage, "current_title", "") or "").strip()
        if current_title:
            return _truncate(current_title, 72)

        if session_storage is not None:
            get_info = getattr(session_storage, "get_session_info", None)
            if callable(get_info):
                try:
                    info = dict(get_info() or {})
                except Exception:
                    info = {}
                for key in ("user_title", "ai_title", "first_prompt"):
                    value = str(info.get(key, "") or "").strip()
                    if value:
                        return _truncate(value, 72)

        for collection in (self.messages, list(getattr(self.engine, "messages", []) or [])):
            for message in collection:
                role = str(_extract_attr(message, "role", "") or "")
                if role != "user":
                    continue
                content = _extract_attr(message, "content", "")
                if isinstance(content, list):
                    parts: list[str] = []
                    for block in content:
                        if isinstance(block, dict):
                            block_text = str(block.get("text", "") or block.get("content", "") or "")
                            if block_text:
                                parts.append(block_text)
                        elif block:
                            parts.append(str(block))
                    content_text = "\n".join(parts).strip()
                else:
                    content_text = str(content or "").strip()
                if content_text:
                    return _truncate(content_text.replace("\n", " "), 72)

        return "未命名会话"

    def _session_allow_rules(self, permission_context: Any) -> List[str]:
        """
        获取当前会话的放行规则列表（可写引用）。

        参数:
            permission_context: 权限上下文对象

        返回:
            List[str]: 会话放行规则列表，如 ["Bash", "Read"]
        """
        always_allow = getattr(permission_context, "always_allow_rules", {})
        session_rules = always_allow.get(PermissionRuleSource.SESSION)
        if session_rules is None:
            session_rules = []
            always_allow[PermissionRuleSource.SESSION] = session_rules
        return session_rules

    def get_permission_mode_state(self) -> Dict[str, Any]:
        """
        获取当前权限模式的完整状态，供 UI 展示和命令处理使用。

        返回:
            dict: 权限状态字典，如：
                {
                    "mode": PermissionMode.DEFAULT,
                    "label": "ask",
                    "display_label": "询问",
                    "bypass_confirmed": False,
                    "session_allow_rules": ["Bash"],
                    "session_allow_rule_count": 1,
                    "bypass_available": True,
                }
        """
        permission_context = self._ensure_permission_context()
        global_config = get_global_config()
        session_rules = list(self._session_allow_rules(permission_context))
        mode = getattr(permission_context, "mode", PermissionMode.DEFAULT)
        return {
            "mode": mode,
            "label": self._permission_mode_label(mode),
            "display_label": _permission_mode_display_label(mode.value if isinstance(mode, PermissionMode) else mode),
            "bypass_confirmed": bool(global_config.bypass_permissions_mode_accepted),
            "session_allow_rules": session_rules,
            "session_allow_rule_count": len(session_rules),
            "bypass_available": bool(
                getattr(permission_context, "is_bypass_permissions_mode_available", True)
            ),
        }

    def _record_permission_mode_change(
        self,
        mode: PermissionMode,
        *,
        source: str,
        strict: bool = False,
        cleared_rules: int = 0,
    ) -> None:
        """
        记录权限模式变更到 execution_context 和 session_storage。

        [Workflow]
        1. 将新模式写入 execution_context["options"]["permission_mode"]
        2. 通过 session_storage.append_event 持久化变更事件

        参数:
            mode: 新的权限模式
            source: 变更来源，如 "command"、"interaction"
            strict: 是否严格模式（清除了会话放行规则）
            cleared_rules: 被清除的会话放行规则数量
        """
        execution_context = getattr(self.engine, "execution_context", {})
        if isinstance(execution_context, dict):
            options = execution_context.setdefault("options", {})
            if isinstance(options, dict):
                options["permission_mode"] = mode.value

        session_storage = getattr(self.engine, "session_storage", None)
        if session_storage is not None and hasattr(session_storage, "append_event"):
            try:
                session_storage.append_event(
                    "permission_mode_changed",
                    {
                        "permission_mode": mode.value,
                        "label": self._permission_mode_label(mode),
                        "source": source,
                        "strict": strict,
                        "cleared_session_allow_rules": cleared_rules,
                    },
                )
            except Exception:
                pass

    def set_permission_mode(
        self,
        mode: str,
        *,
        strict: bool = False,
        confirm: bool = False,
        source: str = "command",
    ) -> Dict[str, Any]:
        """
        设置权限模式（询问 / 直通）。

        [Workflow]
        1. 获取权限上下文和当前会话放行规则
        2. mode == "ask"/"default"：切换为询问模式，strict=True 时清除会话放行规则
        3. mode == "bypass"：切换为直通模式，需要 confirm=True 或已全局确认
        4. 其他 mode：显示用法提示并返回失败
        5. 记录模式变更到 execution_context 和 session_storage
        6. 显示 Toast 通知并触发 UI 刷新

        参数:
            mode: 目标权限模式，"ask"/"default" 或 "bypass"/"bypasspermissions"
            strict: 切换为询问模式时是否同时清除所有会话放行规则
            confirm: 切换为直通模式时是否跳过全局确认检查
            source: 变更来源，如 "command"、"interaction"

        返回:
            dict: 操作结果，如：
                {"success": True, "message": "权限模式已切换为：询问", "mode": PermissionMode.DEFAULT}
                {"success": False, "message": "直通模式会跳过后续权限确认...", "mode": PermissionMode.DEFAULT}
        """
        permission_context = self._ensure_permission_context()
        normalized = str(mode or "").strip().lower()
        session_rules = self._session_allow_rules(permission_context)

        if normalized in {"ask", "default"}:
            cleared_rules = 0
            if strict:
                cleared_rules = len(session_rules)
                session_rules.clear()
            permission_context.mode = PermissionMode.DEFAULT
            self._record_permission_mode_change(
                PermissionMode.DEFAULT,
                source=source,
                strict=strict,
                cleared_rules=cleared_rules,
            )
            if strict and cleared_rules:
                message = f"权限模式已切换为：询问（清除了 {cleared_rules} 条会话放行规则）"
            elif strict:
                message = "权限模式已切换为：询问（严格模式）"
            else:
                message = "权限模式已切换为：询问"
            self.add_toast(message, level="info")
            self.notify()
            return {"success": True, "message": message, "mode": PermissionMode.DEFAULT}

        if normalized in {"bypass", "bypasspermissions"}:
            if not getattr(permission_context, "is_bypass_permissions_mode_available", True):
                message = "当前会话不可用直通模式"
                self.add_toast(message, level="warning")
                self.notify()
                return {"success": False, "message": message, "mode": getattr(permission_context, "mode", PermissionMode.DEFAULT)}

            global_config = get_global_config()
            if not global_config.bypass_permissions_mode_accepted and not confirm:
                message = (
                    "直通模式会跳过后续权限确认。"
                    "运行 /permissions bypass confirm，或在这里按 [B] 启用。"
                )
                self.add_toast(message, level="warning", duration=5.0)
                self.notify()
                return {"success": False, "message": message, "mode": getattr(permission_context, "mode", PermissionMode.DEFAULT)}

            if not global_config.bypass_permissions_mode_accepted:
                global_config.bypass_permissions_mode_accepted = True
                save_global_config(global_config)

            permission_context.mode = PermissionMode.BYPASS_PERMISSIONS
            self._record_permission_mode_change(
                PermissionMode.BYPASS_PERMISSIONS,
                source=source,
            )
            message = "权限模式已切换为：直通"
            self.add_toast(message, level="info")
            self.notify()
            return {"success": True, "message": message, "mode": PermissionMode.BYPASS_PERMISSIONS}

        message = "用法：/permissions [show|ask [--strict]|bypass [confirm]]"
        self.add_toast(message, level="warning")
        self.notify()
        return {"success": False, "message": message, "mode": getattr(permission_context, "mode", PermissionMode.DEFAULT)}

    def clear_conversation(self) -> None:
        """
        清空当前对话的所有消息和运行时状态（保留 Toast 通知）。

        用于 /clear 命令，重置 UI 到初始状态。
        """
        self._reset_runtime_view_state(clear_toasts=False)
        self.notify()

    def _reset_runtime_view_state(self, *, clear_toasts: bool = True) -> None:
        """
        重置所有运行时视图状态到初始值。

        参数:
            clear_toasts: True 时同时清空 Toast 列表，False 时保留
        """
        if clear_toasts:
            self._toasts.clear()
        self.messages.clear()
        self.is_generating = False
        self.last_retry_prompt = None
        self._current_assistant_id = None
        self._tool_owner.clear()
        self._tool_name.clear()
        self._agent_owner.clear()
        self._last_visible_agent_id = None
        self._last_visible_agent_at = 0.0
        self._pending_visible_agent_id = None
        self._runtime_phase = None
        self._runtime_resume_target = None
        self._last_checkpoint_id = None
        self._retry_checkpoint_id = None
        self._cached_context_stats = None
        self._cached_context_stats_key = None
        self._cached_context_stats_at = 0.0

    def _capture_runtime_view_state(self) -> Dict[str, Any]:
        """
        捕获当前运行时视图状态的深拷贝快照，用于错误恢复时回滚。

        返回:
            dict: 包含 messages、is_generating、tool_owner 等所有视图状态的深拷贝字典
        """
        return {
            "messages": copy.deepcopy(self.messages),
            "is_generating": self.is_generating,
            "last_retry_prompt": self.last_retry_prompt,
            "current_assistant_id": self._current_assistant_id,
            "tool_owner": dict(self._tool_owner),
            "tool_name": dict(self._tool_name),
            "agent_owner": dict(self._agent_owner),
            "last_visible_agent_id": self._last_visible_agent_id,
            "last_visible_agent_at": self._last_visible_agent_at,
            "pending_visible_agent_id": self._pending_visible_agent_id,
            "toasts": copy.deepcopy(self._toasts),
            "runtime_phase": self._runtime_phase,
            "runtime_resume_target": self._runtime_resume_target,
            "last_checkpoint_id": self._last_checkpoint_id,
            "retry_checkpoint_id": self._retry_checkpoint_id,
        }

    def _restore_runtime_view_state(self, state: Dict[str, Any]) -> None:
        """
        从捕获的状态快照恢复运行时视图状态（用于错误回滚）。

        参数:
            state: 由 _capture_runtime_view_state() 返回的状态字典
        """
        self.messages = copy.deepcopy(state.get("messages", []))
        self.is_generating = bool(state.get("is_generating", False))
        self.last_retry_prompt = state.get("last_retry_prompt")
        self._current_assistant_id = state.get("current_assistant_id")
        self._tool_owner = dict(state.get("tool_owner", {}) or {})
        self._tool_name = dict(state.get("tool_name", {}) or {})
        self._agent_owner = dict(state.get("agent_owner", {}) or {})
        self._last_visible_agent_id = state.get("last_visible_agent_id")
        self._last_visible_agent_at = float(state.get("last_visible_agent_at", 0.0) or 0.0)
        self._pending_visible_agent_id = state.get("pending_visible_agent_id")
        self._toasts = copy.deepcopy(state.get("toasts", []))
        self._runtime_phase = state.get("runtime_phase")
        self._runtime_resume_target = state.get("runtime_resume_target")
        self._last_checkpoint_id = state.get("last_checkpoint_id")
        self._retry_checkpoint_id = state.get("retry_checkpoint_id")
        self._cached_context_stats = None
        self._cached_context_stats_key = None
        self._cached_context_stats_at = 0.0

    @property
    def has_active_interaction(self) -> bool:
        """返回当前是否有活动的交互请求（权限确认/问题/diff 审阅）。"""
        return self._active_interaction is not None

    async def request_interaction(self, request: InteractionRequest) -> Any:
        """
        发起一个交互请求并等待用户响应。

        [Workflow]
        1. 创建 asyncio.Future 存入 _pending_interactions
        2. 设置 _active_interaction 并通知 UI 显示交互组件
        3. 等待 Future 被 resolve_interaction() 或 cancel_interaction() 完成
        4. finally 块中清理交互状态

        参数:
            request: 交互请求对象

        返回:
            Any: 用户选择结果，如 "allow_once"、{"问题": "答案"}，取消时返回 None
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_interactions[request.request_id] = future
        self._active_interaction = request
        self.notify()
        try:
            return await future
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            return None
        finally:
            self._clear_interaction(request.request_id)

    async def request_permission(
        self,
        tool_name: str,
        tool_info: str,
        message: str = "",
    ) -> Optional[str]:
        """
        发起权限确认交互，等待用户选择允许/拒绝/中止。

        参数:
            tool_name: 请求权限的工具名称，如 "Bash"
            tool_info: 工具操作详情，如 "rm -rf /tmp/test"
            message: 附加说明（可选）

        返回:
            str | None: "allow_once" / "allow_always" / "deny" / "abort"，取消时返回 None
        """
        return await self.request_interaction(
            InteractionRequest(
                request_id=str(uuid4()),
                kind="permission",
                label=f"等待权限：{tool_name}",
                tool_name=tool_name,
                tool_info=tool_info,
                message=message,
                options=[
                    InteractionOption(value="allow_once", label="本次允许"),
                    InteractionOption(value="allow_always", label="本会话始终允许"),
                    InteractionOption(value="deny", label="拒绝"),
                    InteractionOption(value="abort", label="中止"),
                ],
            )
        )

    async def request_questions(self, questions: List[Any]) -> Optional[Dict[str, str]]:
        """
        发起多问题问答交互，等待用户逐题回答。

        参数:
            questions: 问题列表，每个问题包含 header、question、options 等字段

        返回:
            Dict[str, str] | None: 问题到答案的映射，取消时返回 None
        """
        interaction_questions: List[InteractionQuestion] = []

        for index, question in enumerate(questions, 1):
            options = [
                InteractionOption(
                    value=str(_extract_attr(option, "label", "") or ""),
                    label=str(_extract_attr(option, "label", "") or ""),
                    description=str(_extract_attr(option, "description", "") or ""),
                    preview=str(_extract_attr(option, "preview", "") or ""),
                )
                for option in list(_extract_attr(question, "options", []) or [])
            ]
            interaction_questions.append(
                InteractionQuestion(
                    question_id=str(uuid4()),
                    header=str(_extract_attr(question, "header", f"问题 {index}") or f"问题 {index}"),
                    question=str(_extract_attr(question, "question", f"请回答问题 {index}") or f"请回答问题 {index}"),
                    options=options,
                    multi_select=bool(
                        _extract_attr(question, "multiSelect", False)
                        or _extract_attr(question, "multi_select", False)
                    ),
                )
            )

        return await self.request_interaction(
            InteractionRequest(
                request_id=str(uuid4()),
                kind="question",
                label=interaction_questions[0].header if interaction_questions else "等待你的回答",
                questions=interaction_questions,
            )
        )

    async def request_change_review(self, change: ProposedFileChange) -> Optional[str]:
        """
        发起文件变更审阅交互，等待用户接受或拒绝。

        参数:
            change: 待审阅的文件变更对象

        返回:
            str | None: "accept" 或 "reject"，取消时返回 None
        """
        return await self.request_interaction(
            InteractionRequest(
                request_id=str(uuid4()),
                kind="diff_review",
                label=f"审阅变更：{change.path}",
                message="要应用这些变更吗？",
                options=[
                    InteractionOption(value="accept", label="接受"),
                    InteractionOption(value="reject", label="拒绝"),
                ],
                payload={
                    "change_id": change.change_id,
                    "path": change.path,
                    "diff_text": change.diff_text,
                    "original_content": change.original_content,
                    "new_content": change.new_content,
                },
            )
        )

    def resolve_interaction(self, request_id: Optional[str] = None, data: Any = None) -> None:
        """
        完成一个交互请求，将用户的选择结果传回等待方。

        [Workflow]
        1. 确定目标 request_id（若未指定则使用当前活动交互的 ID）
        2. 若有本地 Future 在等待，直接 set_result
        3. 否则通过 engine.resolve_interaction 传递到运行时控制器
        4. 清理交互状态

        参数:
            request_id: 交互请求 ID，None 时使用当前活动交互
            data: 用户选择结果，如 "allow_once"、{"问题1": "答案1"} 等
        """
        target_id = request_id
        if target_id is None and self._active_interaction is not None:
            target_id = self._active_interaction.request_id
        if not target_id:
            return
        future = self._pending_interactions.get(target_id)
        if future is not None and not future.done():
            future.set_result(data)
            return
        if self._active_interaction is not None and self._active_interaction.request_id == target_id:
            resolve = getattr(self.engine, "resolve_interaction", None)
            if callable(resolve):
                resolve(target_id, data)
        self._clear_interaction(target_id)

    def cancel_interaction(self, request_id: Optional[str] = None) -> None:
        """
        取消一个交互请求（等同于 resolve(None)）。

        参数:
            request_id: 交互请求 ID，None 时取消当前活动交互
        """
        self.resolve_interaction(request_id, None)

    def _clear_interaction(self, request_id: str) -> None:
        """
        清理指定交互请求的状态（Future、active_interaction），并通知 UI 刷新。

        参数:
            request_id: 要清理的交互请求 ID
        """
        self._pending_interactions.pop(request_id, None)
        if self._active_interaction is not None and self._active_interaction.request_id == request_id:
            self._active_interaction = None
        self.notify()

    def _reset_message_state(self) -> None:
        """重置消息状态（保留 Toast），用于重新加载消息前的清理。"""
        self._reset_runtime_view_state(clear_toasts=False)

    def _reload_messages_from_engine(self) -> None:
        """
        从引擎重新加载消息历史，失败时回滚到之前状态。

        [Workflow]
        1. 捕获当前状态快照
        2. 重置消息状态
        3. 重新水化历史消息
        4. 若异常则回滚
        """
        previous_state = self._capture_runtime_view_state()
        try:
            self._reset_message_state()
            self._hydrate_existing_messages()
        except Exception:
            self._restore_runtime_view_state(previous_state)
            raise

    def reload_from_engine(self) -> None:
        """
        完整重新加载引擎状态到 UI（含历史消息和运行时状态）。

        [Workflow]
        1. 捕获当前状态快照（用于失败回滚）
        2. 取消所有待处理的交互 Future
        3. 重置运行时视图状态
        4. 重新水化历史消息和持久化运行时状态
        5. 若有跳过的损坏记录，显示 Toast 警告
        6. 若完全失败，回滚到之前状态
        """
        previous_state = self._capture_runtime_view_state()
        pending = list(self._pending_interactions.values())
        self._pending_interactions.clear()
        for future in pending:
            if not future.done():
                future.cancel()
        self._active_interaction = None
        try:
            self._reset_runtime_view_state()
            report = self._hydrate_existing_messages()
            self._hydrate_persisted_runtime_state()
            if report.skipped_count:
                if report.restored_count == 0 and previous_state.get("messages"):
                    self._restore_runtime_view_state(previous_state)
                    self.add_toast(
                        "恢复历史时跳过了损坏记录，已保留上一份可用内容。",
                        level="warning",
                        duration=5.0,
                    )
                else:
                    self.add_toast(
                        f"恢复历史时已跳过 {report.skipped_count} 条损坏记录。",
                        level="warning",
                        duration=4.0,
                    )
        except Exception:
            self._restore_runtime_view_state(previous_state)
            self.add_toast("恢复历史时跳过了损坏记录，已保留上一份可用内容。", level="warning", duration=5.0)
        self.notify()

    def _remember_retry_checkpoint(self, event: Dict[str, Any]) -> None:
        """
        从 status_changed 事件中提取并记录可重试的检查点 ID。

        只有处于可重试阶段（prepare_turn、execute_tools 等）的检查点才会被记录为
        _retry_checkpoint_id，用于后续的重试操作。

        参数:
            event: status_changed 事件字典，包含 checkpoint_id 和 phase 字段
        """
        checkpoint_id = str(event.get("checkpoint_id", "") or "")
        if not checkpoint_id:
            return
        phase = str(event.get("phase", "") or "")
        retryable_phases = {
            "prepare_turn",
            "stream_assistant",
            "collect_tool_calls",
            "execute_tools",
            "wait_interaction",
            "apply_interaction_result",
            "stop_hooks",
            "compact",
        }
        if phase in retryable_phases:
            self._retry_checkpoint_id = checkpoint_id
        self._last_checkpoint_id = checkpoint_id

    def _runtime_sub_status(self) -> Optional[str]:
        """
        根据当前运行时阶段返回状态栏的副标题文本。

        返回:
            str | None: 中文状态描述，如 "正在流式输出"、"执行工具中"，
                        未知阶段返回 None
        """
        phase = str(self._runtime_phase or "")
        if phase == "prepare_turn":
            return "准备本轮对话"
        if phase == "stream_assistant":
            return "正在流式输出"
        if phase == "collect_tool_calls":
            return "收集工具调用"
        if phase == "execute_tools":
            return "执行工具中"
        if phase == "wait_interaction":
            return self._active_interaction.label if self._active_interaction is not None else "等待输入"
        if phase == "apply_interaction_result":
            return "应用你的选择"
        if phase == "stop_hooks":
            return "执行停止钩子"
        if phase == "compact":
            return "压缩上下文"
        if phase == "interrupted":
            return "已中断"
        if phase == "error":
            return "处理错误中"
        return None

    def apply_stream_event(self, event: Dict[str, Any], *, notify: bool = True) -> None:
        """
        处理来自 QueryEngine 的单条流式事件，更新内部消息快照状态。

        [Workflow]
        1. 根据 event["type"] 分发到对应处理逻辑
        2. 更新 MessageSnapshot（content、thinking、tool_calls 等）
        3. 更新运行时状态（phase、interaction、checkpoint 等）
        4. 按需通知 UI 刷新

        支持的事件类型:
            - stream_request_start: 新一轮流式请求开始
            - content_block_start/stop: 内容块开始/结束
            - text_delta / thinking_delta / input_json_delta: 增量文本
            - tool_started / tool_progress / tool_completed / tool_result: 工具生命周期
            - agent_started / agent_delta / agent_tool_* / agent_completed / agent_error: 子代理事件
            - todo_updated: TODO 列表更新
            - compact: 上下文压缩完成
            - interaction_requested / interaction_resolved: 交互请求生命周期
            - status_changed / checkpoint_restored / interrupt_ack / turn_completed: 运行时状态
            - error / message_stop: 错误和消息结束

        参数:
            event: 流式事件字典，如 {"type": "text_delta", "delta": {"text": "你好"}}
            notify: 是否触发 UI 通知，默认 True（恢复历史时传 False 避免频繁刷新）
        """
        event_type = event.get("type")
        if event_type == "stream_request_start":
            if self.current_assistant is not None and self.current_assistant.completed:
                self._current_assistant_id = None
            self.is_generating = True
            self._runtime_phase = "stream_assistant"
        elif event_type == "content_block_start":
            message = self._ensure_assistant_message()
            block = event.get("content_block")
            block_type = _extract_attr(block, "type")
            if block_type == "thinking":
                message.thinking_collapsed = False
            elif block_type == "tool_use":
                tool_use_id = str(_extract_attr(block, "id", str(uuid4())))
                tool_name = str(_extract_attr(block, "name", "工具"))
                call = ToolCallSnapshot(tool_use_id=tool_use_id, name=tool_name)
                message.tool_calls.append(call)
                self._tool_owner[tool_use_id] = message.id
                self._tool_name[tool_use_id] = tool_name
        elif event_type == "text_delta":
            message = self._ensure_assistant_message()
            message.content += str(event.get("delta", {}).get("text", ""))
            if message.thinking:
                message.thinking_collapsed = True
            message.updated_at = _now()
        elif event_type == "thinking_delta":
            message = self._ensure_assistant_message()
            message.thinking += str(event.get("delta", {}).get("thinking", ""))
            if not message.content:
                message.thinking_collapsed = False
            message.updated_at = _now()
        elif event_type == "input_json_delta":
            message = self._ensure_assistant_message()
            if message.tool_calls:
                message.tool_calls[-1].input_preview += str(event.get("delta", {}).get("partial_json", ""))
                message.tool_calls[-1].updated_at = _now()
        elif event_type == "tool_started":
            tool_use_id = str(event.get("tool_use_id", "") or str(uuid4()))
            message = self._message_for_tool(tool_use_id) or self._ensure_assistant_message()
            call = self._tool_call_for_id(message, tool_use_id)
            if call is None:
                call = ToolCallSnapshot(
                    tool_use_id=tool_use_id,
                    name=str(event.get("tool_name", "") or self._tool_name.get(tool_use_id, "工具")),
                )
                message.tool_calls.append(call)
                self._tool_owner[tool_use_id] = message.id
            call.status = str(event.get("status", "") or "running")
            call.input_preview = str(event.get("input_preview", "") or call.input_preview)
            call.updated_at = _now()
            message.updated_at = _now()
        elif event_type == "tool_progress":
            tool_use_id = str(event.get("tool_use_id", "") or "")
            message = self._message_for_tool(tool_use_id) or self._ensure_assistant_message()
            call = self._tool_call_for_id(message, tool_use_id)
            if call is None:
                call = ToolCallSnapshot(
                    tool_use_id=tool_use_id or str(uuid4()),
                    name=str(event.get("tool_name", "") or "工具"),
                    status="running",
                )
                message.tool_calls.append(call)
                self._tool_owner[call.tool_use_id] = message.id
            progress = str(event.get("progress", "") or "")
            if progress:
                call.result = progress
            call.status = "running"
            call.updated_at = _now()
            message.updated_at = _now()
        elif event_type == "tool_completed":
            tool_use_id = str(event.get("tool_use_id", "") or "")
            message = self._message_for_tool(tool_use_id) or self._ensure_assistant_message()
            call = self._tool_call_for_id(message, tool_use_id)
            if call is None:
                call = ToolCallSnapshot(
                    tool_use_id=tool_use_id or str(uuid4()),
                    name=str(event.get("tool_name", "") or "工具"),
                )
                message.tool_calls.append(call)
                self._tool_owner[call.tool_use_id] = message.id
            call.status = str(event.get("status", "") or "completed")
            receipt = event.get("receipt")
            if isinstance(receipt, dict):
                call.receipt = receipt
            content = str(event.get("content", "") or "")
            if content:
                call.result = content
            raw_audit_events = event.get("audit_events", []) or []
            if raw_audit_events:
                call.audit_events = [item for item in raw_audit_events if isinstance(item, dict)]
            call.updated_at = _now()
            message.updated_at = _now()
        elif event_type == "tool_result":
            tool_use_id = str(event.get("tool_use_id", ""))
            message = self._message_for_tool(tool_use_id) or self._ensure_assistant_message()
            call = self._tool_call_for_id(message, tool_use_id)
            if call is None:
                call = ToolCallSnapshot(tool_use_id=tool_use_id, name=self._tool_name.get(tool_use_id, "工具"))
                message.tool_calls.append(call)
            call.result = str(event.get("content", ""))
            receipt = event.get("receipt")
            call.receipt = receipt if isinstance(receipt, dict) else None
            raw_audit_events = event.get("audit_events", []) or []
            call.audit_events = [item for item in raw_audit_events if isinstance(item, dict)]
            call.is_error = bool(event.get("is_error", False))
            call.status = str(event.get("status", "completed"))
            call.updated_at = _now()
            message.updated_at = _now()
            if isinstance(call.receipt, dict) and call.receipt.get("kind") == "agent":
                agent_id = str(call.receipt.get("agent_id", "") or "")
                if agent_id:
                    self._track_visible_agent(agent_id)
                    child = self._ensure_agent_child(
                        agent_id,
                        label=str(call.receipt.get("summary", "") or agent_id),
                        status=str(call.receipt.get("status", "") or "completed"),
                        agent_type=str(call.receipt.get("agent_type", "") or ""),
                        mode=str(call.receipt.get("mode", "") or ""),
                        task_id=str(call.receipt.get("task_id", "") or ""),
                        background=bool(call.receipt.get("background", False)),
                    )
                    preview = str(call.receipt.get("result_preview", "") or "")
                    if preview:
                        child.content = preview
                    child.total_tokens = int(call.receipt.get("total_tokens", 0) or 0)
                    child.audit_events.extend(call.audit_events[-3:])
                    child.audit_events = child.audit_events[-5:]
            if _tool_key(self._tool_name.get(tool_use_id, "")) == "todowrite":
                message.todo_summary = self._build_todo_summary(self.engine.session_id)
        elif event_type == "todo_updated":
            key = str(event.get("key", "") or "")
            if key:
                self._store_todo_items(key, event.get("items", []))
                summary = self._build_todo_summary(key)
                if key == self.engine.session_id:
                    message = self.current_assistant or self._ensure_assistant_message()
                    message.todo_summary = summary
                    message.updated_at = _now()
                else:
                    self._track_visible_agent(key)
                    child = self._ensure_agent_child(key, label=key)
                    child.todo_summary = summary
                    active = summary.active
                    if active is not None:
                        child.content = active.active_form or active.content
                    child.updated_at = _now()
        elif event_type == "agent_started":
            message = self._ensure_assistant_message()
            agent_id = str(event.get("agent_id", "") or "")
            if agent_id:
                self._track_visible_agent(agent_id)
                child = self._ensure_agent_child(
                    agent_id,
                    label=str(event.get("label", "") or agent_id),
                    status=str(event.get("status", "") or "running"),
                    agent_type=str(event.get("agent_type", "") or ""),
                    mode=str(event.get("mode", "") or ""),
                    task_id=str(event.get("task_id", "") or ""),
                    background=bool(event.get("background", False)),
                )
                child.content = str(event.get("content", "") or child.content)
                message.updated_at = _now()
        elif event_type == "agent_delta":
            agent_id = str(event.get("agent_id", "") or "")
            if agent_id:
                self._track_visible_agent(agent_id)
                child = self._ensure_agent_child(
                    agent_id,
                    status=str(event.get("status", "") or "active"),
                )
                child.thinking += str(event.get("thinking_delta", "") or "")
                delta = str(event.get("content_delta", "") or "")
                if delta:
                    child.content += delta
                child.updated_at = _now()
        elif event_type == "agent_tool_started":
            agent_id = str(event.get("agent_id", "") or "")
            if agent_id:
                self._track_visible_agent(agent_id)
                child = self._ensure_agent_child(agent_id, status="active")
                child.tool_calls.append(
                    ToolCallSnapshot(
                        tool_use_id=str(event.get("tool_use_id", "") or str(uuid4())),
                        name=str(event.get("tool_name", "") or "工具"),
                        status="running",
                        input_preview=str(event.get("input_preview", "") or ""),
                    )
                )
                child.updated_at = _now()
        elif event_type == "agent_tool_completed":
            agent_id = str(event.get("agent_id", "") or "")
            if agent_id:
                self._track_visible_agent(agent_id)
                child = self._ensure_agent_child(agent_id, status="active")
                tool_use_id = str(event.get("tool_use_id", "") or "")
                call = next((item for item in child.tool_calls if item.tool_use_id == tool_use_id), None)
                if call is None:
                    call = ToolCallSnapshot(
                        tool_use_id=tool_use_id or str(uuid4()),
                        name=str(event.get("tool_name", "") or "工具"),
                    )
                    child.tool_calls.append(call)
                call.result = str(event.get("content", "") or "")
                call.status = str(event.get("status", "") or "completed")
                receipt = event.get("receipt")
                call.receipt = receipt if isinstance(receipt, dict) else None
                raw_audit_events = event.get("audit_events", []) or []
                call.audit_events = [item for item in raw_audit_events if isinstance(item, dict)]
                call.is_error = bool(event.get("is_error", False)) or call.status == "error"
                call.updated_at = _now()
                child.updated_at = _now()
        elif event_type == "agent_completed":
            agent_id = str(event.get("agent_id", "") or "")
            if agent_id:
                self._track_visible_agent(agent_id)
                child = self._ensure_agent_child(
                    agent_id,
                    status=str(event.get("status", "") or "completed"),
                )
                result = str(event.get("result", "") or "")
                if result:
                    child.content = f"{child.content}\n{result}".strip()
                child.total_tokens = int(event.get("total_tokens", 0) or child.total_tokens)
                child.updated_at = _now()
        elif event_type == "agent_error":
            agent_id = str(event.get("agent_id", "") or "")
            if agent_id:
                self._track_visible_agent(agent_id, immediate=True)
                child = self._ensure_agent_child(agent_id, status="error")
                error_message = str(event.get("error", "") or "")
                if error_message:
                    child.content = f"{child.content}\n{error_message}".strip()
                child.updated_at = _now()
        elif event_type == "compact":
            result = event.get("result", {})
            self.add_toast(
                f"上下文已压缩：{result.get('pre_tokens', '?')} -> {result.get('post_tokens', '?')} 令牌",
                level="info",
            )
        elif event_type == "interaction_requested":
            request = _interaction_request_from_payload(event.get("request"))
            if request is not None:
                self._active_interaction = request
        elif event_type == "interaction_resolved":
            request_id = str(event.get("request_id", "") or "")
            if self._active_interaction is not None and self._active_interaction.request_id == request_id:
                self._active_interaction = None
        elif event_type == "status_changed":
            phase = str(event.get("phase", "") or "")
            if phase:
                self._runtime_phase = phase
            resume_target = str(event.get("resume_target", "") or "")
            self._runtime_resume_target = resume_target or None
            self._remember_retry_checkpoint(event)
        elif event_type == "checkpoint_restored":
            phase = str(event.get("phase", "") or "")
            if phase:
                self._runtime_phase = phase
            checkpoint_id = str(event.get("checkpoint_id", "") or "")
            if checkpoint_id:
                self._last_checkpoint_id = checkpoint_id
        elif event_type == "interrupt_ack":
            self._runtime_phase = "interrupted"
            checkpoint_id = str(event.get("checkpoint_id", "") or "")
            if checkpoint_id:
                self._retry_checkpoint_id = checkpoint_id
                self._last_checkpoint_id = checkpoint_id
        elif event_type == "turn_completed":
            self._runtime_phase = "complete"
            self._runtime_resume_target = None
        elif event_type == "sidebar_focus_changed":
            sidebar_mode = str(event.get("sidebar_mode", "") or "")
            if sidebar_mode:
                self.sidebar_mode = sidebar_mode
            if "auto_follow" in event:
                self.auto_follow = bool(event.get("auto_follow"))
        elif event_type == "error":
            error_type = str(event.get("error_type", ""))
            error_message = str(event.get("error", "未知错误"))
            user_message = _humanize_runtime_error(error_message, error_type)
            if error_type == "user_interrupted":
                message = self._ensure_assistant_message()
                message.interrupted = True
                message.completed = True
                self._runtime_phase = "interrupted"
            elif event.get("recoverable"):
                self.add_toast(user_message, level="warning", duration=4.0)
                self._runtime_phase = "error"
            else:
                self.add_info_message(user_message, level="error")
                self._runtime_phase = "error"
            self.is_generating = False
        elif event_type == "message_stop":
            message = self.current_assistant
            if message:
                message.completed = True
                if message.thinking:
                    message.thinking_collapsed = True
                message.updated_at = _now()
            self.is_generating = False
        if notify:
            self.notify(
                force=event_type
                not in {
                    "text_delta",
                    "input_json_delta",
                    "tool_progress",
                    "agent_delta",
                }
            )

    def finish_terminal(self, terminal: Any) -> None:
        """
        处理 query 循环的终止事件（Terminal 对象）。

        将生成状态设为 False，运行时阶段设为 complete，通知 UI 刷新。

        参数:
            terminal: Terminal 对象，包含 reason 和 metadata
        """
        self.is_generating = False
        self._runtime_phase = "complete"
        self._runtime_resume_target = None
        self.notify()

    def _message_for_tool(self, tool_use_id: str) -> Optional[MessageSnapshot]:
        """
        根据工具调用 ID 查找其所属的 assistant 消息快照。

        参数:
            tool_use_id: 工具调用唯一 ID

        返回:
            MessageSnapshot | None: 所属消息，未找到则返回 None
        """
        owner_id = self._tool_owner.get(tool_use_id)
        if owner_id is None:
            return None
        for message in self.messages:
            if message.id == owner_id:
                return message
        return None

    @staticmethod
    def _tool_call_for_id(message: MessageSnapshot, tool_use_id: str) -> Optional[ToolCallSnapshot]:
        """
        在消息的工具调用列表中查找指定 ID 的 ToolCallSnapshot。

        参数:
            message: 要搜索的消息快照
            tool_use_id: 工具调用唯一 ID

        返回:
            ToolCallSnapshot | None: 找到的工具调用快照，未找到则返回 None
        """
        for call in message.tool_calls:
            if call.tool_use_id == tool_use_id:
                return call
        return None

    def _get_app_state_todos(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        从 execution_context 中获取 todos 字典。

        返回:
            dict: session_id/agent_id 到 TODO 列表的映射，如：
                {
                    "sess_abc": [{"content": "实现登录", "status": "pending", "activeForm": ""}],
                    "agent_001": [{"content": "写测试", "status": "completed", "activeForm": ""}],
                }
        """
        options = self.engine.execution_context.get("options", {})
        app_state = options.get("app_state", {})
        todos = app_state.get("todos", {})
        if isinstance(todos, dict):
            return todos
        return {}

    def _store_todo_items(self, key: str, items: Any) -> None:
        """
        将 TODO 列表写入 execution_context["options"]["app_state"]["todos"]。

        参数:
            key: session_id 或 agent_id
            items: TODO 项列表，每项为字典，如：
                [{"content": "实现登录", "status": "pending", "activeForm": ""}]
        """
        if not key:
            return
        execution_context = getattr(self.engine, "execution_context", None)
        if not isinstance(execution_context, dict):
            return
        options = execution_context.setdefault("options", {})
        if not isinstance(options, dict):
            options = {}
            execution_context["options"] = options
        app_state = dict(options.get("app_state", {}) or {})
        todos = dict(app_state.get("todos", {}) or {})
        normalized_items = [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        todos[str(key)] = normalized_items
        app_state["todos"] = todos
        options["app_state"] = app_state

    def _build_todo_summary(self, key: str) -> TodoSummarySnapshot:
        """
        构建指定 key 的 TODO 摘要快照。

        [Workflow]
        1. 从 app_state 获取 todos 列表
        2. 转换为 TodoLineSnapshot 列表
        3. 若总数 > 4，折叠中间项（只保留最近完成、进行中、第一个待办）
        4. 返回 TodoSummarySnapshot

        参数:
            key: session_id 或 agent_id

        返回:
            TodoSummarySnapshot: TODO 摘要，如：
                TodoSummarySnapshot(
                    key="sess_abc",
                    items=[TodoLineSnapshot("实现登录", "in_progress", ""), ...],
                    completed_count=2,
                    total_count=5,
                    hidden_count=2,
                )
        """
        todos = self._get_app_state_todos().get(key, [])
        lines = [
            TodoLineSnapshot(
                content=str(item.get("content", "")),
                status=str(item.get("status", "pending")),
                active_form=str(item.get("activeForm", "")),
            )
            for item in todos
        ]
        total_count = len(lines)
        completed_count = sum(1 for item in lines if item.status == "completed")

        if total_count > 4:
            active_indices = [index for index, item in enumerate(lines) if item.status == "in_progress"]
            pending_indices = [index for index, item in enumerate(lines) if item.status == "pending"]
            completed_indices = [index for index, item in enumerate(lines) if item.status == "completed"]
            keep = set()
            if completed_indices:
                keep.add(completed_indices[-1])
            keep.update(active_indices)
            if pending_indices:
                keep.add(pending_indices[0])
            compact_lines = [item for index, item in enumerate(lines) if index in keep]
            hidden_count = total_count - len(compact_lines)
            return TodoSummarySnapshot(
                key=key,
                items=compact_lines,
                completed_count=completed_count,
                total_count=total_count,
                hidden_count=hidden_count,
            )

        return TodoSummarySnapshot(
            key=key,
            items=lines,
            completed_count=completed_count,
            total_count=total_count,
        )

    @staticmethod
    def _todo_summary_snippet(summary: Optional[TodoSummarySnapshot]) -> str:
        """
        从 TODO 摘要中提取最值得展示的单行文本。

        优先级：进行中任务 > 待办任务 > 完成进度 > 第一项内容

        参数:
            summary: TODO 摘要快照，None 时返回 ""

        返回:
            str: 单行摘要文本，如 "实现登录功能" 或 "已完成 3/5"
        """
        if summary is None or not summary.items:
            return ""
        active = summary.active
        if active is not None:
            if active.status == "in_progress":
                return _friendly_task_preview(active.active_form or active.content)
            if active.status == "pending":
                return _friendly_task_preview(active.content)
        if summary.completed_count:
            return f"已完成 {summary.completed_count}/{summary.total_count}"
        return _friendly_task_preview(summary.items[0].content)

    def _latest_session_activity_snippet(self) -> str:
        """
        获取当前会话最新活动的单行摘要文本，用于侧边栏"当前会话"区域展示。

        优先级：活动交互 > TODO 进度 > 正在运行的工具 > 最后完成的工具 > 消息内容 > 运行时状态
        """
        if self._active_interaction is not None:
            return _friendly_task_preview(
                self._active_interaction.label,
                self._active_interaction.message,
            )

        assistant = self.current_assistant
        if assistant is None:
            assistant = next((message for message in reversed(self.messages) if message.role == "assistant"), None)
        if assistant is None:
            return self._runtime_sub_status() or ""

        todo_snippet = self._todo_summary_snippet(assistant.todo_summary)
        if todo_snippet:
            return todo_snippet

        running_tool = next(
            (
                call for call in reversed(assistant.tool_calls)
                if str(call.status or "").lower() in {"starting", "running"}
            ),
            None,
        )
        if running_tool is not None:
            return _friendly_task_preview(running_tool.summary)

        last_tool = next((call for call in reversed(assistant.tool_calls) if call.summary), None)
        if last_tool is not None:
            return _friendly_task_preview(last_tool.summary)

        return _friendly_task_preview(
            assistant.content,
            assistant.thinking,
            self._runtime_sub_status() or "",
        )

    def _build_agents(self) -> List[AgentSnapshot]:
        """
        构建当前所有协作代理的快照列表，用于侧边栏代理区域展示。

        [Workflow]
        1. 从 TaskManager 获取所有任务，从消息历史中收集子代理快照
        2. 合并 todos 中的代理 key，构建代理名册（roster）
        3. 对每个代理，综合任务状态和子代理快照确定 status 和 current_task
        4. 按更新时间倒序排列

        返回:
            List[AgentSnapshot]: 代理快照列表，如：
                [
                    AgentSnapshot(agent_id="agent_001", label="执行代理", status="active",
                                  current_task="正在写测试", updated_at=1700000000.0),
                ]
        """
        tasks = list(self._task_manager.get_all_tasks())
        todos = self._get_app_state_todos()
        child_index: Dict[str, AgentChildSnapshot] = {}
        for message in self.messages:
            for child in message.agent_children:
                current = child_index.get(child.agent_id)
                if current is None or child.updated_at >= current.updated_at:
                    child_index[child.agent_id] = child
        roster: List[str] = []
        for task in tasks:
            agent_id = getattr(task, "agent_id", "")
            if agent_id and agent_id not in roster:
                roster.append(agent_id)
        for key in todos.keys():
            if key != self.engine.session_id and key not in roster:
                roster.append(key)
        for agent_id in child_index.keys():
            if agent_id not in roster:
                roster.append(agent_id)

        agents: List[AgentSnapshot] = []
        for agent_id in roster:
            agent_tasks = [task for task in tasks if getattr(task, "agent_id", "") == agent_id]
            latest_task = sorted(agent_tasks, key=lambda task: getattr(task, "completed_at", None) or getattr(task, "started_at", None) or getattr(task, "created_at", 0), reverse=True)
            running_task = next((task for task in agent_tasks if getattr(task, "status", None) == TaskStatus.RUNNING), None)
            latest = running_task or (latest_task[0] if latest_task else None)
            child = child_index.get(agent_id)

            status = "idle"
            current_task = ""
            updated_at = _now()
            if latest is not None:
                latest_updated_at = (
                    getattr(latest, "completed_at", None)
                    or getattr(latest, "started_at", None)
                    or getattr(latest, "created_at", 0)
                )
                child_is_fresher = child is not None and child.updated_at >= float(latest_updated_at or 0.0)
                current_task = _friendly_task_preview(
                    child.content if child_is_fresher and child is not None else "",
                    child.thinking if child_is_fresher and child is not None else "",
                    str(getattr(latest, "current_action", None) or ""),
                    str(getattr(latest, "description", "") or ""),
                    child.content if child is not None else "",
                    child.thinking if child is not None else "",
                )
                updated_at = latest_updated_at or _now()
                latest_status = getattr(latest, "status", None)
                if latest_status == TaskStatus.RUNNING:
                    status = "active"
                elif latest_status == TaskStatus.FAILED:
                    status = "error"
                elif latest_status == TaskStatus.CANCELLED:
                    status = "idle"
            elif child is not None:
                current_task = _friendly_task_preview(
                    child.content.strip(),
                    child.thinking.strip(),
                    child.task_id,
                    child.label,
                )
                updated_at = child.updated_at
                child_status = child.status.lower().strip()
                if child_status in {"error", "failed"}:
                    status = "error"
                elif child_status in {"waiting"}:
                    status = "waiting"
                elif child_status in {"thinking"}:
                    status = "thinking"
                elif child_status in {"completed", "done", "idle"}:
                    status = "idle"
                else:
                    status = "active"

            todo_summary = self._build_todo_summary(agent_id)
            if todo_summary.items and status == "idle":
                active = todo_summary.active
                if active and active.status == "in_progress":
                    status = "active"
                    current_task = _friendly_task_preview(active.active_form or active.content)

            label = _friendly_agent_label(
                agent_id,
                child.label if child is not None else "",
                child.agent_type if child is not None else "",
            )

            if self._active_interaction and (
                agent_id == self._last_visible_agent_id or (not self._last_visible_agent_id and status == "active")
            ):
                status = "waiting"
            elif (
                self.is_generating
                and self.current_assistant is not None
                and self.current_assistant.thinking
                and not self.current_assistant.content.strip()
                and status == "active"
            ):
                status = "thinking"

            agents.append(
                AgentSnapshot(
                    agent_id=agent_id,
                    label=label,
                    status=status,
                    agent_type=child.agent_type if child is not None else "",
                    current_task=current_task,
                    todo_summary=todo_summary,
                    updated_at=updated_at,
                )
            )

        agents.sort(key=lambda agent: agent.updated_at, reverse=True)
        return agents

    def _resolve_active_entity(self, agents: List[AgentSnapshot]) -> tuple[str, str]:
        """
        根据侧边栏模式和自动跟随状态，确定当前活动实体的标签和任务摘要。

        [Workflow]
        1. sidebar_mode == "global"：返回当前会话的 TODO 进度和活动摘要
        2. sidebar_mode == "agent:xxx"：返回指定代理的标签和当前任务
        3. auto_follow 开启时：跟随最近活跃的代理
        4. 兜底：返回当前会话信息

        参数:
            agents: 当前代理快照列表

        返回:
            tuple[str, str]: (活动实体标签, 任务摘要文本)
        """
        global_todos = self._build_todo_summary(self.engine.session_id)
        session_activity = self._latest_session_activity_snippet()
        if self.sidebar_mode == "global":
            active = global_todos.active
            return (
                "当前会话",
                _friendly_task_preview(active.active_form or active.content if active else "", session_activity)
                or "最近的处理进展会显示在这里",
            )

        if self.sidebar_mode.startswith("agent:"):
            agent_id = self.sidebar_mode.split(":", 1)[1]
            for agent in agents:
                if agent.agent_id == agent_id:
                    return (agent.label, agent.current_task or "这位协作成员暂时没有新的进展")
            return (
                "当前会话",
                _friendly_task_preview(
                    global_todos.active.content if global_todos.active else "",
                    session_activity,
                )
                or "最近的处理进展会显示在这里",
            )

        agent = None
        if self.auto_follow and self._last_visible_agent_id:
            for candidate in agents:
                if candidate.agent_id == self._last_visible_agent_id:
                    agent = candidate
                    break

        if agent is not None:
            return (agent.label, agent.current_task or "这位协作成员暂时没有新的进展")

        active = global_todos.active
        return (
            "当前会话",
            _friendly_task_preview(active.active_form or active.content if active else "", session_activity)
            or "最近的处理进展会显示在这里",
        )

    def set_sidebar_global(self) -> None:
        """将侧边栏焦点切换到全局会话视图（等同于 set_sidebar_focus("global")）。"""
        self.set_sidebar_focus("global")

    def toggle_auto_follow(self) -> None:
        """切换自动跟随模式：开启时切换到 global，关闭时切换到 auto。"""
        self.set_sidebar_focus("global" if self.auto_follow else "auto")

    def select_agent(self, index: int) -> None:
        """
        按索引选择侧边栏代理（对应数字键 1-9）。

        参数:
            index: 代理列表中的 0-based 索引
        """
        agents = self._build_agents()
        if 0 <= index < len(agents):
            self.set_sidebar_focus(f"agent:{agents[index].agent_id}")

    def cycle_sidebar(self, direction: int) -> None:
        """
        循环切换侧边栏焦点（对应 [ 和 ] 快捷键）。

        参数:
            direction: 1 表示向前，-1 表示向后
        """
        agents = self._build_agents()
        modes = ["global", "auto"] + [f"agent:{agent.agent_id}" for agent in agents]
        current = self.sidebar_mode if not self.auto_follow else "auto"
        if current not in modes:
            current = "auto"
        index = modes.index(current)
        next_mode = modes[(index + direction) % len(modes)]
        if next_mode == "auto":
            self.set_sidebar_focus("auto")
        else:
            self.set_sidebar_focus(next_mode)

    def _send_runtime_control(self, command_type: str, **payload: Any) -> None:
        """
        向引擎发送运行时控制命令（如切换侧边栏焦点）。

        参数:
            command_type: 命令类型，如 "switch_sidebar_focus"
            **payload: 命令附加参数
        """
        send_control = getattr(self.engine, "send_control", None)
        if callable(send_control):
            try:
                send_control({"type": command_type, **payload})
            except Exception:
                pass

    def _resolve_current_agent_id(self) -> Optional[str]:
        """
        解析当前应该聚焦的代理 ID。

        [Workflow]
        1. 提升待定的可见代理
        2. 若 sidebar_mode 指定了代理，直接返回
        3. 若有最近可见代理，返回它
        4. 若 auto_follow 开启，返回最活跃的代理
        5. 兜底返回第一个代理

        返回:
            str | None: 代理 ID，无代理时返回 None
        """
        self._promote_pending_visible_agent()
        if self.sidebar_mode.startswith("agent:"):
            return self.sidebar_mode.split(":", 1)[1]
        if self._last_visible_agent_id:
            return self._last_visible_agent_id
        agents = self._build_agents()
        if not agents:
            return None
        if self.auto_follow:
            active = next((agent for agent in agents if agent.status in {"thinking", "waiting", "active"}), None)
            if active is not None:
                return active.agent_id
        return agents[0].agent_id

    def set_sidebar_focus(self, target: str, *, source: str = "ui") -> None:
        """
        设置侧边栏焦点视角，并同步通知引擎和 UI。

        [Workflow]
        1. 规范化 target 字符串
        2. "" / "auto"：开启自动跟随，sidebar_mode = "auto"
        3. "global"：关闭自动跟随，sidebar_mode = "global"
        4. "current"：解析当前活跃代理 ID，切换到该代理；无代理时切换到 global
        5. "agent:xxx"：直接切换到指定代理
        6. 其他字符串：视为代理 ID，自动加 "agent:" 前缀
        7. 发送 switch_sidebar_focus 控制命令到引擎
        8. 通知 UI 刷新

        参数:
            target: 目标视角标识，如 ""、"auto"、"global"、"current"、"agent:abc123"
            source: 变更来源，如 "ui"、"command"
        """
        normalized = str(target or "").strip()
        if normalized in {"", "auto"}:
            self.auto_follow = True
            self.sidebar_mode = "auto"
        elif normalized == "global":
            self.auto_follow = False
            self.sidebar_mode = "global"
        elif normalized == "current":
            agent_id = self._resolve_current_agent_id()
            if agent_id:
                self.auto_follow = False
                self.sidebar_mode = f"agent:{agent_id}"
            else:
                self.auto_follow = False
                self.sidebar_mode = "global"
        elif normalized.startswith("agent:"):
            self.auto_follow = False
            self.sidebar_mode = normalized
        else:
            self.auto_follow = False
            self.sidebar_mode = f"agent:{normalized}"
        self._send_runtime_control(
            "switch_sidebar_focus",
            sidebar_mode=self.sidebar_mode,
            auto_follow=self.auto_follow,
            source=source,
        )
        self.notify()

    def interrupt_generation(self) -> None:
        """
        中断当前正在进行的生成。

        [Workflow]
        1. 检查是否正在生成，若否则直接返回
        2. 调用 engine.interrupt() 发送中断信号
        3. 标记当前 assistant 消息为已中断
        4. 设置 is_generating = False
        5. 显示中断 Toast 通知
        """
        if not self.is_generating:
            return
        self.engine.interrupt()
        message = self.current_assistant
        if message:
            message.interrupted = True
            message.completed = True
        self.is_generating = False
        self.add_toast("已中断当前生成", level="warning")
        self.notify()

    async def retry_last_turn(self) -> None:
        """
        重试上一轮对话。

        [Workflow]
        1. 优先尝试从 checkpoint 恢复（精确回退到上次状态）
        2. 若 checkpoint 不可用，回退到使用 last_retry_prompt 重新提交
        3. 若都不可用，静默返回
        """
        checkpoint_id = self._retry_checkpoint_id
        if checkpoint_id:
            retry_checkpoint = getattr(self.engine, "retry_checkpoint", None)
            restored = None
            if callable(retry_checkpoint):
                restored = retry_checkpoint(checkpoint_id)
            if restored is not None:
                self._reload_messages_from_engine()
                await self._stream_engine("", begin_user_turn=False)
                return
            submit_stream = getattr(self.engine, "submit_message_stream", None)
            if callable(submit_stream):
                await self._stream_engine("", checkpoint_id=checkpoint_id, begin_user_turn=False)
                return
        if self.last_retry_prompt:
            await self.submit_prompt(self.last_retry_prompt)

    async def submit_prompt(self, prompt: str) -> None:
        """
        提交用户消息并启动流式生成。

        参数:
            prompt: 用户输入的消息文本
        """
        await self._stream_engine(prompt, begin_user_turn=True)

    async def _stream_engine(
        self,
        prompt: str,
        *,
        checkpoint_id: Optional[str] = None,
        begin_user_turn: bool,
    ) -> None:
        """
        核心流式引擎驱动方法。

        [Workflow]
        1. 重置中断状态，取消活动交互
        2. 若 begin_user_turn 为 True，追加用户消息并重置状态
        3. 设置 is_generating = True
        4. 调用 engine.submit_message_stream() 获取事件流
        5. 逐条事件调用 apply_stream_event() 更新 UI 状态
        6. 遇到 Terminal 对象时调用 finish_terminal()
        7. 异常时显示错误信息

        参数:
            prompt: 用户消息文本（空字符串表示重试/恢复）
            checkpoint_id: 要恢复的检查点 ID（可选）
            begin_user_turn: 是否开始新的用户轮次
        """
        if begin_user_turn and not prompt.strip():
            return
        self.engine.reset_interrupt_state()
        self.cancel_interaction()
        if begin_user_turn:
            self.begin_user_turn(prompt)
        self.is_generating = True
        self.notify()

        try:
            submit_stream = getattr(self.engine, "submit_message_stream")
            try:
                stream = submit_stream(prompt, checkpoint_id=checkpoint_id)
            except TypeError:
                stream = submit_stream(prompt)
            async for event in stream:
                if isinstance(event, dict):
                    self.apply_stream_event(event)
                else:
                    self.finish_terminal(event)
        except asyncio.CancelledError:
            self.is_generating = False
            self.notify()
            return
        except Exception as exc:
            self.is_generating = False
            self.add_info_message(self.format_runtime_error(exc), level="error")
            self.notify()
            return

        self.is_generating = False
        self.notify()

    def build_status(self) -> StatusSnapshot:
        """
        构建状态栏快照，包含模型名、token 用量、权限模式等。

        [Workflow]
        1. 获取上下文统计（带缓存）
        2. 根据当前状态（生成中/等待输入/空闲/中断）确定顶部状态文本
        3. 计算 token 用量和剩余量
        4. 组装 StatusSnapshot 返回

        返回:
            StatusSnapshot: 状态栏数据，如：
                StatusSnapshot(
                    model_name="claude-opus-4-20250514",
                    top_status="🟢 处理中",
                    sub_status="正在流式输出",
                    permission_mode="询问",
                    token_count=3200,
                    context_window=200000,
                    effective_context_window=160000,
                    remaining_tokens=156800,
                    model_visible_message_count=12,
                    session_message_count=24,
                    session_title="排序算法讨论",
                )
        """
        stats = self._get_context_stats()
        current_assistant = self.current_assistant
        permission_state = self.get_permission_mode_state()
        permission_mode = str(permission_state["display_label"])
        if self._active_interaction is not None:
            top_status = "🟡 等待输入"
            sub_status = self._active_interaction.label or "等待输入"
        elif (
            self.is_generating
            and current_assistant is not None
            and current_assistant.thinking
            and not current_assistant.content.strip()
        ):
            top_status = "🔵 思考中"
            sub_status = "正在思考这个请求"
        elif self.is_generating and self._runtime_phase == "wait_interaction":
            top_status = "🟡 等待输入"
            sub_status = self._runtime_sub_status() or "等待输入"
        elif self.is_generating:
            top_status = "🟢 处理中"
            sub_status = self._runtime_sub_status() or "正在流式输出"
        elif current_assistant is not None and current_assistant.interrupted:
            top_status = "⏸ 已中断"
            sub_status = "可重试"
        else:
            top_status = "⚪ 空闲"
            sub_status = "已就绪"
        token_count = int(stats.get("token_count", 0))
        effective_window = int(
            stats.get("effective_context_window", 0) or stats.get("context_window", 0) or 0
        )
        remaining_tokens = int(
            stats.get("remaining_tokens", max(0, effective_window - token_count))
        )
        return StatusSnapshot(
            model_name=str(getattr(self.engine, "model", "未知模型")),
            session_title=self._derive_session_title(),
            top_status=top_status,
            sub_status=sub_status,
            permission_mode=permission_mode,
            token_count=token_count,
            context_window=int(stats.get("context_window", 0)),
            effective_context_window=effective_window,
            remaining_tokens=remaining_tokens,
            model_visible_message_count=int(stats.get("model_visible_message_count", 0)),
            session_message_count=int(stats.get("session_message_count", 0)),
        )

    def _context_stats_cache_key(self) -> tuple[Any, ...]:
        """
        生成上下文统计的缓存 key，用于判断是否需要重新获取统计数据。

        key 由会话 ID、消息数量、生成状态等关键字段组成，
        任意字段变化都会导致缓存失效。

        返回:
            tuple: 缓存 key，如 ("sess_abc", 12, "msg_001", True, False, True, "execute_tools", None, 3)
        """
        current_assistant = self.current_assistant
        return (
            getattr(self.engine, "session_id", ""),
            len(self.messages),
            self._current_assistant_id,
            bool(current_assistant.completed) if current_assistant is not None else True,
            bool(current_assistant.interrupted) if current_assistant is not None else False,
            self.is_generating,
            self._runtime_phase,
            self._active_interaction.request_id if self._active_interaction is not None else None,
            getattr(self.engine, "turn_count", None),
        )

    def _get_context_stats(self) -> Dict[str, Any]:
        """
        获取上下文统计数据（带 TTL 缓存）。

        生成中时 TTL 为 0.4s，空闲时 TTL 为 1.5s，避免频繁调用引擎统计接口。

        返回:
            dict: 上下文统计字典，如：
                {
                    "token_count": 3200,
                    "context_window": 200000,
                    "effective_context_window": 160000,
                    "remaining_tokens": 156800,
                    "model_visible_message_count": 12,
                    "session_message_count": 24,
                }
        """
        cache_key = self._context_stats_cache_key()
        now = time.monotonic()
        ttl = 0.4 if self.is_generating else 1.5
        if (
            self._cached_context_stats is not None
            and self._cached_context_stats_key == cache_key
            and (now - self._cached_context_stats_at) < ttl
        ):
            return dict(self._cached_context_stats)

        stats = dict(self.engine.get_context_stats() or {})
        self._cached_context_stats = dict(stats)
        self._cached_context_stats_key = cache_key
        self._cached_context_stats_at = now
        return stats

    def get_snapshot(self) -> UISnapshot:
        """
        构建当前完整的 UI 快照，供 Textual App 渲染。

        [Workflow]
        1. 清理过期 Toast
        2. 提升待定的可见代理
        3. 构建代理列表和全局 TODO 摘要
        4. 解析当前活动实体（侧边栏焦点）
        5. 组装 UISnapshot 返回

        返回:
            UISnapshot: 包含 messages、status、agents、todos、toasts 等完整 UI 状态
        """
        self.prune_toasts()
        self._promote_pending_visible_agent()
        agents = self._build_agents()
        global_todos = self._build_todo_summary(self.engine.session_id)
        active_label, active_task = self._resolve_active_entity(agents)
        return UISnapshot(
            messages=list(self.messages),
            status=self.build_status(),
            global_todos=global_todos,
            agents=agents,
            active_entity_label=active_label,
            active_task_snippet=active_task,
            is_generating=self.is_generating,
            sidebar_mode=self.sidebar_mode,
            auto_follow=self.auto_follow,
            toasts=list(self._toasts),
            last_retry_prompt=self.last_retry_prompt,
            interaction=self._active_interaction,
        )

    def format_runtime_error(self, error: Any, error_type: str = "") -> str:
        """
        将运行时异常格式化为用户友好的中文错误信息。

        参数:
            error: 异常对象或错误字符串
            error_type: 错误类型标识

        返回:
            str: 用户友好的中文错误提示
        """
        return _humanize_runtime_error(str(error or ""), error_type)

    def _emit_snapshot(self) -> None:
        """立即构建快照并推送给所有监听器。"""
        self._notify_handle = None
        self._last_notify_monotonic = time.monotonic()
        snapshot = self.get_snapshot()
        for listener in list(self._listeners):
            listener(snapshot)

    def notify(self, *, force: bool = True) -> None:
        """
        通知 UI 刷新。

        参数:
            force: True 时立即推送，False 时按 STREAM_NOTIFY_INTERVAL 节流
                   （流式文本增量事件用 False 避免过度刷新）
        """
        if force:
            if self._notify_handle is not None:
                self._notify_handle.cancel()
                self._notify_handle = None
            self._emit_snapshot()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._emit_snapshot()
            return

        if self._notify_handle is not None:
            return

        elapsed = time.monotonic() - self._last_notify_monotonic
        delay = max(0.0, self.STREAM_NOTIFY_INTERVAL - elapsed)
        if delay <= 0:
            self._emit_snapshot()
            return

        self._notify_handle = loop.call_later(delay, self._emit_snapshot)
