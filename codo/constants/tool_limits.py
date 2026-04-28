"""
工具结果大小限制常量

这个模块定义了工具结果大小的各种限制：
1. 单个工具结果的默认大小限制
2. 单个消息中所有工具结果的聚合限制
3. 预览大小限制
4. Token 和字节转换常量

[Key Concepts]
- 单工具限制：防止单个工具返回过大结果
- 消息级限制：防止多个并行工具的结果总和过大
- 持久化策略：超限结果写入磁盘，返回预览
"""

# 默认单工具结果大小限制（字符数）

DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000
"""
默认单工具结果大小限制（字符数）

个别工具可以声明更低的 maxResultSizeChars，但这是系统级上限。
实际生效阈值 = min(工具声明值, DEFAULT_MAX_RESULT_SIZE_CHARS)
"""

# Token 和字节转换常量

MAX_TOOL_RESULT_TOKENS = 100_000
"""最大工具结果 Token 数"""

BYTES_PER_TOKEN = 4
"""每个 Token 的平均字节数"""

MAX_TOOL_RESULT_BYTES = MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN
"""最大工具结果字节数（400,000 字节）"""

# 单消息聚合限制（字符数）

MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000
"""
单个 user message 中所有 tool_result 的聚合预算（字符数）

这不是单个工具限制，而是"单条消息里多个并行工具结果的总预算"。
避免多个并行工具都单独没超，但合起来超大。
"""

# 预览大小限制（字节数）

PREVIEW_SIZE_BYTES = 2000
"""
持久化工具结果的预览大小（字节数）

当工具结果超限被持久化到文件时，返回给模型的预览片段大小。
"""

# 工具摘要最大长度

TOOL_SUMMARY_MAX_LENGTH = 50
"""工具使用摘要的最大长度（字符数）"""

# 持久化输出标签

PERSISTED_OUTPUT_TAG = "<persisted-output>"
"""持久化输出的开始标签"""

PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
"""持久化输出的结束标签"""
