"""
Attachment 处理模块

本模块负责收集和处理各种附件消息，包括：
- queued_command: 队列中的命令
- ide_selection: IDE 中的选择
- opened_file_in_ide: IDE 中打开的文件
- memory: 相关记忆文件（需要 memory 系统支持）
- plan: Plan Mode 提醒
- todo: TODO 提醒
- diagnostics: 诊断信息

当前实现：
- 支持基础附件类型（queued_command, ide_selection）
- memory prefetch 已完整实现（基于关键词匹配）
- filter_duplicate_memory_attachments 已完整实现
- 跳过 skill discovery（需要 skill 系统）
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

# 模块级日志记录器，用于调试和错误追踪
logger = logging.getLogger(__name__)

# ============================================================================
# 附件类型定义
# ============================================================================

def create_attachment_message(attachment: Dict[str, Any]) -> Dict[str, Any]:
    """
    创建附件消息

    [Workflow]
    1. 接收标准化后的附件数据；
    2. 包装为统一的"附件消息"结构；
    3. 交由上游 query 循环决定是否注入模型上下文。

    Args:
        attachment: 附件数据

    Returns:
        Dict[str, Any]: 附件消息
    """
    # 统一附件消息壳结构，避免不同附件类型在上层分支处理。
    return {
        "type": "attachment",      # 标记该消息为"附件类消息"
        "attachment": attachment,  # 存放附件有效载荷（payload）
    }

# ============================================================================
# 附件收集函数
# ============================================================================

async def get_attachment_messages(
    messages: List[Dict[str, Any]],
    turn_count: int,
    context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    收集所有附件消息

    [Workflow]
    1. 收集 queued_command 附件
    2. 收集 IDE selection 附件
    3. 收集 plan mode 提醒（如果在 plan mode）
    4. 收集 todo 提醒（如果有未完成任务）
    5. 过滤重复附件

    注：memory 预取在 query.py 中通过 start_relevant_memory_prefetch() 完成。

    Args:
        messages: 当前消息历史
        turn_count: 当前轮次
        context: 执行上下文（包含 mode, session_id 等）

    Returns:
        List[Dict[str, Any]]: 附件消息列表
    """
    attachments = []  # 聚合本轮附件，后续统一转换为附件消息。

    if context is None:
        context = {}  # 防御式兜底，确保后续 context.get() 不会抛异常。

    # ========================================================================
    # 1. Queued Command 附件：把待执行命令显式注入本轮上下文，避免命令意图丢失。
    # ========================================================================
    queued_commands = context.get("queued_commands", [])
    if queued_commands:
        for cmd in queued_commands:
            # 将每条排队命令转为结构化附件，便于后续模型理解"命令来源与语义"。
            attachments.append({
                "type": "queued_command",                       # 附件子类型：排队命令
                "prompt": cmd.get("prompt", ""),               # 命令文本
                "source_uuid": cmd.get("uuid"),                # 来源消息 UUID（用于追踪）
                "origin": cmd.get("origin", {"kind": "user"}), # 命令来源元信息
                "isMeta": cmd.get("isMeta", False),            # 是否为元命令
            })

    # ========================================================================
    # 2. IDE Selection 附件：把编辑器当前选区/文件状态补给模型，减少额外 Read 调用。
    # ========================================================================
    ide_selection = context.get("ide_selection")
    if ide_selection:
        file_path = ide_selection.get("filePath")  # 当前 IDE 文件路径（可能为 None）
        text = ide_selection.get("text")           # 选中文本（可能为空）

        if text and file_path:
            # 场景 A：有选区文本，优先注入高置信度"用户关注片段"。
            attachments.append({
                "type": "ide_selection",                      # 子类型：选区附件
                "filename": file_path,                        # 选区所属文件
                "text": text,                                 # 选区内容
                "startLine": ide_selection.get("startLine"),  # 选区起始行
                "endLine": ide_selection.get("endLine"),      # 选区结束行
            })
        elif file_path:
            # 场景 B：只有打开文件，无选区；至少注入文件焦点信息。
            attachments.append({
                "type": "opened_file_in_ide",  # 子类型：仅打开文件
                "filename": file_path,         # 当前活跃文件路径
            })

    # ========================================================================
    # 3. Plan Mode 提醒：在计划模式中周期性提醒，防止模型提前进入编码执行。
    # ========================================================================
    mode = context.get("mode")  # 读取当前运行模式（例如 plan/work 等）
    if mode == "plan":
        # 每 5 轮提醒一次
        from codo.services.attachments_config import PLAN_MODE_ATTACHMENT_CONFIG
        if turn_count % PLAN_MODE_ATTACHMENT_CONFIG["TURNS_BETWEEN_ATTACHMENTS"] == 0:
            # 每 5 次提醒中有 1 次是完整提醒
            is_full_reminder = (
                turn_count // PLAN_MODE_ATTACHMENT_CONFIG["TURNS_BETWEEN_ATTACHMENTS"]
            ) % PLAN_MODE_ATTACHMENT_CONFIG["FULL_REMINDER_EVERY_N_ATTACHMENTS"] == 0

            if is_full_reminder:
                attachments.append({
                    "type": "plan_mode_reminder",  # 子类型：计划模式提醒
                    "full": True,                  # 完整提醒（包含更强约束）
                })
            else:
                attachments.append({
                    "type": "plan_mode_reminder",  # 子类型：计划模式提醒
                    "full": False,                 # 轻量提醒（降低噪声）
                })

    # ========================================================================
    # 4. TODO 提醒：注入当前任务列表状态，帮助模型保持进度感知
    # ========================================================================
    app_state = context.get("app_state") or {}
    todos_dict = app_state.get("todos") or {}
    session_id = context.get("session_id", "default")
    session_todos = todos_dict.get(session_id, [])
    if session_todos:
        in_progress = [t for t in session_todos if t.get("status") == "in_progress"]
        pending = [t for t in session_todos if t.get("status") == "pending"]
        if in_progress or pending:
            attachments.append({
                "type": "todo_reminder",
                "in_progress": in_progress,
                "pending": pending,
            })

    # ========================================================================
    # 5. 过滤重复附件
    # ========================================================================

    # 将附件载荷转换为统一消息格式，便于 query 循环逐条产出。
    attachment_messages = [
        create_attachment_message(att) for att in attachments
    ]

    if attachment_messages and logger.isEnabledFor(logging.DEBUG):
        # 仅在 DEBUG 级别记录收集量，避免常规日志噪声。
        logger.debug(
            f"[attachments] Collected {len(attachment_messages)} attachments "
            f"for turn {turn_count}"
        )

    # 返回本轮收集到的全部附件消息；可能为空列表。
    return attachment_messages

def filter_duplicate_memory_attachments(
    attachments: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    过滤重复的 memory 附件

    [Workflow]
    1. 遍历消息历史，收集所有已注入的 memory 附件文件路径
    2. 若历史中无任何 memory 附件，直接返回原列表（快速路径）
    3. 遍历候选附件列表，跳过路径已在历史中出现的 memory 附件
    4. 非 memory 类型的附件不受影响，直接保留
    5. 返回去重后的附件列表

    Args:
        attachments: 待过滤的候选附件列表（本轮新生成的）
        messages: 当前完整消息历史（用于检测已注入的 memory）

    Returns:
        List[Dict[str, Any]]: 过滤掉重复 memory 后的附件列表
    """
    # 步骤 1：遍历消息历史，收集所有已注入过的 memory 文件路径
    # 消息历史中的 memory 附件结构：
    #   {"type": "attachment", "attachment": {"type": "memory", "path": "..."}}
    already_injected_paths: set = set()
    for msg in messages:
        # 只处理附件类型的消息，跳过普通 user/assistant 消息
        if msg.get("type") != "attachment":
            continue
        # 取出内层附件载荷（attachment 字段）
        inner_att = msg.get("attachment", {})
        # 只关心 memory 类型的附件，其他类型（ide_selection 等）不参与去重
        if inner_att.get("type") != "memory":
            continue
        # 提取文件路径并加入已注入集合
        path = inner_att.get("path")
        if path:
            already_injected_paths.add(path)  # 记录已注入路径，用于后续去重判断

    if not already_injected_paths:
        # 历史中无任何 memory 附件，无需过滤，直接返回原列表（快速路径）
        return attachments

    # 步骤 2：过滤候选附件，跳过路径已在历史中出现的 memory 附件
    filtered: List[Dict[str, Any]] = []
    for att in attachments:
        # 只对附件消息格式做去重检查（type == "attachment"）
        if att.get("type") == "attachment":
            inner = att.get("attachment", {})
            # 只对 memory 类型附件做去重，其他类型直接保留
            if inner.get("type") == "memory":
                path = inner.get("path")
                if path and path in already_injected_paths:
                    # 该 memory 文件已在历史中注入过，跳过以避免重复注入
                    logger.debug(f"[attachments] 过滤重复 memory 附件: {path}")
                    continue  # 跳过此附件，不加入 filtered 列表
        # 非 memory 附件或路径未重复，保留到结果列表
        filtered.append(att)

    return filtered

# ============================================================================
# Memory Prefetch
# ============================================================================

class MemoryPrefetch:
    """
    Memory 预取句柄

    [Workflow]
    使用 context manager（Disposable）pattern：
    - __enter__：返回自身，供 with 语句绑定
    - __exit__：退出时取消未完成的预取任务，释放资源

    字段说明：
    - task: asyncio.Task 句柄，持有后台预取协程
    - settled_at: 预取完成时间戳（秒），None 表示尚未完成
    - consumed_on_iteration: 首次消费时的迭代轮次，-1 表示未消费
    """

    def __init__(self):
        # asyncio.Task 句柄，持有后台预取协程（None 表示未启动）
        self.task: Optional[asyncio.Task] = None
        # 预取完成时间戳（秒），None 表示尚未完成
        self.settled_at: Optional[float] = None
        # 首次消费时的迭代轮次，-1 表示尚未被消费
        self.consumed_on_iteration: int = -1

    def __enter__(self) -> "MemoryPrefetch":
        # 进入上下文，返回自身供 with 语句绑定
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # 退出上下文时，若后台任务仍在运行则取消，防止资源泄漏
        if self.task is not None and not self.task.done():
            self.task.cancel()  # 取消未完成的预取任务

def start_relevant_memory_prefetch(
    messages: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> Optional[MemoryPrefetch]:
    """
    启动相关记忆预取

    [Workflow]
    1. 从 context 中获取工作目录（cwd），无则返回 None
    2. 从消息历史中找到最后一条非 meta 的用户消息
    3. 提取该消息的文本内容作为查询字符串
    4. 若查询为单词（无空白字符），跳过预取（上下文不足）
    5. 获取 memory 目录路径
    6. 收集本会话已展示过的 memory 文件路径（用于去重）
    7. 创建 MemoryPrefetch 句柄，启动异步预取任务
    8. 返回句柄供调用方 await 结果

    本实现使用 asyncio.Task + context manager 替代。

    Args:
        messages: 当前消息历史
        context: 执行上下文（包含 cwd、session_id 等）

    Returns:
        Optional[MemoryPrefetch]: 预取句柄；若不需要预取则返回 None
    """
    # 步骤 1：获取工作目录，无 cwd 则无法定位 memory 目录，直接返回 None
    cwd = context.get("cwd", "")
    if not cwd:
        return None

    # 步骤 2：从消息历史中找到最后一条非 meta 的用户消息
    # 倒序遍历，找到第一条 role == "user" 且 isMeta != True 的消息
    last_user_message: Optional[Dict[str, Any]] = None
    for msg in reversed(messages):
        if msg.get("role") == "user" and not msg.get("isMeta", False):
            last_user_message = msg
            break  # 找到最后一条用户消息，停止遍历

    if not last_user_message:
        # 无用户消息，无法构建查询，跳过预取
        return None

    # 步骤 3：提取查询文本
    content = last_user_message.get("content", "")
    if isinstance(content, list):
        # content 为 content blocks 列表（多模态消息格式）
        # 只提取 type == "text" 的块，拼接为纯文本查询
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        query = " ".join(text_parts)  # 多个文本块用空格拼接
    else:
        # content 为普通字符串，直接使用
        query = str(content)

    # 步骤 4：单词查询（无空白字符）缺乏上下文，跳过预取

    if not query or not re.search(r'\s', query.strip()):
        return None

    # 步骤 5：获取 memory 目录路径
    from codo.services.memory.paths import get_project_memory_dir
    memory_dir = str(get_project_memory_dir(cwd))  # 转为字符串，便于后续传参

    # 步骤 6：收集本会话已展示过的 memory 文件路径（用于去重）
    # 遍历消息历史，找出所有已注入的 memory 附件路径
    already_surfaced: set = set()
    for msg in messages:
        if msg.get("type") != "attachment":
            continue  # 只处理附件类型消息
        inner_att = msg.get("attachment", {})
        if inner_att.get("type") != "memory":
            continue  # 只处理 memory 类型附件
        path = inner_att.get("path")
        if path:
            already_surfaced.add(path)  # 记录已展示路径，避免重复预取

    # 步骤 7：定义异步预取协程
    async def _prefetch() -> List[Dict[str, Any]]:
        """
        执行 memory 预取的内部协程

        [Workflow]
        1. 调用 find_relevant_memories 获取相关文件列表
        2. 逐个读取文件内容
        3. 构建 memory 附件数据结构并返回
        """
        try:
            # 延迟导入，避免循环依赖
            from codo.services.memory.relevance import find_relevant_memories
            # 调用关键词匹配查找相关记忆文件
            relevant = find_relevant_memories(
                query=query,
                memory_dir=memory_dir,
                already_surfaced=already_surfaced,
            )

            # 逐个读取相关文件内容，构建附件数据
            result: List[Dict[str, Any]] = []
            for mem in relevant:
                try:
                    # 读取文件内容（UTF-8 编码）
                    file_content = Path(mem.path).read_text(encoding="utf-8")
                    result.append({
                        "type": "memory",          # 附件子类型：memory
                        "path": mem.path,          # 文件绝对路径（用于去重）
                        "content": file_content,   # 文件内容（注入模型上下文）
                        "mtime_ms": mem.mtime_ms,  # 修改时间（毫秒），供调用方判断新鲜度
                    })
                except Exception as e:
                    # 单个文件读取失败不影响其他文件，记录 debug 日志后继续
                    logger.debug(f"[memory prefetch] 读取文件失败: {mem.path}: {e}")

            return result

        except Exception as e:
            # 预取整体失败（如 memory 目录不存在），记录 debug 日志并返回空列表
            # 预取是 best-effort，失败不应影响主流程
            logger.debug(f"[memory prefetch] 预取失败: {e}")
            return []

    # 步骤 8：创建 MemoryPrefetch 句柄并启动异步任务
    prefetch = MemoryPrefetch()  # 创建预取句柄

    try:
        # 尝试获取当前事件循环并创建任务
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 事件循环正在运行（异步上下文），可以直接创建 Task
            prefetch.task = asyncio.create_task(_prefetch())
            return prefetch  # 返回句柄，调用方可 await prefetch.task
        else:
            # 事件循环未运行（同步上下文），无法创建 Task，跳过预取
            return None
    except RuntimeError:
        # 无法获取事件循环（如在非异步线程中调用），跳过预取
        return None
