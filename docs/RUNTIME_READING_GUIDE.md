# Codo Runtime 运行时阅读路线

> 本文档专注于 Codo 的 Runtime 执行链路，忽略 CLI 实现细节

## 概览

Codo 的 Runtime 核心是一个**状态机驱动的对话循环**，负责：
1. 管理对话状态（消息历史、token 预算、压缩状态）
2. 调用 ?? API 流式响应
3. 并发执行工具调用
4. 处理上下文压缩、记忆提取、错误恢复

---

## 核心执行链路

```
用户输入
  ↓
QueryEngine.submit_message_stream()
  ↓
query() → query_loop() ← 核心状态机循环
  ├─ Phase 1: prepare_turn
  │   ├─ 估算 token 用量
  │   ├─ 判断是否需要 auto_compact
  │   └─ 记录 checkpoint
  ├─ Phase 2: stream_assistant
  │   ├─ 调用 ?? API（流式）
  │   ├─ 解析 content blocks
  │   └─ 发现 tool_use 块 → 提交到 StreamingToolExecutor
  ├─ Phase 3: execute_tools
  │   ├─ StreamingToolExecutor 并发执行工具
  │   ├─ 收集工具结果
  │   └─ 应用 context_modifiers
  ├─ Phase 4: collect_results
  │   ├─ 追加 assistant_message 到 messages
  │   ├─ 追加 tool_result 消息到 messages
  │   └─ 持久化到 SessionStorage
  ├─ Phase 5: post_turn
  │   ├─ 触发 Memory 提取（后台任务）
  │   ├─ 判断终止条件（stop_reason）
  │   └─ 更新 QueryState
  └─ 循环或终止
```

---

## 阅读路线

### 第一层：核心状态机（必读）

#### 1. [codo/query.py](../codo/query.py) - 主循环引擎
**关键函数：**
- `query_loop()` (L400-L800) - 核心 while(true) 循环
- `QueryState` (L100-L188) - 状态机数据结构
- `QueryParams` (L191-L247) - 不可变参数快照
- `QueryPhaseTracker` (L273-L350) - 阶段追踪器

**阅读重点：**
```python
# 状态机核心循环
async for event in query_loop(params, initial_state):
    # 每轮迭代：
    # 1. 解构 state → 局部变量
    # 2. 执行当前 phase 逻辑
    # 3. 更新局部状态
    # 4. 重新组装为新的 QueryState
    # 5. 记录 checkpoint
    # 6. 判断终止条件
```

**关键状态字段：**
- `messages` - 消息历史
- `turn_count` - 轮次计数
- `phase` - 当前执行阶段
- `auto_compact_tracking` - 压缩状态追踪
- `active_tool_ids` - 当前活动工具
- `checkpoint_id` - 最近检查点

#### 2. [codo/runtime_protocol.py](../codo/runtime_protocol.py) - 运行时协议
**关键类：**
- `QueryRuntimeController` (L46-L137) - 双向运行时桥接
- `RuntimeEvent` (L31-L37) - 运行时事件
- `RuntimeCheckpoint` (L22-L27) - 检查点快照
- `RuntimeCommand` (L40-L43) - 运行时命令

**核心机制：**
```python
# 事件流：query_loop → UI
await runtime_controller.emit_runtime_event("turn_started", turn_count=1)

# 命令流：UI → query_loop
command = await runtime_controller.next_command()

# 交互请求：query_loop ↔ UI
result = await runtime_controller.request_interaction(request)

# 检查点：保存状态快照
runtime_controller.checkpoint(RuntimeCheckpoint(...))
```

#### 3. [codo/query_engine.py](../codo/query_engine.py) - 引擎封装
**关键方法：**
- `__init__()` (L132-L200) - 初始化运行时依赖
- `submit_message_stream()` (L250-L400) - 提交消息并启动循环
- `restore_session()` (L450-L500) - 恢复会话历史

**职责：**
- 管理 API 客户端、工具池、会话存储
- 组装 QueryParams 并调用 query()
- 处理会话持久化
- 管理 MCP 工具、Memory 提取

---

### 第二层：工具执行系统（核心）

#### 4. [codo/services/tools/streaming_executor.py](../codo/services/tools/streaming_executor.py) - 流式工具执行器
**关键类：**
- `StreamingToolExecutor` (L99-L500) - 流式执行器
- `TrackedTool` (L54-L74) - 工具状态追踪
- `ToolStatus` (L42-L51) - 工具状态枚举

**核心流程：**
```python
executor = StreamingToolExecutor(tools, context)

# 1. 添加工具（在 API 流式响应期间）
executor.add_tool(tool_use_block, assistant_message)
# → 立即开始执行（如果并发安全）

# 2. 增量获取结果
async for update in executor.get_completed_results():
    # update.message - tool_result 消息
    # update.context_modifier - 上下文修改器
    # update.receipt - 结构化收据

# 3. 等待全部完成
await executor.wait_all()
```

**并发规则：**
- `concurrent-safe` 工具（Read, Grep, Glob）→ 并行执行
- `non-concurrent` 工具（Bash, Write, Edit）→ 独占执行
- Bash 错误 → 触发 sibling abort（取消并行工具）

#### 5. [codo/services/tools/orchestration.py](../codo/services/tools/orchestration.py) - 工具编排
**关键函数：**
- `partition_tool_calls()` (L54-L130) - 批处理分区
- `run_tools_batch()` (L150-L250) - 批量执行入口
- `run_batch_concurrently()` (L280-L350) - 并发执行
- `run_batch_serially()` (L360-L420) - 串行执行

**分区策略：**
```python
# 输入: [Read, Read, Bash, Grep]
# 分区: [[Read, Read], [Bash], [Grep]]
# 执行: [并发批次] → [串行] → [串行]
```

#### 6. [codo/services/tools/error_handler.py](../codo/services/tools/error_handler.py) - 错误处理
**职责：**
- 工具执行错误捕获
- 重试逻辑（可配置）
- 错误格式化（返回给模型）
- Sibling abort 触发

---

### 第三层：上下文管理系统

#### 7. [codo/services/compact/compact.py](../codo/services/compact/compact.py) - 上下文压缩
**关键类：**
- `AutoCompactState` (L64-L110) - 自动压缩状态追踪
- `CompactResult` (L42-L62) - 压缩结果封装

**核心函数：**
- `auto_compact_if_needed()` (L150-L250) - 自动压缩判断
- `compact_conversation()` (L300-L500) - 执行压缩

**压缩流程：**
```python
# 1. 判断是否需要压缩
if current_tokens > auto_compact_threshold:
    # 2. 调用模型生成摘要
    summary = await compact_conversation(client, messages, system_prompt)

    # 3. 替换消息历史
    new_messages = [
        compact_boundary_message,
        summary_message
    ]

    # 4. 重新注入关键上下文（CODO.md、近期文件）
    # 5. 更新 AutoCompactState
```

**触发条件：**
- 自动触发：`current_tokens > (context_window - 13000)`
- 响应式触发：API 返回 `prompt_too_long` 错误
- Circuit breaker：连续失败 3 次后停止尝试

#### 8. [codo/services/compact/microcompact.py](../codo/services/compact/microcompact.py) - 微压缩
**职责：**
- 压缩单个工具结果（如大文件内容）
- 在追加到 messages 前执行
- 避免单个消息占用过多 token

#### 9. [codo/services/token_estimation.py](../codo/services/token_estimation.py) - Token 估算
**关键函数：**
- `estimate_messages_tokens()` - 估算消息列表 token 数
- `calculate_token_warning_state()` - 计算警告状态

**阈值定义：**
```python
effective_window = 200000  # Opus 4.7
auto_compact_threshold = effective_window - 13000  # 187000
warning_threshold = auto_compact_threshold - 20000  # 167000
blocking_limit = effective_window - 3000  # 197000
```

---

### 第四层：记忆与持久化

#### 10. [codo/services/memory/extract.py](../codo/services/memory/extract.py) - 记忆提取
**关键类：**
- `MemoryExtractionState` (L42-L65) - 提取状态追踪

**核心函数：**
- `extract_memories()` (L150-L300) - 后台提取任务

**提取流程：**
```python
# 1. 统计新增消息
new_message_count = count_model_visible_since(messages, last_uuid)

# 2. 扫描现有记忆文件
memory_manifest = scan_memory_files(memory_dir)

# 3. 构建提取提示词
prompt = build_extract_prompt(new_messages, memory_manifest)

# 4. 调用模型执行提取（单独 API 调用）
response = await client.messages.create(...)

# 5. 解析 tool_use 块（Write/Edit）
# 6. 在 memory 目录中执行工具
```

**触发时机：**
- 每轮对话结束后（后台异步执行）
- 可配置提取频率（默认每轮）

#### 11. [codo/session/storage.py](../codo/session/storage.py) - 会话存储
**关键函数：**
- `append_message()` (L200-L300) - 追加消息到 JSONL
- `append_event()` (L350-L400) - 追加事件到 JSONL
- `load_session()` (L500-L700) - 加载会话历史

**存储格式：**
```jsonl
{"type": "message", "role": "user", "content": "...", "uuid": "..."}
{"type": "message", "role": "assistant", "content": [...], "uuid": "..."}
{"type": "tool_result", "tool_use_id": "...", "content": "..."}
{"type": "event", "event_type": "turn_completed", "turn_count": 1}
```

**持久化时机：**
- 每条消息追加后立即写入
- 每个事件发生后立即写入
- 使用 JSONL 格式（追加友好）

---

### 第五层：API 与错误处理

#### 12. [codo/services/api/errors.py](../codo/services/api/errors.py) - API 错误处理
**关键函数：**
- `classify_api_error()` - 错误分类
- `is_retryable()` - 判断是否可重试
- `format_api_error()` - 格式化错误消息
- `with_retry()` - 重试装饰器

**错误分类：**
```python
class APIErrorCategory(Enum):
    RATE_LIMIT = "rate_limit"           # 速率限制 → 重试
    PROMPT_TOO_LONG = "prompt_too_long" # 上下文过长 → 触发 compact
    OVERLOADED = "overloaded"           # 服务过载 → 重试
    AUTHENTICATION = "authentication"   # 认证失败 → 不重试
    NETWORK = "network"                 # 网络错误 → 重试
    UNKNOWN = "unknown"                 # 未知错误 → 不重试
```

#### 13. [codo/services/prompt/builder.py](../codo/services/prompt/builder.py) - Prompt 构建
**职责：**
- 组装系统提示词
- 注入上下文（CODO.md、Memory、工具列表）
- 处理附件（图片、文件）

---

## 关键数据流

### 1. 消息流（Messages Flow）
```
用户输入
  ↓
{"role": "user", "content": "...", "uuid": "..."}
  ↓
query_loop() → API 调用
  ↓
{"role": "assistant", "content": [text_block, tool_use_block], "uuid": "..."}
  ↓
StreamingToolExecutor → 执行工具
  ↓
{"role": "user", "content": [tool_result_block], "uuid": "..."}
  ↓
追加到 messages → 持久化到 JSONL
  ↓
下一轮循环
```

### 2. 事件流（Events Flow）
```
query_loop()
  ↓
runtime_controller.emit_runtime_event("turn_started")
  ↓
QueryRuntimeController._events Queue
  ↓
UI 层消费（Textual TUI）
  ↓
显示进度、工具执行状态、Token 用量
```

### 3. 检查点流（Checkpoint Flow）
```
query_loop() 每个 phase
  ↓
_record_runtime_checkpoint(phase="prepare_turn", turn_count=1)
  ↓
RuntimeCheckpoint(checkpoint_id, phase, turn_count, metadata)
  ↓
runtime_controller.checkpoint()
  ↓
存储到 _checkpoints: dict[str, RuntimeCheckpoint]
  ↓
用于调试、恢复、分析
```

---

## 核心状态机阶段

```python
# query_loop() 的主要阶段
phases = [
    "prepare_turn",           # 准备轮次（token 估算、compact 判断）
    "stream_assistant",       # 流式接收 API 响应
    "collect_tool_calls",     # 收集 tool_use 块
    "execute_tools",          # 执行工具
    "wait_interaction",       # 等待用户交互（如权限确认）
    "apply_interaction_result", # 应用交互结果
    "collect_results",        # 收集工具结果
    "stop_hooks",             # 执行停止钩子
    "compact",                # 执行压缩（如果需要）
    "post_turn",              # 轮次后处理（Memory 提取）
    "complete",               # 完成
]
```

---

## 并发模型

### 工具执行并发
```python
# StreamingToolExecutor 并发策略
concurrent_safe_tools = [Read, Grep, Glob, WebFetch]
non_concurrent_tools = [Bash, Write, Edit, Agent]

# 执行规则：
# 1. concurrent_safe 工具可以并行执行（最多 max_concurrency=10）
# 2. non_concurrent 工具获得独占访问（等待所有并发工具完成）
# 3. Bash 错误触发 sibling abort（取消所有并行工具）
```

### Memory 提取并发
```python
# Memory 提取在后台异步执行
asyncio.create_task(extract_memories(...))

# 不阻塞主循环，下一轮对话可以立即开始
# 使用 in_progress 标志防止重叠执行
```

---

## 推荐阅读顺序

### 快速理解（1小时）
1. [query.py:query_loop()](../codo/query.py#L400-L800) - 看主循环逻辑
2. [runtime_protocol.py:QueryRuntimeController](../codo/runtime_protocol.py#L46-L137) - 看运行时协议
3. [streaming_executor.py:StreamingToolExecutor](../codo/services/tools/streaming_executor.py#L99-L500) - 看工具执行

### 深入理解（3小时）
4. [orchestration.py](../codo/services/tools/orchestration.py) - 工具编排细节
5. [compact.py](../codo/services/compact/compact.py) - 上下文压缩机制
6. [extract.py](../codo/services/memory/extract.py) - 记忆提取流程
7. [storage.py](../codo/session/storage.py) - 会话持久化

### 完整掌握（6小时）
8. [query_engine.py](../codo/query_engine.py) - 引擎封装
9. [error_handler.py](../codo/services/tools/error_handler.py) - 错误处理
10. [errors.py](../codo/services/api/errors.py) - API 错误分类
11. [token_estimation.py](../codo/services/token_estimation.py) - Token 管理
12. [builder.py](../codo/services/prompt/builder.py) - Prompt 构建

---

## 调试技巧

### 1. 启用详细日志
```python
# 在 query_engine.py 中设置 verbose=True
engine = QueryEngine(api_key=..., verbose=True)

# 查看日志输出
# - 每轮 token 用量
# - 工具执行状态
# - Compact 触发时机
# - Memory 提取结果
```

### 2. 检查 Checkpoint
```python
# 导出所有检查点
checkpoints = runtime_controller.export_checkpoints()

# 查看特定阶段的状态
checkpoint = runtime_controller.get_checkpoint(checkpoint_id)
print(checkpoint.phase, checkpoint.turn_count, checkpoint.metadata)
```

### 3. 追踪工具执行
```python
# 在 StreamingToolExecutor 中查看工具状态
for tool in executor.tools:
    print(f"{tool.id}: {tool.status}, duration={tool.duration}")
```

### 4. 分析会话文件
```bash
# 查看 JSONL 会话文件
cat .codo/sessions/<session_id>/transcript.jsonl | jq .

# 统计消息数量
grep '"type":"message"' transcript.jsonl | wc -l

# 查看 Compact 事件
grep '"event_type":"compact"' transcript.jsonl | jq .
```

---

## 性能优化点

### 1. Token 管理
- 及时触发 auto_compact（阈值：187000）
- 使用 microcompact 压缩大文件内容
- 避免重复注入相同上下文

### 2. 工具执行
- 最大化并发安全工具的并行度
- 避免不必要的 Bash 调用（使用专用工具）
- 实现工具结果缓存（如 Read 工具）

### 3. Memory 提取
- 调整提取频率（默认每轮 → 每 N 轮）
- 使用增量提取（只处理新增消息）
- 避免重复扫描记忆文件

### 4. 会话持久化
- 使用 JSONL 追加写入（避免重写整个文件）
- 批量写入事件（减少 I/O）
- 定期清理旧会话文件

---

## 常见问题

### Q1: 为什么工具没有并行执行？
A: 检查工具的 `is_concurrency_safe` 属性。只有标记为 `True` 的工具才能并行执行。

### Q2: 为什么 Compact 没有触发？
A: 检查：
1. Token 用量是否超过阈值（187000）
2. Circuit breaker 是否触发（连续失败 3 次）
3. 是否已执行过响应式 compact

### Q3: 为什么 Memory 没有提取？
A: 检查：
1. 是否有新增消息（user/assistant）
2. 提取任务是否正在执行（in_progress=True）
3. 提取频率配置（extraction_interval）

### Q4: 如何恢复会话？
A: 使用 `QueryEngine.from_session_id()` 或 `restore_session()` 方法，会自动加载 JSONL 历史。

---

## 总结

Codo 的 Runtime 核心是一个**状态机驱动的对话循环**，通过以下机制实现高效的 AI Agent 执行：

1. **状态机循环** - query_loop() 管理对话生命周期
2. **流式工具执行** - StreamingToolExecutor 并发执行工具
3. **上下文管理** - 自动压缩、Token 估算、微压缩
4. **记忆系统** - 后台提取、持久化、增量更新
5. **错误恢复** - 分类、重试、Circuit breaker
6. **运行时协议** - 事件流、命令流、检查点

理解这些核心机制后，你就能完整掌握 Codo 的运行时行为。
