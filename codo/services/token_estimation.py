"""
Token 估算服务 - 粗略估算消息的 token 数量

基于字符数的简单估算（1 token ≈ 3.5 个英文字符，1.5 个中文字符）。
用于自动压缩阈值判断，不用于计费。
底层实现：
模型的上下文窗口硬编码  最大输出token数也是硬编码
估算一段话的token max(1, int(len(text) / CHARS_PER_TOKEN))
估算API格式的消息块token
"""

import json

from typing import Any, Dict, List

# 平均每个 token 的字符数（保守估计）
CHARS_PER_TOKEN = 1.5

# 已知模型的上下文窗口大小
CONTEXT_WINDOWS = {
    "claude-opus-4-20250514": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-haiku-3-5-20241022": 200_000,
    "claude-sonnet-3-5-20241022": 200_000,
    # 默认值
    "default": 200_000,
}

# 已知模型的最大输出 token 数
MAX_OUTPUT_TOKENS = {
    "claude-opus-4-20250514": 16_384,
    "claude-sonnet-4-20250514": 16_384,
    "claude-haiku-3-5-20241022": 8_192,
    "claude-sonnet-3-5-20241022": 8_192,
    "default": 8_192,
}

def get_context_window(model: str) -> int:
    """获取模型的上下文窗口大小"""
    return CONTEXT_WINDOWS.get(model, CONTEXT_WINDOWS["default"])

def get_max_output_tokens(model: str) -> int:
    """获取模型的最大输出 token 数"""
    return MAX_OUTPUT_TOKENS.get(model, MAX_OUTPUT_TOKENS["default"])

def estimate_token_count(text: str) -> int:
    """估算文本字符串的 token 数量

    - roughTokenCountEstimation()

    使用简单的字符数启发式算法。
    """
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))

def estimate_content_tokens(content: Any) -> int:
    """估算消息内容的 token 数量（字符串或内容块列表）

    支持的内容类型：
    - 字符串：直接估算
    - 列表：遍历所有块（text/tool_use/tool_result/image）
    - 其他：序列化为 JSON 后估算
    """
    if content is None:
        return 0

    if isinstance(content, str):
        return estimate_token_count(content)

    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    total += estimate_token_count(block.get("text", ""))
                elif block_type == "tool_use":
                    # 工具名称 + 输入 JSON
                    total += estimate_token_count(block.get("name", ""))
                    input_str = json.dumps(block.get("input", {}), ensure_ascii=False)
                    total += estimate_token_count(input_str)
                elif block_type == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        total += estimate_token_count(result_content)
                    elif isinstance(result_content, list):
                        total += estimate_content_tokens(result_content)
                elif block_type == "image":
                    # 图片使用固定 token 预算
                    total += 1600
                else:
                    # 未知块类型 - 从 JSON 估算
                    total += estimate_token_count(json.dumps(block, ensure_ascii=False))
            elif isinstance(block, str):
                total += estimate_token_count(block)
        return total

    # 兜底：序列化为 JSON
    return estimate_token_count(json.dumps(content, ensure_ascii=False))

def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """估算消息列表的总 token 数量

    - roughTokenCountEstimationForMessages()

    每条消息额外计入 4 个 token（role/结构开销）
    """
    total = 0
    for msg in messages:
        # Role 开销（每条消息约 4 个 token 用于 role/结构）
        total += 4
        total += estimate_content_tokens(msg.get("content"))
    return total

def estimate_system_prompt_tokens(system_prompt: str) -> int:
    """估算系统提示词的 token 数量"""
    return estimate_token_count(system_prompt)

class TokenBudget:
    """Token 预算管理器

    作用：
    1. 追踪 token 使用量
    2. 计算有效上下文窗口（扣除输出预留）
    3. 判断是否需要触发压缩

    设计思想（类似油箱指示灯）：
    - 剩余 < 13k → 黄灯（自动压缩）
    - 剩余 < 3k → 红灯（必须压缩）
    - 预留 20k 作为"备用油箱"（输出 token）
    """

    # 压缩摘要输出预留（最大 20k）
    MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

    # 自动压缩缓冲区（触发自动压缩的安全距离）
    AUTOCOMPACT_BUFFER_TOKENS = 13_000

    # 警告阈值缓冲区（用于 UI 警告提示）
    WARNING_THRESHOLD_BUFFER_TOKENS = 20_000

    # 错误阈值缓冲区（严重警告）
    ERROR_THRESHOLD_BUFFER_TOKENS = 20_000

    # 手动压缩缓冲区（阻塞限制，必须压缩才能继续）
    MANUAL_COMPACT_BUFFER_TOKENS = 3_000

    def __init__(self, model: str):
        self.model = model
        self.context_window = get_context_window(model)  # 模型的上下文窗口大小
        self.max_output = get_max_output_tokens(model)   # 模型的最大输出 token

    @property
    def effective_context_window(self) -> int:
        """有效上下文窗口 = 总窗口 - 输出预留"""
        reserved = min(self.max_output, self.MAX_OUTPUT_TOKENS_FOR_SUMMARY)
        return self.context_window - reserved

    @property
    def auto_compact_threshold(self) -> int:
        """自动压缩阈值（超过此值触发自动压缩）"""
        return self.effective_context_window - self.AUTOCOMPACT_BUFFER_TOKENS

    @property
    def blocking_limit(self) -> int:
        """阻塞限制（超过此值必须压缩才能继续）"""
        return self.effective_context_window - self.MANUAL_COMPACT_BUFFER_TOKENS

    def should_auto_compact(self, token_count: int) -> bool:
        """判断是否应该触发自动压缩"""
        return token_count >= self.auto_compact_threshold

    def is_at_blocking_limit(self, token_count: int) -> bool:
        """判断是否达到阻塞限制（必须压缩）"""
        return token_count >= self.blocking_limit

    def get_usage_stats(self, token_count: int) -> Dict[str, Any]:
        """获取 token 使用统计信息

        返回字典包含：
        - token_count: 当前 token 数量
        - context_window: 模型上下文窗口
        - effective_context_window: 有效上下文窗口
        - auto_compact_threshold: 自动压缩阈值
        - blocking_limit: 阻塞限制
        - percent_used: 使用百分比
        - percent_left: 剩余百分比
        - should_auto_compact: 是否应该自动压缩
        - is_at_blocking_limit: 是否达到阻塞限制
        """
        effective = self.effective_context_window
        percent_used = min(100, round((token_count / effective) * 100))
        percent_left = max(0, 100 - percent_used)

        return {
            "token_count": token_count,
            "context_window": self.context_window,
            "effective_context_window": effective,
            "auto_compact_threshold": self.auto_compact_threshold,
            "blocking_limit": self.blocking_limit,
            "percent_used": percent_used,
            "percent_left": percent_left,
            "should_auto_compact": self.should_auto_compact(token_count),
            "is_at_blocking_limit": self.is_at_blocking_limit(token_count),
        }
