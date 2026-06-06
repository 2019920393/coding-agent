"""从会话 transcript 抽取对话片段，生成 memory.extract 评估数据集的脚手架。

把真实会话切成「一轮 user + assistant」的片段，每个片段附带：
    messages —— 原始消息（保留 content block 结构），可直接喂给 extract_memories
    preview  —— 扁平化可读文本，供人工标注时阅读
    label    —— 留空的标注字段，人工填好后即为评估数据集

用法：
    python -m codo.evals.dataset_extract <transcript.jsonl 或 会话目录> -o scaffold.json
    python -m codo.evals.dataset_extract ~/.codo/sessions/xxx --window 2 -o scaffold.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from codo.session.storage import build_conversation_chain, load_session_from_file

# 人工标注字段，留空待填
_EMPTY_LABEL = {
    "expect_write": None,    # true / false：这段是否应该产生记忆
    "expected_type": None,   # preference / project_fact / feedback / task_context
    "expected_gist": None,   # 一句话描述应被记住的内容（供关键词或语义判分）
}


def _flatten_content(content: str | list[dict[str, Any]]) -> str:
    """把消息 content 压成可读文本，供人工标注阅读。"""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_use":
            parts.append(f"[tool_use: {block.get('name', '?')}]")
        elif btype == "tool_result":
            parts.append("[tool_result]")
        else:
            parts.append(f"[{btype}]")
    return "\n".join(p for p in parts if p)


# __REST__
