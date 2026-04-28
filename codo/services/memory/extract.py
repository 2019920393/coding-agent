"""
记忆抽取核心逻辑。

该模块会在每轮查询结束后以后台任务方式运行：
1. 统计自上次抽取以来新增的模型可见消息
2. 扫描现有记忆文件，生成摘要清单
3. 构建抽取提示词
4. 单独调用模型执行抽取（非流式，不复用主对话）
5. 解析 tool_use 块，并在 memory 目录中执行 Write/Edit

"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from codo.services.memory.paths import (
    ensure_memory_dir,
    get_project_memory_dir,
    is_memory_path,
    ENTRYPOINT_NAME,
)
from codo.services.memory.prompts import build_extract_prompt
from codo.services.memory.scan import (
    format_memory_manifest,
    scan_memory_files,
)

logger = logging.getLogger(__name__)

@dataclass
class MemoryExtractionState:
    """
    抽取系统的闭包态可变状态。

    每个 QueryEngine 持有一个实例，用于记录游标位置和并发保护状态。
    """

    # 上一次已处理消息的 UUID。
    # 作为游标使用，确保每次只处理新增消息。
    last_message_uuid: Optional[str] = None

    # 抽取执行中标记，用于防止重叠运行。
    in_progress: bool = False

    # 自上次抽取以来累计的可触发轮次数。
    turns_since_last_extraction: int = 0

    # 抽取频率：每 N 轮执行一次。默认 1，表示每轮都抽取。
    extraction_interval: int = 1

    # 最近一次抽取中写入的文件路径列表。
    last_written_paths: List[str] = field(default_factory=list)

def _count_model_visible_since(
    messages: List[Dict[str, Any]],
    since_uuid: Optional[str],
) -> int:
    """
    统计给定 UUID 之后的模型可见消息数量（user/assistant）。

    Args:
        messages: 完整对话消息列表
        since_uuid: 起始 UUID；若为 None，则统计全部

    Returns:
        自游标之后的模型可见消息数量
    """
    if since_uuid is None:
        return sum(1 for m in messages if m.get("role") in ("user", "assistant"))

    found_start = False
    count = 0
    for m in messages:
        if not found_start:
            if m.get("uuid") == since_uuid:
                found_start = True
            continue
        if m.get("role") in ("user", "assistant"):
            count += 1

    # 如果 UUID 找不到（例如已被 compact 删除），则回退为统计全部，
    # 避免抽取功能被永久卡死。
    if not found_start:
        return sum(1 for m in messages if m.get("role") in ("user", "assistant"))

    return count

def _has_memory_writes_since(
    messages: List[Dict[str, Any]],
    since_uuid: Optional[str],
    cwd: str,
) -> bool:
    """
    检查主 agent 是否已经向 memory 目录写入过内容。

    如果已经写入，则跳过后台抽取，避免两套链路重复写入。
    """
    found_start = since_uuid is None
    for m in messages:
        if not found_start:
            if m.get("uuid") == since_uuid:
                found_start = True
            continue
        if m.get("role") != "assistant":
            continue
        content = m.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use":
                continue
            if block.get("name") not in ("Write", "Edit"):
                continue
            file_path = (block.get("input") or {}).get("file_path", "")
            if file_path and is_memory_path(file_path, cwd):
                return True
    return False

#本质就是扫描对话历史，看 assistant 有没有调用过 Write/Edit 工具去写 memory 目录的文件。

#如果主 agent 已经手动写过记忆了，后台抽取就跳过，避免重复写入。

def _execute_memory_write(file_path: str, content: str, memory_dir: str) -> bool:
    """
    执行一次 Write 工具调用，在 memory 目录中创建或覆盖文件。

    出于安全考虑，只允许写入 memory 目录内部。

    Returns:
        写入成功时返回 True
    """
    resolved = Path(file_path).resolve()
    mem_resolved = Path(memory_dir).resolve()

    # 安全校验：仅允许写入 memory 目录内部。
    if not str(resolved).startswith(str(mem_resolved)):
        logger.warning(
            f"[extractMemories] blocked write outside memory dir: {file_path}"
        )
        return False

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"[extractMemories] write failed: {file_path}: {e}")
        return False

def _execute_memory_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    memory_dir: str,
) -> bool:
    """
    执行一次 Edit 工具调用，替换 memory 目录中文件内的文本。

    Returns:
        编辑成功时返回 True
    """
    resolved = Path(file_path).resolve()
    mem_resolved = Path(memory_dir).resolve()

    if not str(resolved).startswith(str(mem_resolved)):
        logger.warning(
            f"[extractMemories] blocked edit outside memory dir: {file_path}"
        )
        return False

    try:
        if not resolved.exists():
            logger.warning(f"[extractMemories] edit target not found: {file_path}")
            return False

        content = resolved.read_text(encoding="utf-8")
        if old_string not in content:
            logger.warning(
                f"[extractMemories] old_string not found in {file_path}"
            )
            return False

        new_content = content.replace(old_string, new_string, 1)
        resolved.write_text(new_content, encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"[extractMemories] edit failed: {file_path}: {e}")
        return False

def _process_tool_calls(
    content: List[Dict[str, Any]],
    memory_dir: str,
) -> List[str]:
    """
    处理抽取 agent 响应中的 `tool_use` 块。

    会对 memory 目录执行 Write/Edit 操作。

    Returns:
        被写入或编辑过的文件路径列表
    """
    written_paths = []

    for block in content:
        if block.get("type") != "tool_use":
            continue

        name = block.get("name", "")
        inp = block.get("input", {})

        if name == "Write":
            file_path = inp.get("file_path", "")
            file_content = inp.get("content", "")
            if file_path and file_content:
                if _execute_memory_write(file_path, file_content, memory_dir):
                    written_paths.append(file_path)

        elif name == "Edit":
            file_path = inp.get("file_path", "")
            old_string = inp.get("old_string", "")
            new_string = inp.get("new_string", "")
            if file_path and old_string:
                if _execute_memory_edit(
                    file_path, old_string, new_string, memory_dir
                ):
                    written_paths.append(file_path)

        elif name == "Read":
            # 允许 Read，但在这里不会产出额外记录。
            pass

    return list(set(written_paths))

async def _run_extraction_agent(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    user_prompt: str,
    memory_dir: str,
    max_turns: int = 5,
) -> List[str]:
    """
    运行抽取 agent：调用模型、执行工具、循环迭代。

    循环流程：
    1. 带着工具定义发送用户提示词
    2. 模型返回 `tool_use` 块（读取现有记忆、写入/编辑记忆文件）
    3. 执行工具调用，并把结果喂回模型
    4. 持续迭代，直到模型停止使用工具或达到 `max_turns`

    参考 `runForkedAgent()` 的整体模式，但这里做了简化：
    不共享 prompt cache，不依赖复杂的 ToolUseContext，只做直接 API 调用。

    Args:
        client: 客户端
        model: 使用的模型名
        system_prompt: 抽取 agent 的系统提示词
        user_prompt: 抽取指令
        memory_dir: memory 目录路径
        max_turns: 最大 API 往返轮数

    Returns:
        agent 写入或编辑过的文件路径列表
    """
    # 提供给抽取 agent 的工具定义。
    # 当前仅暴露 Read / Write / Edit，并对写入路径施加 memory 目录限制。
    tools = [
        {
            "name": "Read",
            "description": "Read a file's contents. Use this to read existing memory files.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to read",
                    }
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "Write",
            "description": f"Write a file. Only paths inside {memory_dir} are allowed.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
        {
            "name": "Edit",
            "description": f"Edit a file by replacing a string. Only paths inside {memory_dir} are allowed.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact text to replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement text",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    ]

    messages = [{"role": "user", "content": user_prompt}]
    all_written_paths = []

    for turn in range(max_turns):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
                tools=tools,
            )
        except Exception as e:
            logger.error(f"[extractMemories] API call failed on turn {turn}: {e}")
            break

        # 提取响应中的内容块。
        content_blocks = []
        for block in response.content:
            if block.type == "text":
                content_blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content_blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        # 将 assistant 响应追加到对话上下文。
        messages.append({"role": "assistant", "content": content_blocks})

        # 检查本轮是否触发了工具调用。
        tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]
        if not tool_uses:
            # 没有工具调用，说明 agent 已结束。
            break

        # 执行工具调用，并构造 tool_result。
        tool_results = []
        for tool_use in tool_uses:
            name = tool_use["name"]
            inp = tool_use["input"]

            if name == "Read":
                # 执行读文件。
                file_path = inp.get("file_path", "")
                try:
                    result_text = Path(file_path).read_text(encoding="utf-8")
                except Exception as e:
                    result_text = f"Error reading file: {e}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": result_text,
                })

            elif name == "Write":
                file_path = inp.get("file_path", "")
                file_content = inp.get("content", "")
                if _execute_memory_write(file_path, file_content, memory_dir):
                    all_written_paths.append(file_path)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": f"Successfully wrote {file_path}",
                    })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": f"Error: write blocked or failed for {file_path}",
                        "is_error": True,
                    })

            elif name == "Edit":
                file_path = inp.get("file_path", "")
                old_string = inp.get("old_string", "")
                new_string = inp.get("new_string", "")
                if _execute_memory_edit(file_path, old_string, new_string, memory_dir):
                    all_written_paths.append(file_path)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": f"Successfully edited {file_path}",
                    })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": f"Error: edit failed for {file_path}",
                        "is_error": True,
                    })

        # 将工具结果作为 user 消息继续喂回模型。
        messages.append({"role": "user", "content": tool_results})

        # 若模型主动结束当前轮次，则停止循环。
        if response.stop_reason == "end_turn":
            break

    return list(set(all_written_paths))

async def extract_memories(
    client: AsyncAnthropic,
    model: str,
    messages: List[Dict[str, Any]],
    cwd: str,
    state: MemoryExtractionState,
) -> List[str]:
    """
    在 query loop 结束后执行记忆抽取。

    该函数会在 QueryEngine 完成一轮完整响应后以 fire-and-forget 方式触发，
    即模型已经给出最终回答，且没有待执行的工具调用。

    Args:
        client: 异步客户端
        model: 抽取 agent 使用的模型名
        messages: 完整消息列表
        cwd: 当前工作目录
        state: 可变抽取状态（游标、并发保护等）

    Returns:
        被写入或编辑的 memory 文件路径列表。
        如果本次跳过或没有变更，则返回空列表。
    """
    # 守卫：若已有抽取在执行，则直接跳过，避免重叠运行。
    if state.in_progress:
        logger.debug("[extractMemories] skipping — extraction in progress")
        return []

    # 频率检查：仅每 N 轮执行一次。
    state.turns_since_last_extraction += 1
    if state.turns_since_last_extraction < state.extraction_interval:
        return []
    state.turns_since_last_extraction = 0

    # 统计自上次抽取以来的新增消息数量。
    new_message_count = _count_model_visible_since(
        messages, state.last_message_uuid
    )
    if new_message_count < 2:
        # 至少需要一组 user + assistant 才值得抽取。
        return []

    # 互斥保护：如果主 agent 已经写过 memory，则跳过后台抽取。
    if _has_memory_writes_since(messages, state.last_message_uuid, cwd):
        logger.debug(
            "[extractMemories] skipping — conversation already wrote to memory files"
        )
        # 将游标推进到最新消息，跳过这一段已覆盖范围。
        last_msg = messages[-1] if messages else None
        if last_msg and last_msg.get("uuid"):
            state.last_message_uuid = last_msg["uuid"]
        return []

    # 标记为执行中。
    state.in_progress = True
    try:
        logger.debug(
            f"[extractMemories] starting — {new_message_count} new messages"
        )

        # 确保 memory 目录存在。
        memory_dir = str(ensure_memory_dir(cwd))

        # 扫描现有记忆文件并生成清单。
        headers = scan_memory_files(memory_dir)
        existing_memories = format_memory_manifest(headers)

        # 构建抽取提示词。
        user_prompt = build_extract_prompt(
            new_message_count=new_message_count,
            existing_memories=existing_memories,
            memory_dir=memory_dir,
        )

        # 构建一个最小系统提示词。
        # 对话历史会通过 messages 传入，使 agent 能看到最近消息。
        system_prompt = (
            "You are the memory extraction subagent. "
            "Your job is to analyze the recent conversation and save durable memories. "
            "You have access to Read, Write, and Edit tools. "
            "Only write files inside the memory directory."
        )

        # 运行抽取 agent。
        written_paths = await _run_extraction_agent(
            client=client,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            memory_dir=memory_dir,
            max_turns=5,
        )

        # 成功后推进游标。
        last_msg = messages[-1] if messages else None
        if last_msg and last_msg.get("uuid"):
            state.last_message_uuid = last_msg["uuid"]

        state.last_written_paths = written_paths

        # 报告时过滤掉索引文件本身。
        memory_paths = [
            p for p in written_paths
            if not p.endswith(ENTRYPOINT_NAME)
        ]

        if memory_paths:
            logger.info(
                f"[extractMemories] memories saved: {', '.join(memory_paths)}"
            )
        else:
            logger.debug("[extractMemories] no memories saved this run")

        return written_paths

    except Exception as e:
        # 抽取属于 best-effort：只记录日志，不向上抛出。
        logger.error(f"[extractMemories] error: {e}")
        return []

    finally:
        state.in_progress = False
