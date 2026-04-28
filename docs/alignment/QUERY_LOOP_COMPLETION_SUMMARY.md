# Query Loop 对齐完成总结

## 完成的工作

### 1. 修复 Attachment 消息隔离问题

**问题**: Attachment 消息被错误地持久化到 `state.messages`，导致每轮迭代累积

**解决方案**:
- 分离 `messages_for_query`（持久）和 `messages_with_attachments`（临时）
- Attachment 消息只在 API 调用时临时添加
- 每轮迭代重新收集 attachment 消息

**对齐参考**: `query.ts:1585-1614, 1716`

**修改文件**:
- `Codo_new/codo/query.py`: 重构消息流转逻辑

### 2. 修复 Section 11 缺失问题

**问题**: Section 11（Stop Hooks）在 `needs_follow_up=False` 分支中缺失

**解决方案**:
- 添加 Section 11: 执行 stop hooks
- 添加 memory extraction 触发逻辑
- 正确处理最终轮次的清理工作

**对齐参考**: `query.ts:1620-1650`

**修改文件**:
- `Codo_new/codo/query.py`: 添加 stop hooks 执行逻辑

### 3. 修正所有 Section 编号

**问题**: Section 5-6 重复，导致后续编号错乱

**解决方案**:
- 重新编号所有 section（1-14）
- 确保逻辑顺序清晰

**最终结构**:
```
1. 解构状态（每次迭代开始）
2. 收集 Attachment 消息
3. 产出 stream_request_start 事件
4. 准备消息（不包含 attachments）
5. 执行 Microcompact（清除旧工具结果）
6. Auto-compact 检查
7. 创建 StreamingToolExecutor
8. 调用 API streaming
9. 获取已完成的工具结果（增量返回）
10. 等待剩余工具结果
11. 消费 Memory Prefetch（如果已完成）
12. 检查是否需要继续（有工具调用）
13. 检查 maxTurns 限制
14. Continue - 更新状态并继续下一轮
```

### 4. 创建测试和文档

**测试文件**:
- `test_attachment_isolation.py`: 验证 attachment 消息隔离

**文档文件**:
- `docs/alignment/ATTACHMENT_ISOLATION.md`: Attachment 隔离机制说明

## 关键架构决策

### Attachment 消息流转

```python
# 每轮迭代开始
messages_for_query = state.messages.copy()  # 基础消息

# 收集临时 attachment
attachment_messages = await get_attachment_messages(...)

# API 调用时临时合并
messages_with_attachments = messages_for_query + attachment_messages

# 调用 API
async with client.messages.stream(messages=messages_with_attachments):
    ...

# 添加助手响应和工具结果到 messages_for_query
messages_for_query.append(assistant_message)
messages_for_query.extend(tool_results)

# 更新状态（不包含 attachment）
state = QueryState(messages=messages_for_query, ...)
```

### 持久化 vs 临时消息

**持久化到 state.messages**:
- 用户输入
- 助手响应
- 工具结果
- Memory prefetch 结果
- Compact boundary 标记

**不持久化（每轮重新生成）**:
- Attachment 消息（IDE selection, queued commands, etc.）

## 对齐状态

### 已完成 ✅
- [x] Attachment 消息隔离机制
- [x] Stop hooks 执行逻辑
- [x] Memory prefetch 消费逻辑
- [x] Section 编号修正
- [x] 消息流转架构

### 待完成 ⏳
- [ ] Stop hooks 实际实现（目前是 TODO）
- [ ] Memory extraction 实际实现（目前是 TODO）
- [ ] 完整的 attachment 类型支持（目前只有基础类型）
- [ ] Reactive compact 的完整实现
- [ ] Max output tokens recovery

## 测试验证

运行测试：
```bash
python test_attachment_isolation.py
```

预期结果：
- ✅ 2 次 API 调用
- ✅ 2 次 stream start
- ✅ 2 次 attachment 事件（每轮重新生成）

## 相关文件

### 修改的文件
- `Codo_new/codo/query.py`: 主循环实现

### 新增的文件
- `test_attachment_isolation.py`: 测试文件
- `docs/alignment/ATTACHMENT_ISOLATION.md`: 文档

### 参考文件

## 下一步

1. 实现 stop hooks 的实际逻辑
2. 实现 memory extraction 的实际逻辑
3. 扩展 attachment 类型支持
4. 完善 reactive compact
5. 添加更多集成测试
