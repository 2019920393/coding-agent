# 对话循环引擎设计演练：从 0 到 1

> 和会话系统、工具系统一样，本文档用"遇到问题 → 思考 → 写代码 → 下一个问题"的方式，带你从零设计整个对话循环引擎。

---

## Stage 0：为什么需要对话循环？

### 问题

Claude API 的调用是**一次性的**：你发消息，它回消息，结束。

但 AI 助手需要**多轮交互**：

```
用户: "帮我把 src/main.py 的 TODO 注释删掉"
AI:   "好的，让我先读取文件" → 调用 Read 工具
AI:   "找到了 3 个 TODO，让我逐个删除" → 调用 Edit 工具（3 次）
AI:   "已完成，删除了 3 个 TODO 注释"
```

这里 AI 和 API 交互了 **5 次**（1 次用户消息 + 4 次工具调用）。如果只调一次 API，AI 只能说"我会帮你删"，但没法真正执行。

### 思考

需要一个**循环**：

```
用户输入
    │
    ▼
┌──────────────────────┐
│ 调用 API              │ ← 第 1 轮
│ 收到响应              │
│   └→ 有 tool_use？    │
│       ├─ 是 → 执行工具，把结果加入消息，回到顶部
│       └─ 否 → 结束循环
└──────────────────────┘
```

每次循环叫做一个 **turn**（轮次）。一个用户请求可能需要多个 turn 才能完成。

### 核心挑战

1. **什么时候停？** — AI 不调工具了（`stop_reason != "tool_use"`）
2. **消息怎么管理？** — 每轮都要把完整历史发给 API
3. **工具结果怎么拼回去？** — `tool_result` 消息格式
4. **token 超了怎么办？** — 对话越来越长，会超出 context window
5. **出错了怎么办？** — API 限流、超时、prompt 太长
6. **用户想中断怎么办？** — Ctrl+C

一个一个来。

---

## Stage 1：最简单的循环

### 问题

先不考虑工具，只实现"用户说话 → AI 回话"。

### 代码

```python
async def simple_loop(client, messages, system_prompt):
    while True:
        # 1. 调用 API
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )

        # 2. 把 AI 的回复加入消息历史
        messages.append({"role": "assistant", "content": response.content})

        # 3. 检查是否结束
        if response.stop_reason == "end_turn":
            break

        # 4. 如果有 tool_use，暂时不管，先退出
        print("AI 想调用工具，但我们还没实现")
        break
```

### 问题

这只能单轮对话。AI 调不了工具，就做不了任何实际操作。

### 下一个问题

怎么让 AI 能调用工具？

---

## Stage 2：加入工具调用

### 问题

AI 返回 `tool_use` 时，我们需要：
1. 执行工具
2. 把结果作为 `tool_result` 追加到消息
3. 再次调用 API

### 代码

```python
async def loop_with_tools(client, messages, system_prompt, tool_schemas, tools):
    while True:
        # 1. 调用 API（带上工具定义）
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=tool_schemas,  # ← 告诉 AI 有哪些工具
        )

        # 2. 把 AI 的回复加入消息
        assistant_message = {"role": "assistant", "content": response.content}
        messages.append(assistant_message)

        # 3. 检查是否有 tool_use
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # AI 没有调用工具，对话结束
            break

        # 4. 执行每个工具
        tool_results = []
        for block in tool_use_blocks:
            tool = find_tool(tools, block.name)
            result = await tool.call(block.input, ...)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": format_result(result),
            })

        # 5. 把工具结果追加到消息
        messages.append({"role": "user", "content": tool_results})

        # 6. 回到 while 循环顶部，再次调用 API
```

### 循环图

```
Turn 1:
  用户: "删掉 TODO"
  API → assistant: [text("好的"), tool_use(Read, "src/main.py")]
  执行 Read → "def main():\n    # TODO: fix\n    ..."
  追加 tool_result

Turn 2:
  API → assistant: [text("找到了"), tool_use(Edit, old="# TODO: fix", new="")]
  执行 Edit → "Updated 1 line"
  追加 tool_result

Turn 3:
  API → assistant: [text("已删除 3 个 TODO")]
  stop_reason = "end_turn" → 循环结束
```

### 下一个问题

这个循环把所有逻辑都塞在一起了。实际项目中，谁负责管理会话状态，谁负责跑循环？

---

## Stage 3：QueryEngine vs query() — 职责分离

### 问题

循环涉及两件事：
1. **会话管理**：消息持久化、工具注册、系统提示词构建、中断控制
2. **循环逻辑**：调 API、执行工具、判断是否继续

如果全塞在一个函数里，会变成 2000 行的怪物。

### 思考

分成两层：

```
┌─────────────────────────────────────────┐
│  QueryEngine（高层协调器）                 │
│                                         │
│  - 管理会话状态（messages, session_id）    │
│  - 初始化工具、构建系统提示词               │
│  - 调用 query() 主循环                   │
│  - 处理会话持久化                         │
│  - 处理中断控制                           │
└──────────────────┬──────────────────────┘
                   │ 调用
                   ▼
┌─────────────────────────────────────────┐
│  query() / query_loop()（核心循环）       │
│                                         │
│  - while True 循环                      │
│  - 调用 API                             │
│  - 解析响应                              │
│  - 执行工具                              │
│  - 判断是否继续                           │
│  - 处理错误和重试                         │
└─────────────────────────────────────────┘
```

### QueryEngine 的职责

```python
# codo/query_engine.py

class QueryEngine:
    async def submit_message_stream(self, prompt):
        """高层入口：接收用户消息，产出流式事件"""

        # 1. 追加用户消息
        self.messages.append({"role": "user", "content": prompt})

        # 2. 构建系统提示词
        system_prompt = self.prompt_builder.build_system_prompt()

        # 3. 组装参数
        params = QueryParams(
            client=self.client,
            model=self.model,
            system_prompt=system_prompt,
            messages=self.messages.copy(),  # 传副本
            tools=self.tools,
            tool_schemas=self.tool_schemas,
            execution_context=self.execution_context,
            max_turns=self.max_turns,
            ...
        )

        # 4. 调用 query() 主循环，转发事件
        async for event in query(params):
            yield event  # 转发给调用方

        # 5. 同步消息历史（从 session_storage 重新加载）
        if self.session_storage:
            self.messages = self.session_storage.load_messages()
```

### query_loop 的职责

```python
# codo/query.py

async def query_loop(params: QueryParams):
    """核心循环：调 API → 执行工具 → 判断是否继续"""
    state = QueryState(messages=params.messages, turn_count=1)

    while True:
        # ... 调用 API、执行工具、判断是否继续 ...
        # 不直接操作 QueryEngine，只通过 params 和 state 管理
```

### 设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 消息副本 | `self.messages.copy()` 传给 query() | 避免 query() 直接修改引擎状态 |
| 状态同步 | Terminal 后从 session_storage 重新加载 | 保证与持久化一致 |
| 参数不可变 | QueryParams 是 dataclass，不修改 | 避免循环内意外修改外部状态 |

### 下一个问题

query_loop 内部的状态怎么管理？

---

## Stage 4：while-true 状态机 — QueryState

### 问题

循环内有很多可变状态：消息列表、轮次计数、压缩标志、重试次数……如果用散落的变量管理，很容易出 bug。

### 思考

用一个 **QueryState** dataclass 把所有状态打包。每次 `continue` 时，创建新的 state 实例（类似不可变更新）。

```python
# codo/query.py

@dataclass
class QueryState:
    # 核心状态
    messages: List[Dict]       # 完整消息历史
    turn_count: int = 1        # 当前轮次

    # Token 管理
    auto_compact_tracking: Optional[AutoCompactState] = None
    has_attempted_reactive_compact: bool = False
    max_output_tokens_recovery_count: int = 0
    max_output_tokens_override: Optional[int] = None

    # 执行状态
    phase: str = "prepare_turn"
    pending_interaction: Optional[Dict] = None
    active_tool_ids: List[str] = field(default_factory=list)

    # 调试信息
    transition: Optional[Dict] = None
```

### 每轮循环的生命周期

```python
while True:
    # 解构 state → 局部变量
    messages = state.messages
    turn_count = state.turn_count
    ...

    # Step 1: 准备轮次
    # Step 2: Token 管理
    # Step 3: 调用 API
    # Step 4: 收集工具调用
    # Step 5: 执行工具
    # Step 6: 判断是否继续

    if needs_follow_up:
        # 有 tool_use → 下一轮
        state = QueryState(messages=new_messages, turn_count=turn_count + 1, ...)
        continue
    else:
        # 没有 tool_use → 结束
        yield Terminal(reason="completed")
        return
```

### 状态转换图

```
prepare_turn
    │
    ▼
stream_assistant ──(API 调用)──→ collect_tool_calls
    │                                    │
    │                                    ▼
    │                             execute_tools
    │                                    │
    │                   ┌────────────────┤
    │                   │                │
    │                   ▼                ▼
    │            has tool_use      no tool_use
    │                   │                │
    │                   ▼                ▼
    │            prepare_turn      stop_hooks
    │            (turn+1)               │
    │                                   ▼
    │                              complete → Terminal
    │
    ├──(error)──→ error → Terminal
    └──(max_tokens, no tools)──→ 自动 Continue → prepare_turn
```

### 下一个问题

API 调用是怎么流式工作的？

---

## Stage 5：流式 API 调用

### 问题

Claude API 的响应不是一次性返回的，而是**流式**（streaming）的。我们需要边接收边处理。

### 思考

Anthropic API 的流式事件类型：

```
message_start          → 消息开始
content_block_start    → 新内容块开始（text / thinking / tool_use）
content_block_delta    → 内容增量（文字逐字出现、JSON 逐步拼装）
content_block_stop     → 内容块结束
message_stop           → 消息结束
```

### 代码

```python
# codo/query.py（简化）

# 1. 创建流式执行器
streaming_tool_executor = StreamingToolExecutor(max_concurrency=10)

# 2. 调用 API（流式）
async with client.messages.stream(**api_kwargs) as stream:
    async for event in stream:
        if event.type == "content_block_start":
            # 新内容块开始
            if event.content_block.type == "text":
                assistant_message["content"].append({"type": "text", "text": ""})
            elif event.content_block.type == "tool_use":
                block = {"type": "tool_use", "id": ..., "name": ..., "input": {}}
                assistant_message["content"].append(block)
                streaming_tool_executor.register_tool(block)  # 注册，不执行

        elif event.type == "content_block_delta":
            # 增量更新
            if event.delta.type == "text_delta":
                current_block["text"] += event.delta.text
                yield {"type": "text_delta", "text": event.delta.text}  # 实时显示
            elif event.delta.type == "input_json_delta":
                current_block["input_json_str"] += event.delta.partial_json

        elif event.type == "content_block_stop":
            yield {"type": "content_block_stop", "index": ...}

    # 3. 获取完整响应
    final_message = await stream.get_final_message()
    stop_reason = final_message.stop_reason
```

### 关键点

- **text_delta** 实时 yield 给 UI，用户看到文字逐字出现
- **tool_use** 只是注册到 executor，**不立即执行**（input 还不完整）
- **input_json_delta** 逐步拼装 JSON 字符串，最后 `json.loads` 解析
- **final_message** 拿到完整数据后才开始执行工具

### 下一个问题

API 流式响应处理完了，工具也注册了。但工具具体怎么执行？并发怎么控制？错误怎么处理？

---

## Stage 5.5：流式工具执行器 — StreamingToolExecutor

### 问题

AI 一次可能调用多个工具。比如：

```
tool_use[0]: Read("a.py")     ← 并发安全
tool_use[1]: Read("b.py")     ← 并发安全
tool_use[2]: Bash("npm test") ← 非并发安全
```

Read 和 Read 可以同时跑，但 Bash 必须等前面的 Read 都完成才能开始。而且如果 Bash 报错了，正在并行的其他工具也要取消。

### 思考

StreamingToolExecutor 是一个**工具执行队列**，负责：
1. 注册工具（streaming 阶段）
2. 管理并发（哪些可以同时跑）
3. 执行工具（验证输入 → 权限检查 → 调用 call()）
4. 收集结果（已完成的先返回，未完成的等待）
5. 处理错误（Bash 错误触发 sibling abort）

### 核心数据结构

```python
class TrackedTool:
    """一个被跟踪的工具调用"""
    id: str                          # tool_use_id
    block: Dict                      # {id, name, input}
    status: ToolStatus               # QUEUED → EXECUTING → COMPLETED → YIELDED
    is_concurrency_safe: bool        # 是否可以并发
    results: List[Dict]              # 执行结果
    promise: Optional[asyncio.Task]  # 异步任务引用
    receipt: Optional[ToolReceipt]   # 结构化收据
    staged_changes: List             # 暂存的文件变更

class ToolStatus(Enum):
    QUEUED = "queued"                # 已注册，等待执行
    EXECUTING = "executing"          # 正在执行
    WAITING_INTERACTION = "waiting"  # 等待用户交互（如权限确认）
    COMPLETED = "completed"          # 执行完成
    YIELDED = "yielded"              # 结果已返回给调用方
```

### 工具的生命周期

```
register_tool()
    │  注册到队列，状态 = QUEUED
    ▼
_process_queue()
    │  检查是否可以执行（并发规则）
    ▼
_start_tool_execution()
    │  状态 = EXECUTING，创建 asyncio.Task
    ▼
_execute_tool_with_abort()
    │  执行 + 监听 abort 信号
    ▼
_execute_tool()
    │  验证输入 → 权限检查 → tool.call()
    ▼
状态 = COMPLETED，存储结果
    │
    ▼
get_completed_results()
    │  状态 = YIELDED，返回给调用方
```

### 并发控制：_can_execute_tool()

```python
def _can_execute_tool(self, tool: TrackedTool) -> bool:
    executing = [t for t in self.tools if t.status == EXECUTING]

    # 没有工具在执行 → 总是可以启动
    if not executing:
        return True

    # 并发规则：
    # 1. 当前工具必须是 concurrent-safe
    # 2. 所有正在执行的工具也必须是 concurrent-safe
    # 3. 并发数不能超过 max_concurrency
    if tool.is_concurrency_safe and all(t.is_concurrency_safe for t in executing):
        if len(executing) < self.max_concurrency:
            return True

    return False
```

### 队列处理：_process_queue()

```python
async def _process_queue(self):
    for tool in self.tools:
        if tool.status != QUEUED:
            continue

        if self._can_execute_tool(tool):
            await self._start_tool_execution(tool)
        elif not tool.is_concurrency_safe:
            # Non-concurrent 工具被阻塞 → 停止处理后续工具
            break
```

关键：遇到非并发安全的工具且它不能执行时，**直接 break**，后面的工具也不处理了。这保证了执行顺序。

### 工具执行：_execute_tool()

```python
async def _execute_tool(self, tool: TrackedTool):
    # 1. 查找工具实例
    tool_instance = self._find_tool(tool.block["name"])

    # 2. 解析输入（Pydantic 验证）
    tool_input = tool_instance.input_schema(**tool.block["input"])

    # 3. 自定义验证
    validation = await tool_instance.validate_input(tool_input, self.context)
    if not validation.result:
        tool.results = [error_message]
        return

    # 4. 权限检查
    permission = await self._check_tool_permission(tool_instance, tool_input, tool)
    if permission == "deny":
        tool.results = [permission_error]
        return
    if permission == "abort":
        self.sibling_abort_event.set()  # 触发全局中止
        return

    # 5. 执行工具
    result = await tool_instance.call(tool_input, self.context, ...)

    # 6. 处理 Bash 错误 → sibling abort
    if result.error and tool.block["name"] == "Bash":
        self.sibling_abort_event.set()  # 取消所有并行工具

    # 7. 格式化结果
    tool.results = [self._format_tool_result(tool, result)]
    tool.status = COMPLETED
```

### Sibling Abort — Bash 错误连锁取消

这是 StreamingToolExecutor 最重要的特性之一。

```
场景：AI 同时调用了 Read("a.py") 和 Bash("mkdir foo && cd foo && npm init")

并行执行中:
  Read("a.py")        → 正在执行...
  Bash("mkdir foo")   → 失败！(目录已存在)

Bash 失败 → sibling_abort_event.set()
    │
    ▼
Read("a.py") 收到 abort 信号 → 取消执行 → 返回 synthetic error
```

为什么？因为 Bash 命令通常有隐式依赖链。`mkdir` 失败意味着后续的 `cd` 和 `npm init` 也没意义了。并行的其他工具也可能依赖 Bash 的结果。

```python
# 在 _execute_tool_with_abort() 中
execute_task = asyncio.create_task(self._execute_tool(tool))
abort_task = asyncio.create_task(self.sibling_abort_event.wait())

done, pending = await asyncio.wait(
    [execute_task, abort_task],
    return_when=asyncio.FIRST_COMPLETED
)

if abort_task in done:
    # abort 触发了，取消执行中的工具
    tool.results = [self._create_synthetic_error(tool, "sibling_error")]
```

### 结果收集：get_completed_results() vs get_remaining_results()

```python
def get_completed_results(self) -> List[ToolUpdate]:
    """非阻塞，立即返回已完成的结果"""
    results = []
    for tool in self.tools:
        if tool.status == COMPLETED:
            tool.status = YIELDED
            results.append(ToolUpdate(message=tool.results[0], ...))

        # 在第一个未完成的 non-concurrent 工具处停止
        elif tool.status == EXECUTING and not tool.is_concurrency_safe:
            break  # 不返回后面的结果（保证顺序）

    return results

async def get_remaining_results(self) -> AsyncGenerator[ToolUpdate]:
    """阻塞，等待所有未完成的工具"""
    while self._has_unfinished_tools():
        await self._process_queue()           # 推进队列
        for result in self.get_completed_results():
            yield result                      # 返回已完成的
        await asyncio.wait(executing_tasks,   # 等待任意一个完成
                          return_when=FIRST_COMPLETED)
```

### 完整示例

```
AI 返回: [Read("a.py"), Read("b.py"), Bash("npm test"), Grep("TODO")]

Step 1: 注册（streaming 阶段）
  register_tool(Read("a.py"))    → QUEUED, concurrency_safe=True
  register_tool(Read("b.py"))    → QUEUED, concurrency_safe=True
  register_tool(Bash("npm test"))→ QUEUED, concurrency_safe=False
  register_tool(Grep("TODO"))    → QUEUED, concurrency_safe=True

Step 2: _process_queue()（final_message 后启动）
  Read("a.py")    → 可以执行（没有正在执行的）→ EXECUTING
  Read("b.py")    → 可以执行（a.py 是 concurrent-safe）→ EXECUTING
  Bash("npm test")→ 不能执行（需要等 Read 完成）→ 保持 QUEUED，break

Step 3: Read("a.py") 完成
  → on_done 回调触发 _process_queue()
  → Bash("npm test") → 可以执行（没有正在执行的了）→ EXECUTING
  → Grep("TODO") → 不能执行（Bash 不是 concurrent-safe）→ break

Step 4: Bash("npm test") 完成
  → _process_queue()
  → Grep("TODO") → 可以执行 → EXECUTING

Step 5: Grep("TODO") 完成
  → 所有工具完成

结果返回顺序: Read("a.py"), Read("b.py"), Bash("npm test"), Grep("TODO")
```

### 下一个问题

消息发给 API 之前，需要做什么预处理？

---

## Stage 6：消息预处理 — 发给 API 之前

### 问题

消息历史不能直接发给 API。需要：
1. 过滤掉不该发的消息（虚拟消息、附件元数据）
2. 确保角色交替（user → assistant → user，不能两个 user 连续）
3. 添加缓存断点（利用 Anthropic 的 prompt caching）

### 思考

三个步骤：

```
原始 messages
    │
    ▼
normalize_messages_for_api()   ← 过滤 + 合并 + 角色交替
    │
    ▼
add_cache_breakpoints()        ← 在最后一个 user 消息加 cache_control
    │
    ▼
发给 API
```

### Step 1: normalize_messages_for_api

```python
def normalize_messages_for_api(messages):
    result = []
    for msg in messages:
        # 1. 过滤虚拟消息（is_virtual: true）
        if msg.get("is_virtual"):
            continue

        # 2. 转换附件消息（ide_selection, queued_command 等）
        #    → 包裹在 <system-reminder> 标签里

        # 3. 角色标准化（只允许 "user" 和 "assistant"）

        result.append(msg)

    # 4. 确保角色交替（合并连续同角色消息）
    result = ensure_alternating_messages(result)
    return result
```

### Step 2: add_cache_breakpoints

```python
def add_cache_breakpoints(messages):
    """在最后一个 user 消息添加缓存控制"""
    for msg in reversed(messages):
        if msg["role"] == "user":
            # 给最后一个文本块加 cache_control
            if isinstance(msg["content"], str):
                msg["content"] = [{"type": "text", "text": msg["content"],
                                   "cache_control": {"type": "ephemeral"}}]
            elif isinstance(msg["content"], list):
                # 给最后一个 text block 加 cache_control
                ...
            break
```

### 为什么要缓存？

Anthropic 的 prompt caching 机制：如果系统提示词和历史消息带 `cache_control`，API 会缓存这部分内容，下次请求时不用重新处理，**省 token 费用 + 加快速度**。

```
请求 1: system_prompt(cache) + messages[0..9](最后一条 cache)
请求 2: system_prompt(命中缓存) + messages[0..14](最后一条 cache)
         ↑ 省了 system_prompt 的处理
```

### 系统提示词怎么构建？

```python
# codo/services/prompt/builder.py

class PromptBuilder:
    def build_system_prompt(self):
        sections = [
            get_simple_intro_section(),      # 角色定义
            get_system_section(),             # 输出规则
            get_doing_tasks_section(),        # 任务执行指南
            get_actions_section(),            # 操作安全指南
            get_using_tools_section(),        # 工具使用指南
            get_tone_and_style_section(),     # 语气风格
            get_environment_section(),        # 环境信息（cwd, git）
            get_user_context(),               # CODO.md 内容
            get_memory_section(),             # MEMORY.md 索引
        ]
        return "\n\n".join(sections)
```

### 下一个问题

对话越来越长，token 超了怎么办？

---

## Stage 7：Token 管理 — 三级防护

### 问题

每次 API 调用都要发送完整消息历史。随着对话进行，token 数会不断增加：
- 第 1 轮：~1000 tokens
- 第 10 轮：~10000 tokens
- 第 50 轮：~100000 tokens → 超出 context window

### 思考

三级防护，从轻到重：

```
Level 1: Microcompact（微压缩）
  - 清除 60 分钟前的旧工具结果
  - 代价最低，只替换文本

Level 2: Auto-compact（自动压缩）
  - 用 AI 总结整个对话
  - 用摘要替换所有历史消息
  - 代价较高（要调一次 API）

Level 3: Blocking limit（阻塞限制）
  - 硬限制，直接停止
  - 防止超出 context window
```

### Level 1: Microcompact

```python
# codo/services/compact/microcompact.py

async def microcompact_if_needed(messages, context):
    """
    清除旧工具结果，释放 token

    规则：
    - 保护最近 5 个工具结果（不清除）
    - 60 分钟前的工具结果 → 替换为 "[Old tool result content cleared]"
    - 只处理特定工具（Read, Bash, Grep, Glob, ...）
    """
    compacted_count = 0
    for msg in messages:
        if is_old_tool_result(msg, gap_threshold_minutes=60):
            if not is_protected(msg, keep_recent=5):
                msg["content"] = "[Old tool result content cleared]"
                compacted_count += 1

    return MicrocompactResult(compacted_count=compacted_count)
```

### Level 2: Auto-compact

```python
# 如果 token 超过阈值，用 AI 总结对话
if token_count >= auto_compact_threshold:
    # 1. 把整个对话发给 AI，让它总结
    summary = await compact_conversation(messages, client)

    # 2. 用摘要替换所有消息
    messages = [{"role": "user", "content": f"Previously: {summary}"}]

    # 3. 记录 compact 边界（用于会话恢复）
    session_storage.save_metadata("compact_boundary", {...})
```

### Level 3: Blocking limit

```python
# 如果 token 超过硬限制，直接停止
if current_tokens > blocking_limit:
    yield Terminal(reason="blocking_limit")
    return
```

### 三级防护图

```
token 数量增长
│
├─ 1000 ──→ 正常
├─ 5000 ──→ 正常
├─ 20000 ──→ Microcompact 触发（清除旧工具结果）
├─ 50000 ──→ Auto-compact 触发（AI 总结对话）
├─ 100000 ──→ Blocking limit（硬停止）
```

### 下一个问题

API 调用可能出错。怎么处理？

---

## Stage 8：错误处理和重试

### 问题

API 调用可能失败：
- 429 Rate Limited（限流）
- 529 Overloaded（过载）
- 400 Prompt Too Long（prompt 太长）
- 500 Server Error（服务器错误）
- 网络超时

### 思考

错误分两类：**可重试** 和 **不可重试**。

```python
# codo/services/api/errors.py

def classify_api_error(error):
    """把 API 错误分类"""
    if error.status == 429:
        return APIErrorCategory.RATE_LIMITED      # 可重试
    if error.status == 529:
        return APIErrorCategory.OVERLOADED         # 可重试
    if error.status == 500:
        return APIErrorCategory.SERVER_ERROR       # 可重试
    if "prompt too long" in error.message:
        return APIErrorCategory.PROMPT_TOO_LONG    # 特殊处理
    if error.status in (401, 403):
        return APIErrorCategory.AUTH_ERROR         # 不可重试
    return APIErrorCategory.BAD_REQUEST            # 不可重试
```

### 重试策略

```python
# 指数退避重试
MAX_RETRIES = 3

for attempt in range(MAX_RETRIES):
    try:
        response = await call_api(...)
        break  # 成功
    except RetryableError as e:
        delay = get_retry_delay(e, attempt)  # 500ms, 1s, 2s, ... 最大 15s
        await asyncio.sleep(delay)
else:
    # 重试耗尽
    yield Terminal(reason="api_error_retry_exhausted")
```

### PROMPT_TOO_LONG 的特殊处理

```python
# codo/query.py

except APIError as e:
    if classify_api_error(e) == PROMPT_TOO_LONG:
        if not state.has_attempted_reactive_compact:
            # 第一次遇到：尝试压缩对话
            await force_compact(messages)
            state = QueryState(
                messages=compressed_messages,
                has_attempted_reactive_compact=True,  # 标记已尝试
                ...
            )
            continue  # 重试
        else:
            # 已经压缩过了还是太长，放弃
            yield Terminal(reason="prompt_too_long")
            return
```

### max_tokens 截断恢复

```python
# AI 的回复被截断（stop_reason == "max_tokens"）且没有 tool_use
if stop_reason == "max_tokens" and not tool_use_blocks:
    if recovery_count < 3:
        # 自动发一条 "Continue" 消息，让 AI 继续
        messages.append({
            "role": "user",
            "content": "Continue from where you left off."
        })
        state = QueryState(messages=messages, max_output_tokens_recovery_count=recovery_count + 1, ...)
        continue  # 继续循环
```

### 错误处理总结

| 错误类型 | 处理方式 |
|---------|---------|
| Rate Limited (429) | 指数退避重试，最多 3 次 |
| Overloaded (529) | 指数退避重试，最多 3 次 |
| Server Error (500) | 指数退避重试，最多 3 次 |
| Prompt Too Long | 尝试压缩一次，还是太长就停止 |
| max_tokens 截断 | 自动发 "Continue"，最多 3 次 |
| Auth Error (401/403) | 立即停止 |
| 网络超时 | 指数退避重试 |

### 下一个问题

循环什么时候结束？

---

## Stage 9：停止条件 — 什么时候结束

### 问题

循环不能无限跑。需要明确的停止条件。

### 五种停止条件

```python
# codo/query.py

# 条件 1: AI 没有调用工具（正常完成）
if not tool_use_blocks:
    yield Terminal(reason="completed")
    return

# 条件 2: 达到最大轮次
if next_turn_count > max_turns:
    yield Terminal(reason="max_turns")
    return

# 条件 3: Token 超过硬限制
if current_tokens > blocking_limit:
    yield Terminal(reason="blocking_limit")
    return

# 条件 4: API 错误且无法恢复
yield Terminal(reason="api_error")
return

# 条件 5: Stop hooks 阻止
should_continue = await handle_stop_hooks(cwd, messages)
if not should_continue:
    yield Terminal(reason="stop_hook_prevented")
    return
```

### Stop Hooks 是什么？

用户可以在 `.codo/settings.json` 配置 Stop hooks，在对话"看起来完成"时运行额外检查：

```json
{
    "hooks": {
        "Stop": [
            {"type": "command", "command": "npm test", "timeout": 30000}
        ]
    }
}
```

如果 `npm test` 失败（返回非 0），hook 返回 `False`，对话不会结束，AI 会继续修复问题。

### 停止条件优先级

```
1. Token blocking limit     → 最高优先级，硬性停止
2. API 错误且无法恢复       → 立即停止
3. max_turns               → 达到上限停止
4. Stop hooks              → 可以阻止"完成"
5. 没有 tool_use           → 正常完成
```

### 下一个问题

用户想中途打断怎么办？

---

## Stage 10：中断和中止

### 问题

用户按了 Ctrl+C。此时可能：
- API 正在流式返回
- 工具正在执行
- 循环正在等待

### 思考

用 **AbortController** 模式：

```python
# codo/utils/abort_controller.py

class AbortController:
    def __init__(self):
        self._aborted = False
        self._reason = None
        self._callbacks = []

    def abort(self, reason="interrupt"):
        self._aborted = True
        self._reason = reason
        for cb in self._callbacks:
            cb()

    def is_aborted(self):
        return self._aborted

    def on_abort(self, callback):
        self._callbacks.append(callback)
```

### 两种中断类型

```python
# "interrupt" — Ctrl+C，Bash 不杀子进程
# "abort"    — 程序化中止，Bash 杀子进程

controller.abort(reason="interrupt")  # 温和中断
controller.abort(reason="abort")      # 强制中止
```

### 在循环中的处理

```python
# codo/query.py

try:
    while True:
        # 循环体...
        pass
except asyncio.CancelledError:
    # 被取消（来自 abort_controller）
    reason = execution_context["abort_controller"].get_reason()
    phase_tracker.transition("interrupted", interrupt_reason=reason)
    yield {"type": "interrupt_ack"}
    raise  # 向上传播
```

### 在工具执行中的处理

```python
# streaming_executor.py

async def _execute_tool_with_abort(self, task, ...):
    """工具执行时监听中止信号"""
    done, pending = await asyncio.wait(
        [asyncio.create_task(execute_tool(task)),
         asyncio.create_task(sibling_abort_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    if sibling_abort_event.is_set():
        # 被中止，返回合成错误
        task.result = create_error_result("sibling_error")
```

### 下一个问题

循环和 UI 之间怎么通信？

---

## Stage 11：运行时协议 — QueryRuntimeController

### 问题

循环在 `query_loop()` 里跑，但 UI 需要：
- 实时显示文字（text_delta）
- 显示工具执行状态
- 接收用户输入（权限确认、中断）
- 显示错误信息

循环和 UI 之间怎么通信？

### 思考

用**双向通信**：事件（events）从循环流向 UI，命令（commands）从 UI 流向循环。

```
┌──────────────────┐                    ┌──────────────────┐
│  query_loop()    │                    │  UI / 调用方      │
│                  │                    │                  │
│  yield event ────┼──→ events ───────→ │  显示文字/工具结果  │
│                  │                    │                  │
│  receive cmd ←───┼──← commands ←───── │  用户中断/权限确认  │
│                  │                    │                  │
└──────────────────┘                    └──────────────────┘
         ↕
  QueryRuntimeController
  （事件队列 + 命令队列）
```

### 核心代码

```python
# codo/query_engine.py

async def submit_message_stream(self, prompt):
    # 创建运行时控制器
    runtime_controller = QueryRuntimeController()

    # 启动两个并发任务
    pump_task = asyncio.create_task(_pump_query_events())
    command_task = asyncio.create_task(_pump_runtime_commands())

    # 主消费循环
    while True:
        event = await runtime_controller.next_event()
        if isinstance(event, Terminal):
            yield event
            break
        yield event  # 转发给调用方
```

### 事件类型

```python
# 从循环流向 UI 的事件
{"type": "text_delta", "delta": {"text": "你好"}}
{"type": "tool_result", "tool_use_id": "...", "content": "..."}
{"type": "content_block_start", "index": 0, "content_block": {...}}
{"type": "message_stop"}
{"type": "compact", "result": {...}}
{"type": "error", "error": "..."}
Terminal(reason="completed")  # 循环结束
```

### 命令类型

```python
# 从 UI 流向循环的命令
{"type": "interrupt"}                    # 用户按 Ctrl+C
{"type": "resolve_interaction", ...}     # 用户确认权限
{"type": "cancel_interaction", ...}      # 用户取消权限
{"type": "retry_checkpoint", ...}        # 用户重试
```

### 下一个问题

整个系统的完整架构是什么？

---

## Stage 12：完整架构总结

### 对话循环的完整数据流

```
用户输入: "帮我删掉 TODO"
    │
    ▼
QueryEngine.submit_message_stream()
    │
    ├─ 追加用户消息到 self.messages
    ├─ 构建系统提示词（PromptBuilder）
    ├─ 生成工具 schema（tools_to_api_schemas）
    ├─ 组装 QueryParams（不可变）
    │
    ▼
query_loop(params)
    │
    ▼
while True:
    │
    ├─ Step 1: prepare_turn
    │   ├─ 解构 QueryState
    │   ├─ 收集附件（IDE 选择、计划提醒、TODO 提醒）
    │   └─ yield turn_started
    │
    ├─ Step 2: Token 管理
    │   ├─ microcompact（清除旧工具结果）
    │   ├─ auto-compact（AI 总结对话）
    │   └─ blocking limit 检查
    │
    ├─ Step 3: stream_assistant
    │   ├─ normalize_messages_for_api（过滤、角色交替）
    │   ├─ add_cache_breakpoints（缓存优化）
    │   ├─ client.messages.stream(**api_kwargs)
    │   ├─ 流式处理事件:
    │   │   ├─ content_block_start → 注册工具
    │   │   ├─ content_block_delta → yield text_delta
    │   │   └─ content_block_stop
    │   └─ get_final_message() → stop_reason + 完整 blocks
    │
    ├─ Step 4: collect_tool_calls
    │   ├─ 解析 tool_use blocks（补全 input_json）
    │   ├─ 构建 assistant_message
    │   ├─ 追加到 messages
    │   ├─ 启动工具执行 (_process_queue)
    │   └─ yield message_stop
    │
    ├─ [如果 max_tokens 截断]: 注入 "Continue" → continue
    │
    ├─ Step 5: execute_tools
    │   ├─ 收集已完成的结果
    │   ├─ 等待剩余结果
    │   ├─ 追加 tool_result 消息
    │   └─ yield tool_result 事件
    │
    ├─ Step 6: 判断是否继续
    │   │
    │   ├─ 有 tool_use?
    │   │   ├─ 检查 max_turns
    │   │   ├─ state = QueryState(turn_count + 1)
    │   │   └─ continue → 回到 while 顶部
    │   │
    │   └─ 没有 tool_use?
    │       ├─ 触发 memory 提取（后台）
    │       ├─ 运行 stop hooks
    │       │   ├─ hook 返回 True → 完成
    │       │   └─ hook 返回 False → 继续（AI 继续修复）
    │       └─ yield Terminal(reason="completed") → return
    │
    └─ [错误处理]
        ├─ PROMPT_TOO_LONG → 压缩一次 → continue
        ├─ 可重试错误 → 指数退避重试
        ├─ 不可重试错误 → Terminal(reason="api_error")
        └─ CancelledError → interrupt_ack → raise
```

### 核心文件清单

| 文件 | 职责 |
|------|------|
| `codo/query_engine.py` | QueryEngine 类：高层协调器，管理会话、工具、持久化 |
| `codo/query.py` | query_loop()：核心 while-true 循环 |
| `codo/services/prompt/builder.py` | PromptBuilder：系统提示词构建 |
| `codo/services/prompt/messages.py` | normalize_messages_for_api, add_cache_breakpoints |
| `codo/services/compact/microcompact.py` | 微压缩（清除旧工具结果） |
| `codo/services/compact/compact.py` | 自动压缩（AI 总结对话） |
| `codo/services/api/errors.py` | API 错误分类、重试延迟计算 |
| `codo/services/tools/streaming_executor.py` | 流式工具执行器 |
| `codo/services/tools/stop_hooks.py` | 停止钩子 |
| `codo/utils/abort_controller.py` | 中断/中止控制 |

### 设计决策总结

| 决策 | 选择 | 原因 |
|------|------|------|
| 状态管理 | QueryState dataclass | 打包所有可变状态，方便快照和测试 |
| 参数传递 | QueryParams 不可变 | 避免循环内意外修改外部状态 |
| 消息副本 | `.copy()` 传给 query() | 隔离引擎状态和循环状态 |
| 状态同步 | Terminal 后从 session_storage 重加载 | 保证与持久化一致 |
| Token 管理 | 三级防护 | 渐进式，避免不必要的压缩 |
| 错误重试 | 指数退避 + 分类 | 可重试的重试，不可重试的立即停止 |
| max_tokens 恢复 | 自动 "Continue"，最多 3 次 | 简单有效 |
| 循环/UI 通信 | 双向事件/命令队列 | 解耦循环和 UI |
| 压缩方式 | microcompact → auto-compact | 轻量优先，重量兜底 |
| Stop hooks | 用户可配置 | 灵活（如自动测试） |

### 一句话总结

**对话循环引擎 = 一个 while-true 状态机，每轮做三件事：调 API、执行工具、判断是否继续。外围包裹着 token 管理、错误恢复、中断控制和 UI 通信。**

---

> **三个系统的关系**：
> - **会话系统**：保存对话历史（持久化层）
> - **工具系统**：执行实际操作（能力层）
> - **对话循环引擎**：串联一切（控制层）
>
> 循环引擎调用工具系统执行工具，调用会话系统保存历史。三者配合完成一次完整的 AI 对话。
