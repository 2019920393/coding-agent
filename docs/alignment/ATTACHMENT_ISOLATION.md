# Attachment Message Isolation

## 概述

本文档描述了 attachment 消息的隔离机制，确保临时的 attachment 消息不会被持久化到下一轮迭代。

**关键代码** (line 1716):
```typescript
const next: State = {
  messages: [...messagesForQuery, ...assistantMessages, ...toolResults],
  // ...
}
```

其中：
- `messagesForQuery`: 基础消息 + microcompact + auto-compact（**不包含** attachment）
- `assistantMessages`: 助手响应
- `toolResults`: 工具执行结果 + attachment 消息（临时）

**Attachment 消息添加位置** (line 1585):
```typescript
for await (const attachment of getAttachmentMessages(...)) {
  yield attachment
  toolResults.push(attachment)  // 添加到 toolResults，不是 messagesForQuery
}
```

## Python 实现

### 消息流转

```python
# 1. 解构状态
messages = state.messages  # 基础消息（不含 attachment）

# 2. 收集 attachment 消息
attachment_messages = await get_attachment_messages(...)

# 3. 产出 attachment 消息（让用户看到）
for att_msg in attachment_messages:
    yield att_msg

# 4. 准备消息（不包含 attachments）
messages_for_query = messages.copy()  # 工作副本

# 5. Microcompact（修改 messages_for_query）
microcompact_result = await microcompact_if_needed(messages=messages_for_query, ...)
if microcompact_result.compacted_count > 0:
    messages_for_query = microcompact_result.messages

# 6. Auto-compact（修改 messages_for_query）
compact_result = await auto_compact_if_needed(messages=messages_for_query, ...)
if compact_result:
    messages_for_query = compact_result.new_messages

# 7. API 调用时临时合并 attachment
messages_with_attachments = messages_for_query.copy()
if attachment_messages:
    messages_with_attachments.extend(attachment_messages)

# 8. 调用 API
async with client.messages.stream(
    messages=messages_with_attachments,  # 临时合并
    ...
) as stream:
    # ...

# 9. 添加助手响应
messages_for_query.append(assistant_message)

# 10. 添加工具结果
for result in tool_results:
    messages_for_query.append(result.message)

# 11. 消费 memory prefetch（添加到 messages_for_query）
if memory_prefetch:
    memory_results = await memory_prefetch
    for memory_msg in memory_results:
        messages_for_query.append(memory_msg)

# 12. 更新状态（不包含 attachment_messages）
state = QueryState(
    messages=messages_for_query,  # ✅ 不包含 attachment
    ...
)
```

### 关键点

1. **Attachment 消息是临时的**
   - 在每轮迭代开始时重新收集
   - 只在 API 调用时临时添加
   - 不会保存到 `state.messages`

2. **持久化的消息**
   - 用户消息（初始输入）
   - 助手响应
   - 工具结果
   - Memory prefetch 结果
   - Compact boundary 标记

3. **不持久化的消息**
   - Attachment 消息（IDE selection, queued commands, etc.）
   - 这些消息在每轮迭代时动态生成

## 验证

运行测试：
```bash
python test_attachment_isolation.py
```

测试验证：
1. Attachment 消息在每轮都被产出
2. Attachment 消息不会累积（每轮重新生成）
3. 工具结果和助手响应被正确持久化

## 对齐状态

- ✅ Attachment 消息隔离机制
- ✅ 消息流转逻辑
- ✅ 状态更新逻辑
- ✅ Memory prefetch 消费

## 相关文件

- `Codo_new/codo/query.py`: 主循环实现
- `Codo_new/codo/services/attachments.py`: Attachment 收集
