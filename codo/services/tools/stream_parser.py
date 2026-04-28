"""
流式 API 响应解析器

"""

import json
import logging
from typing import Dict, Any, Optional, List, AsyncGenerator
from enum import Enum

logger = logging.getLogger(__name__)

class StreamEventType(str, Enum):
    """流式事件类型"""
    MESSAGE_START = "message_start"
    CONTENT_BLOCK_START = "content_block_start"
    CONTENT_BLOCK_DELTA = "content_block_delta"
    CONTENT_BLOCK_STOP = "content_block_stop"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_STOP = "message_stop"
    ERROR = "error"

class StreamParser:
    """
    流式 API 响应解析器

    解析 ?? API 的流式响应，提取：
    - 文本内容
    - tool_use 块
    - 消息元数据
    """

    def __init__(self):
        """初始化解析器"""
        self.message_id: Optional[str] = None
        self.model: Optional[str] = None
        self.role: str = "assistant"

        # 内容块
        self.content_blocks: List[Dict[str, Any]] = []
        self.current_block_index: int = -1

        # 累积的文本
        self.accumulated_text: str = ""

        # 使用统计
        self.input_tokens: int = 0
        self.output_tokens: int = 0

        # 停止原因
        self.stop_reason: Optional[str] = None

    async def parse_stream(
        self,
        stream: AsyncGenerator[Any, None]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        解析流式响应

        Args:
            stream: ?? API 流式响应

        Yields:
            解析后的事件
        """
        try:
            async for chunk in stream:
                event = await self._parse_chunk(chunk)
                if event:
                    yield event

        except Exception as e:
            logger.error(f"Stream parsing error: {e}")
            yield {
                "type": StreamEventType.ERROR,
                "error": str(e),
            }

    async def _parse_chunk(self, chunk: Any) -> Optional[Dict[str, Any]]:
        """
        解析单个流式块

        Args:
            chunk: API 响应块

        Returns:
            解析后的事件，如果不需要返回则为 None
        """
        # 处理不同类型的事件
        event_type = getattr(chunk, "type", None)

        if event_type == "message_start":
            return await self._handle_message_start(chunk)

        elif event_type == "content_block_start":
            return await self._handle_content_block_start(chunk)

        elif event_type == "content_block_delta":
            return await self._handle_content_block_delta(chunk)

        elif event_type == "content_block_stop":
            return await self._handle_content_block_stop(chunk)

        elif event_type == "message_delta":
            return await self._handle_message_delta(chunk)

        elif event_type == "message_stop":
            return await self._handle_message_stop(chunk)

        return None

    async def _handle_message_start(self, chunk: Any) -> Dict[str, Any]:
        """处理消息开始事件"""
        message = chunk.message
        self.message_id = message.id
        self.model = message.model
        self.role = message.role

        if hasattr(message, "usage"):
            self.input_tokens = message.usage.input_tokens

        return {
            "type": StreamEventType.MESSAGE_START,
            "message_id": self.message_id,
            "model": self.model,
        }

    async def _handle_content_block_start(self, chunk: Any) -> Dict[str, Any]:
        """处理内容块开始事件"""
        self.current_block_index = chunk.index
        block = chunk.content_block

        # 创建内容块
        if block.type == "text":
            content_block = {
                "type": "text",
                "text": "",
            }
        elif block.type == "tool_use":
            content_block = {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": {},
            }
        else:
            content_block = {"type": block.type}

        self.content_blocks.append(content_block)

        return {
            "type": StreamEventType.CONTENT_BLOCK_START,
            "index": self.current_block_index,
            "content_block": content_block,
        }

    async def _handle_content_block_delta(self, chunk: Any) -> Dict[str, Any]:
        """处理内容块增量事件"""
        delta = chunk.delta
        index = chunk.index

        if index >= len(self.content_blocks):
            logger.warning(f"Invalid block index: {index}")
            return None

        block = self.content_blocks[index]

        # 文本增量
        if delta.type == "text_delta":
            text = delta.text
            block["text"] += text
            self.accumulated_text += text

            return {
                "type": StreamEventType.CONTENT_BLOCK_DELTA,
                "index": index,
                "delta_type": "text",
                "text": text,
            }

        # tool_use 输入增量
        elif delta.type == "input_json_delta":
            partial_json = delta.partial_json
            # 累积 JSON 字符串（完整解析在 stop 时）
            if "partial_json" not in block:
                block["partial_json"] = ""
            block["partial_json"] += partial_json

            return {
                "type": StreamEventType.CONTENT_BLOCK_DELTA,
                "index": index,
                "delta_type": "input_json",
                "partial_json": partial_json,
            }

        return None

    async def _handle_content_block_stop(self, chunk: Any) -> Dict[str, Any]:
        """处理内容块停止事件"""
        index = chunk.index

        if index >= len(self.content_blocks):
            return None

        block = self.content_blocks[index]

        # 如果是 tool_use，解析完整的 input JSON
        if block["type"] == "tool_use" and "partial_json" in block:
            try:
                block["input"] = json.loads(block["partial_json"])
                del block["partial_json"]
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse tool input JSON: {e}")
                block["input"] = {}

        return {
            "type": StreamEventType.CONTENT_BLOCK_STOP,
            "index": index,
            "content_block": block,
        }

    async def _handle_message_delta(self, chunk: Any) -> Dict[str, Any]:
        """处理消息增量事件"""
        delta = chunk.delta

        if hasattr(delta, "stop_reason"):
            self.stop_reason = delta.stop_reason

        if hasattr(chunk, "usage"):
            self.output_tokens = chunk.usage.output_tokens

        return {
            "type": StreamEventType.MESSAGE_DELTA,
            "stop_reason": self.stop_reason,
        }

    async def _handle_message_stop(self, chunk: Any) -> Dict[str, Any]:
        """处理消息停止事件"""
        return {
            "type": StreamEventType.MESSAGE_STOP,
            "message_id": self.message_id,
            "stop_reason": self.stop_reason,
        }

    def get_assistant_message(self) -> Dict[str, Any]:
        """
        获取完整的 assistant 消息

        Returns:
            完整的消息对象
        """
        return {
            "role": self.role,
            "content": self.content_blocks,
        }

    def get_tool_uses(self) -> List[Dict[str, Any]]:
        """
        获取所有 tool_use 块

        Returns:
            tool_use 块列表
        """
        return [
            block for block in self.content_blocks
            if block.get("type") == "tool_use"
        ]

    def get_text_content(self) -> str:
        """
        获取累积的文本内容

        Returns:
            文本内容
        """
        return self.accumulated_text

    def get_usage(self) -> Dict[str, int]:
        """
        获取 token 使用统计

        Returns:
            使用统计
        """
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }
