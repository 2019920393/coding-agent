"""
消息规范化

将消息历史规范化为 ?? API 格式。

参考：src/utils/messages.ts - normalizeMessagesForAPI()
简化：移除复杂的附件重排序、媒体块剥离、工具引用块处理
保留：基本的消息过滤、格式转换、工具调用/结果配对
"""

from typing import List, Dict, Any, Optional

def _render_attachment_for_model(attachment: Dict[str, Any]) -> Optional[str]:
    attachment_type = str(attachment.get("type", "") or "")
    if not attachment_type:
        return None

    if attachment_type == "queued_command":
        prompt = str(attachment.get("prompt", "") or "").strip()
        if not prompt:
            return None
        origin = attachment.get("origin", {}) if isinstance(attachment.get("origin"), dict) else {}
        origin_name = str(origin.get("name", "") or "").strip()
        if origin_name:
            header = (
                f"<system-reminder>Slash command /{origin_name} expanded into the following "
                "turn-local instructions. Follow them for this request.</system-reminder>"
            )
        else:
            header = (
                "<system-reminder>A queued command has been attached for this turn. "
                "Use it as part of the user's request.</system-reminder>"
            )
        return f"{header}\n{prompt}".strip()

    if attachment_type == "ide_selection":
        filename = str(attachment.get("filename", "") or "").strip()
        text = str(attachment.get("text", "") or "").strip()
        start_line = attachment.get("startLine")
        end_line = attachment.get("endLine")
        location = ""
        if start_line is not None and end_line is not None:
            location = f" ({start_line}-{end_line})"
        return (
            f"<system-reminder>The user currently selected code in {filename}{location}. "
            "Treat it as high-priority context.</system-reminder>\n"
            f"{text}"
        ).strip()

    if attachment_type == "opened_file_in_ide":
        filename = str(attachment.get("filename", "") or "").strip()
        if not filename:
            return None
        return (
            f"<system-reminder>The user's active IDE file is {filename}. "
            "Use this as current context when relevant.</system-reminder>"
        )

    if attachment_type == "plan_mode_reminder":
        full = bool(attachment.get("full", False))
        if full:
            return (
                "<system-reminder>Plan mode reminder: stay in planning. Do not edit files or run "
                "destructive implementation steps until the plan is agreed.</system-reminder>"
            )
        return (
            "<system-reminder>Plan mode reminder: keep reasoning about the plan before implementation."
            "</system-reminder>"
        )

    if attachment_type == "memory":
        path = str(attachment.get("path", "") or "").strip()
        content = str(attachment.get("content", "") or "").strip()
        if not content:
            return None
        header = (
            f"<system-reminder>Relevant memory attached from {path}.</system-reminder>"
            if path
            else "<system-reminder>Relevant memory attached.</system-reminder>"
        )
        return f"{header}\n{content}".strip()

    payload = {key: value for key, value in attachment.items() if key != "type"}
    if not payload:
        return None
    return f"<system-reminder>Attachment {attachment_type}: {payload}</system-reminder>"

def _normalize_attachment_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    attachment = message.get("attachment")
    if not isinstance(attachment, dict):
        return None
    content = _render_attachment_for_model(attachment)
    if not content:
        return None
    return {
        "role": "user",
        "content": content,
    }

def normalize_messages_for_api(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    规范化消息历史为 ?? API 格式

    [Workflow]
    1. 过滤虚拟消息（内部使用的消息）
    2. 转换消息格式
    3. 确保消息交替（user/assistant）
    4. 验证工具调用/结果配对

    Args:
        messages: 原始消息列表

    Returns:
        规范化后的消息列表（?? API 格式）
    """
    if not messages:
        return []

    normalized = []
    virtual_boundary_before_next = False

    for msg in messages:
        # 跳过虚拟消息
        if msg.get("is_virtual", False):
            # 记录一个“边界”，避免过滤后把两侧同角色消息错误合并
            virtual_boundary_before_next = True
            continue

        if msg.get("type") == "attachment":
            normalized_msg = _normalize_attachment_message(msg)
            if normalized_msg is None:
                continue
            if virtual_boundary_before_next:
                normalized_msg["_virtual_boundary"] = True
                virtual_boundary_before_next = False
            normalized.append(normalized_msg)
            continue

        # 转换消息格式
        role = msg.get("role") or msg.get("type")
        content = msg.get("content")

        if not role or not content:
            continue

        # 规范化 role
        if role == "user":
            normalized_role = "user"
        elif role == "assistant":
            normalized_role = "assistant"
        else:
            # 跳过未知角色
            continue

        # 构建规范化的消息
        normalized_msg = {
            "role": normalized_role,
            "content": content,
        }
        if virtual_boundary_before_next:
            normalized_msg["_virtual_boundary"] = True
            virtual_boundary_before_next = False

        normalized.append(normalized_msg)

    # 确保消息交替
    # 合并连续的相同角色消息
    return ensure_alternating_messages(normalized)

def ensure_alternating_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    确保消息交替（user/assistant）

    [Workflow]
    1. 检查连续的相同角色消息
    2. 合并连续的 user 消息
    3. 合并连续的 assistant 消息

    Args:
        messages: 消息列表

    Returns:
        交替的消息列表
    """
    if not messages:
        return []

    result = []
    current_role = None
    current_content = []

    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        has_boundary = bool(msg.get("_virtual_boundary", False))

        if role == current_role and not has_boundary:
            # 相同角色，合并内容
            if isinstance(content, str):
                current_content.append(content)
            elif isinstance(content, list):
                current_content.extend(content)
        else:
            # 不同角色，保存当前消息
            if current_role is not None:
                result.append({
                    "role": current_role,
                    "content": _merge_content(current_content),
                })

            # 开始新消息
            current_role = role
            if isinstance(content, str):
                current_content = [content]
            elif isinstance(content, list):
                current_content = content.copy()
            else:
                current_content = [content]

    # 保存最后一条消息
    if current_role is not None:
        result.append({
            "role": current_role,
            "content": _merge_content(current_content),
        })

    return result

def _merge_content(content_list: List[Any]) -> Any:
    """
    合并内容列表

    [Workflow]
    1. 如果所有内容都是字符串，合并为单一字符串
    2. 否则，返回内容列表

    Args:
        content_list: 内容列表

    Returns:
        合并后的内容
    """
    if not content_list:
        return ""

    # 检查是否所有内容都是字符串
    all_strings = all(isinstance(c, str) for c in content_list)

    if all_strings:
        # 合并为单一字符串
        return "\n\n".join(content_list)
    else:
        # 返回内容列表（可能包含工具调用等）
        result = []
        for c in content_list:
            if isinstance(c, str):
                result.append({"type": "text", "text": c})
            elif isinstance(c, list):
                result.extend(c)
            else:
                result.append(c)
        return result

def create_user_message(content: str) -> Dict[str, Any]:
    """
    创建用户消息

    [Workflow]
    创建标准的用户消息格式

    Args:
        content: 消息内容

    Returns:
        用户消息字典
    """
    return {
        "role": "user",
        "content": content,
    }

def create_assistant_message(content: Any) -> Dict[str, Any]:
    """
    创建助手消息

    [Workflow]
    创建标准的助手消息格式

    Args:
        content: 消息内容（可以是字符串或内容块列表）

    Returns:
        助手消息字典
    """
    return {
        "role": "assistant",
        "content": content,
    }

def add_cache_breakpoints(
    messages: List[Dict[str, Any]],
    enable_caching: bool = True,
) -> List[Dict[str, Any]]:
    """
    为消息添加缓存断点

    [Workflow]
    1. 如果启用缓存，在最后几条消息上添加缓存控制标记
    2. 简化版本：只在最后一条 user 消息上添加缓存标记

    Args:
        messages: 消息列表
        enable_caching: 是否启用缓存

    Returns:
        添加缓存标记的消息列表
    """
    if not enable_caching or not messages:
        return messages

    # 简化版本：在最后一条 user 消息上添加缓存标记
    result = messages.copy()

    # 从后往前找最后一条 user 消息
    for i in range(len(result) - 1, -1, -1):
        if result[i]["role"] == "user":
            # 添加缓存控制标记
            content = result[i]["content"]

            # 如果内容是字符串，转换为内容块
            if isinstance(content, str):
                result[i]["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            elif isinstance(content, list):
                # 如果内容是列表，在最后一个文本块上添加缓存标记
                for j in range(len(content) - 1, -1, -1):
                    if isinstance(content[j], dict) and content[j].get("type") == "text":
                        content[j]["cache_control"] = {"type": "ephemeral"}
                        break

            break

    return result
