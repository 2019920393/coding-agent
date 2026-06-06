# 流式执行器、中断处理器与运行时通信设计演练

> 本文档还原从零设计这三个子系统的思考过程。
> 每个阶段都有：问题 → 思考 → 代码 → 下一个问题。

---

## Part 1：StreamingToolExecutor — 流式工具执行器

### 问题 1：AI 一次调了 3 个工具，怎么办？

AI 返回了这个：

```json
[
  {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
  {"type": "tool_use", "name": "Read", "input": {"file_path": "b.py"}},
  {"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}}
]
```

最简单的做法：**一个一个执行**。

```python
for block in tool_use_blocks:
    result = await execute_tool(block)
    results.append(result)
```

这样能跑，但太慢了。两个 Read 完全可以**同时**执行，它们互不影响。

### 思考

什么时候可以同时执行？什么时候不行？

```
Read("a.py") + Read("b.py")  → 可以同时（都只读，不冲突）
Read("a.py") + Bash("npm")  → 不行（Bash 可能改文件，Read 可能读到脏数据）
Edit("a.py") + Edit("a.py") → 不行（同一个文件，并发编辑会冲突）
```

规律：**只读工具可以并发，写入工具必须串行**。

每个工具知道自己是不是"并发安全"的：

```python
# ReadTool
def is_concurrency_safe(self, input): return True   # 只读

# BashTool
def is_concurrency_safe(self, input): return False   # 可能写任何东西

# EditTool
def is_concurrency_safe(self, input): return False   # 写文件
```

### 代码：第一个版本

```python
async def execute_tools_sequentially(tool_calls, tools):
    results = []
    for call in tool_calls:
        tool = find_tool(tools, call.name)
        result = await tool.call(call.input, ...)
        results.append(result)
    return results
```

**问题**：完全没有并发，所有工具串行执行。

### 下一个问题

怎么实现并发？并发的同时怎么保证安全？

---

### 问题 2：怎么并发执行，同时保证安全？

### 思考

用 `asyncio.gather` 可以并发执行多个协程：

```python
results = await asyncio.gather(
    execute_tool(read_a),
    execute_tool(read_b),
)
```

但不能把所有工具都 gather 起来，因为 Bash 和 Edit 不是并发安全的。

**策略**：把工具分成**批次**（batch），同一批次内可以并发，批次之间串行。

```
[Read, Read, Bash, Grep]
    ↓ 分批
Batch 0: [Read, Read]   ← 并发
Batch 1: [Bash]          ← 串行
Batch 2: [Grep]          ← 串行
```

分区规则：**连续的并发安全工具合并为一个批次**。遇到非安全工具就断开。

```python
def partition_tool_calls(tool_calls):
    batches = []
    for call in tool_calls:
        tool = find_tool(call.name)
        is_safe = tool.is_concurrency_safe(call.input)

        if is_safe and batches and batches[-1].is_safe:
            # 和上一个批次合并
            batches[-1].add(call)
        else:
            # 新建批次
            batches.append(Batch(is_safe=is_safe, tasks=[call]))
    return batches
```

执行：

```python
for batch in batches:
    if batch.is_safe:
        await asyncio.gather(*[execute(t) for t in batch.tasks])
    else:
        for task in batch.tasks:
            await execute(task)
```

### 下一个问题

到目前为止，我们假设工具是独立的。但实际上，一个工具出错可能影响其他工具。怎么处理？

---

### 问题 3：一个工具出错了，其他正在跑的工具怎么办？

### 场景

AI 同时调了 Read("a.py") 和 Bash("mkdir foo && cd foo && npm init")。

```
Read("a.py")              → 正在执行...
Bash("mkdir foo && ...")  → 失败！(目录不存在)
```

Bash 失败了，但 Read 还在跑。Read 可能读到了不相关的文件。AI 看到 Bash 失败后，可能会说"算了不做了"，那 Read 的结果就浪费了。

更糟的是：Bash 命令通常有**隐式依赖链**。`mkdir` 失败 → `cd` 也失败 → `npm init` 也失败。一个命令失败意味着整个链都没意义了。

### 思考

当一个工具出错时，应该**取消正在并行执行的其他工具**。但不能取消所有工具——如果一个 Edit 工具正在写文件，中途取消会导致文件损坏。

**规则**：只取消**并发安全**的工具。非并发安全的工具（正在独占执行的）让它跑完。

### 代码：Sibling Abort

```python
class StreamingToolExecutor:
    def __init__(self):
        self.sibling_abort_event = asyncio.Event()  # 全局取消信号
        self.has_errored = False
```

当 Bash 出错时，设置信号：

```python
# 在 _execute_tool() 中
if result.error and tool.name == "Bash":
    self.has_errored = True
    self.sibling_abort_event.set()  # 通知所有并行工具
```

每个正在执行的工具都在**竞赛**：是自己先完成，还是 abort 信号先到？

```python
async def _execute_tool_with_abort(self, tool):
    execute_task = asyncio.create_task(self._execute_tool(tool))
    abort_task = asyncio.create_task(self.sibling_abort_event.wait())

    done, pending = await asyncio.wait(
        [execute_task, abort_task],
        return_when=asyncio.FIRST_COMPLETED  # 谁先完成用谁的结果
    )

    # 取消输掉的一方
    for task in pending:
        task.cancel()

    # 如果 abort 胜出
    if abort_task in done:
        tool.results = [synthetic_error("Cancelled: parallel tool errored")]
```

**关键细节**：`_should_abort` 只对并发安全的工具生效：

```python
def _should_abort(self, tool):
    return self.has_errored and tool.is_concurrency_safe
# 非并发安全的工具 → 不取消，让它跑完
```

### 下一个问题

到目前为止，工具是"全部注册完再执行"。但 API 的响应是流式的——tool_use 块一个一个到达。能不能边接收边执行？

---

### 问题 4：API 流式响应时，工具怎么执行？

### 场景

API 流式返回 3 个 tool_use 块：

```
content_block_start { name: "Read", id: "toolu_1" }   ← 到达
content_block_start { name: "Read", id: "toolu_2" }   ← 到达
content_block_start { name: "Bash", id: "toolu_3" }   ← 到达
... input delta 逐步拼装 ...
final_message { 完整 input }                            ← 全部完成
```

### 思考

能不能第一个 tool_use 块到就开始执行？

**不行**。因为 `content_block_start` 时只有名字，没有 `input`。input 是通过 `input_json_delta` 逐步拼装的，必须等 `final_message` 才有完整的 JSON。

所以流程是：
1. **注册阶段**（streaming 时）：知道有哪些工具，但不执行
2. **执行阶段**（final_message 后）：拿到完整 input，开始执行

```python
# streaming 阶段
if event.type == "content_block_start" and event.content_block.type == "tool_use":
    streaming_tool_executor.register_tool(block)  # 只注册

# final_message 后
await streaming_tool_executor._process_queue()    # 开始执行
```

### 下一个问题

工具执行完了，结果怎么返回给调用方？先完成的先返回，还是按顺序返回？

---

### 问题 5：结果怎么收集？

### 思考

两种需求：
- **UI 想实时看到**：哪个工具先完成就先显示（减少等待感）
- **API 需要按顺序**：tool_result 的顺序要和 tool_use 的顺序对应

所以需要两个接口：

```python
def get_completed_results(self):
    """非阻塞，返回所有已完成的结果"""
    results = []
    for tool in self.tools:
        if tool.status == COMPLETED:
            tool.status = YIELDED
            results.append(tool.result)
    return results

async def get_remaining_results(self):
    """阻塞，等待所有未完成的工具"""
    while self._has_unfinished_tools():
        await asyncio.wait(executing_tasks, return_when=FIRST_COMPLETED)
        for result in self.get_completed_results():
            yield result
```

调用方的使用方式：

```python
# 先拿已经完成的（不等待）
completed = executor.get_completed_results()

# 再等剩下的（阻塞）
async for result in executor.get_remaining_results():
    process(result)
```

### Part 1 小结

我们从"AI 一次调了 3 个工具怎么办"出发，逐步解决了：
1. 并发执行 → 分批次
2. 错误传播 → sibling abort
3. 流式注册 → 两阶段（注册 → 执行）
4. 结果收集 → 非阻塞 + 阻塞两个接口

---

## Part 2：AbortController — 中断处理器

### 问题 1：用户按了 Ctrl+C，怎么办？

### 场景

用户问 AI "帮我重构这个文件"。AI 正在执行 5 个工具，用户等不及了，按了 Ctrl+C。

此时的状态：
- API 可能还在流式返回
- 3 个工具正在并发执行
- 循环正在 `await` 等工具完成

### 思考

需要一个机制，让**所有正在运行的东西都知道"该停了"**。

最简单的做法：一个全局的标志位。

```python
class AbortController:
    aborted: bool = False

    def abort(self):
        self.aborted = True
```

每个工具执行前检查：

```python
async def execute_tool(tool):
    if abort_controller.aborted:
        return error("已中止")
    result = await tool.call(...)
    return result
```

但这有个问题：工具可能正在执行中（比如 Bash 正在跑 `npm test`），检查标志位没用——它不会在执行中途去检查。

### 代码：回调机制

```python
class AbortController:
    def __init__(self):
        self.aborted = False
        self._callbacks = set()

    def abort(self, reason="abort"):
        self.aborted = True
        # 通知所有注册的回调
        for callback in self._callbacks:
            callback(reason)

    def on_abort(self, callback):
        """注册回调，返回取消注册函数"""
        if self.aborted:
            callback(self.reason)  # 已经中止了，立即触发
            return lambda: None
        self._callbacks.add(callback)
        def unregister():
            self._callbacks.discard(callback)
        return unregister
```

Bash 工具注册回调：

```python
# BashTool
unregister = abort_controller.on_abort(on_abort_callback)
try:
    process = await asyncio.create_subprocess_shell(command)
    await process.wait()
finally:
    unregister()  # 进程结束后取消注册
```

### 下一个问题

Ctrl+C 时，Bash 子进程要不要杀？

---

### 问题 2："打断"和"杀死"是两回事

### 思考

用户按 Ctrl+C 可能有两种意图：
1. **打断 AI 的思考**：AI 在分析代码，用户觉得方向不对，想打断重来。但 Bash 里跑的 `git status` 不需要杀。
2. **杀死一切**：程序内部判断出了严重问题，需要彻底停止。Bash 子进程也要杀。

所以需要两种中断类型：

```python
# "interrupt" — 温和打断
#   用户按 Ctrl+C
#   Bash 子进程继续运行
#   AI 停止思考，但不破坏正在做的事

# "abort" — 强制杀死
#   程序内部决定放弃
#   Bash 子进程被 kill
#   一切归零
```

Bash 工具根据类型决定行为：

```python
def on_abort(reason):
    if reason == "abort":
        process.kill()      # 杀进程
    # "interrupt" → 进程继续运行
```

### 下一个问题

如果有子 Agent，子 Agent 的工具也要被中止。怎么传播？

---

### 问题 3：中断怎么传播到子 Agent？

### 场景

```
主 Agent
  └─ AgentTool（子 Agent）
       ├─ BashTool
       └─ ReadTool
```

用户按 Ctrl+C，主 Agent 中止了，但子 Agent 的 BashTool 还在跑。

### 思考

需要**父子关系**。父中止时，递归中止所有子。

```python
class AbortController:
    def create_child(self):
        child = AbortController()
        child._parent = weakref.ref(self)    # weakref 防内存泄漏
        self._children.append(weakref.ref(child))

        if self.aborted:
            child.abort(self.reason)         # 父已中止，子立即继承
        return child

    def abort(self, reason):
        self.aborted = True
        self.reason = reason
        # 触发回调
        for cb in self._callbacks:
            cb(reason)
        # 递归中止子节点
        for child_ref in self._children:
            child = child_ref()
            if child:
                child.abort(reason)
```

传播链：

```
Root.abort("interrupt")
  → Child 1.abort("interrupt")
    → Child 1.1.abort("interrupt")
    → Child 1.2.abort("interrupt")
  → Child 2.abort("interrupt")
```

**为什么用 weakref？** 如果用普通引用，父子互相持有对方，Python GC 无法回收，内存泄漏。

### 下一个问题

中断触发后，query_loop 怎么知道该停？

---

### 问题 4：query_loop 怎么响应中断？

### 思考

query_loop 是一个 `async for event in stream` 循环。它不会主动检查 abort 标志。需要从**外部**打断它。

Python 的 asyncio 提供了 `task.cancel()`，会向任务注入一个 `CancelledError`。

```python
# _pump_runtime_commands() 中
if command.type == "interrupt":
    abort_controller.abort("interrupt")
    pump_task.cancel()  # 注入 CancelledError
```

query_loop 捕获这个错误：

```python
try:
    while True:
        # 正常循环...
except asyncio.CancelledError:
    reason = abort_controller.get_reason()
    phase_tracker.transition("interrupted")
    emit interrupt_ack  # 告诉 UI "我收到中断了"
    raise               # 继续向上传播
```

### 完整的 Ctrl+C 流程

```
用户按 Ctrl+C
    │
    ▼
UI 调用 engine.interrupt()
    │
    ▼
发送 RuntimeCommand(type="interrupt") → 命令队列
    │
    ▼
_pump_runtime_commands() 取出命令
    │
    ├─→ abort_controller.abort("interrupt")
    │       ├─→ 触发所有回调（Bash 进程继续运行）
    │       └─→ 递归中止子控制器
    │
    └─→ pump_task.cancel()  ← 注入 CancelledError
            │
            ▼
       query_loop 捕获 CancelledError
            ├─→ 转换阶段为 "interrupted"
            ├─→ 发送 interrupt_ack 事件
            └─→ re-raise → 传播到 _pump_query_events
                    │
                    ▼
               emit error 事件 → UI 显示 "已中断"
```

### Part 2 小结

我们从"用户按 Ctrl+C 怎么办"出发，逐步解决了：
1. 通知机制 → 回调 + 标志位
2. 两种中断 → interrupt（温和）vs abort（强制）
3. 子 Agent 传播 → 父子链 + weakref
4. 循环打断 → task.cancel() + CancelledError

---

## Part 3：Runtime Protocol — 引擎与 UI 的通信

### 问题 1：query_loop 和 UI 怎么通信？

### 场景

query_loop 在一个异步任务里跑，UI 在主线程。它们需要交换信息：

```
query_loop → UI: "AI 说了什么"、"工具执行到哪了"、"出错了"
UI → query_loop: "用户按了 Ctrl+C"、"用户允许了权限"
```

### 思考

最简单的方式：**共享变量**。但多线程/多任务访问共享变量需要锁，容易死锁。

更好的方式：**队列**。一个方向一个队列，无锁。

```python
class QueryRuntimeController:
    def __init__(self):
        self._events = asyncio.Queue()     # 引擎 → UI
        self._commands = asyncio.Queue()   # UI → 引擎
```

引擎往 `_events` 里放事件，UI 从 `_events` 里取。反过来也一样。

### 代码

```python
# 引擎侧
await runtime_controller.emit({"type": "text_delta", "text": "你好"})

# UI 侧
event = await runtime_controller.next_event()
if event["type"] == "text_delta":
    display(event["text"])
```

### 下一个问题

query_loop 是一个 async generator（`yield event`），但 Queue 是 `put/get`。怎么连起来？

---

### 问题 2：async generator 和 Queue 怎么桥接？

### 思考

query_loop 用 `yield` 产出事件。但 UI 需要从 Queue 里取事件。需要一个**泵**（pump）把 yield 的事件搬到 Queue 里。

```python
async def _pump_query_events():
    """把 query() 的 yield 转发到 runtime_controller"""
    try:
        async for event in query(query_params):
            await runtime_controller.emit(event)  # yield → Queue
    except asyncio.CancelledError:
        await runtime_controller.emit(error_event)
        raise
    finally:
        await runtime_controller.finish()  # 放入结束信号
```

主循环从 Queue 里取：

```python
while True:
    event = await runtime_controller.next_event()
    if event is SENTINEL:
        break
    yield event  # 转发给 UI
```

两个 pump 任务并发运行：

```
pump_task = asyncio.create_task(_pump_query_events())
command_task = asyncio.create_task(_pump_runtime_commands())
```

### 下一个问题

工具需要权限确认时，循环必须暂停等用户。怎么实现"暂停-恢复"？

---

### 问题 3：工具需要权限确认，循环怎么暂停？

### 场景

BashTool 要执行 `rm -rf node_modules`。权限系统说"需要用户确认"。此时：
- query_loop 必须暂停
- UI 显示"允许/拒绝"对话框
- 用户点"允许"后，循环继续

### 思考

用 **Future**。引擎创建一个 Future，然后 `await` 它。UI 拿到事件后显示对话框，用户点按钮后设置 Future 的值。`await` 返回，循环继续。

```python
class QueryRuntimeController:
    def __init__(self):
        self._pending_interactions = {}  # request_id → Future

    async def request_interaction(self, request):
        """引擎调用：暂停，等待用户"""
        future = asyncio.get_event_loop().create_future()
        self._pending_interactions[request.request_id] = future

        # 通知 UI "需要用户输入"
        await self.emit(RuntimeEvent(
            type="interaction_requested",
            payload={"request": request},
        ))

        # 暂停在这里
        result = await future
        return result

    def resolve_interaction(self, request_id, data):
        """UI 调用：用户已响应"""
        future = self._pending_interactions.pop(request_id, None)
        if future:
            future.set_result(data)  # 解除 await
```

完整的暂停-恢复流程：

```
工具需要权限
    │
    ▼
request_interaction(request)
    │  创建 Future
    │  emit interaction_requested
    │  await future  ← 暂停
    │
    ▼  (事件流到 UI)
    │
UI 显示对话框
    │  用户点 "Allow"
    ▼
resolve_interaction(request_id, "allow")
    │  future.set_result("allow")  ← 恢复
    │
    ▼
工具继续执行
```

### 下一个问题

用户想"重试上一轮"。怎么回到之前的状态？

---

### 问题 4：用户想重试，怎么回到之前的状态？

### 场景

AI 写了一段代码，用户觉得不对，想重来。

### 思考

如果我们在每个阶段变化时保存一份**快照**（checkpoint），重试就是"回到快照"。

```python
@dataclass
class RuntimeCheckpoint:
    checkpoint_id: str
    phase: str
    turn_count: int
    metadata: dict  # 包含 messages_state（完整消息副本）
```

每次阶段变化时自动创建：

```python
# 在 QueryPhaseTracker.transition() 中
checkpoint = RuntimeCheckpoint(
    checkpoint_id=str(uuid4()),
    phase=new_phase,
    turn_count=turn_count,
    metadata={"messages_state": [dict(m) for m in messages]},
)
runtime_controller.checkpoint(checkpoint)
```

重试时：

```python
def retry_checkpoint(self, checkpoint_id):
    checkpoint = self._checkpoints[checkpoint_id]
    self.messages = checkpoint.metadata["messages_state"]  # 恢复消息
    self.turn_count = checkpoint.turn_count                  # 恢复轮次
    # 然后重新进入 query_loop
```

### 下一个问题

UI 怎么知道当前是什么状态（正在生成、正在执行工具、等待权限）？

---

### 问题 5：UI 怎么知道引擎在做什么？

### 思考

引擎在每个阶段变化时发送 `status_changed` 事件：

```python
await phase_tracker.transition(
    "collect_tool_results",
    turn_count=3,
    active_tool_ids=["toolu_1", "toolu_2"],
    checkpoint_id="cp-123",
)
```

UI 收到后更新状态栏：

```python
if event["type"] == "status_changed":
    if event["phase"] == "stream_assistant":
        show_spinner("AI is thinking...")
    elif event["phase"] == "collect_tool_results":
        show_progress(event["active_tool_ids"])
    elif event["phase"] == "complete":
        hide_spinner()
```

工具执行也有独立的事件：

```
tool_started   → UI 显示 "Executing Bash..."
tool_progress  → UI 显示进度
tool_completed → UI 显示结果
```

### Part 3 小结

我们从"query_loop 和 UI 怎么通信"出发，逐步解决了：
1. 通信方式 → 双向 asyncio.Queue
2. 桥接方式 → pump 任务转发
3. 暂停恢复 → Future + await
4. 状态回退 → Checkpoint + 快照恢复
5. 状态显示 → status_changed 事件

---

## 三个子系统的关系

```
用户输入 "帮我重构"
    │
    ▼
query_loop() 开始
    │
    ├─→ 调用 API（流式）
    │       │
    │       ▼
    │   StreamingToolExecutor
    │       ├─ 注册工具（streaming 阶段）
    │       ├─ 分批并发执行
    │       ├─ sibling_abort 处理错误
    │       └─ 收集结果
    │
    ├─→ QueryRuntimeController
    │       ├─ 事件 → UI（文字、工具状态、权限请求）
    │       └─ 命令 ← UI（中断、权限确认、重试）
    │
    └─→ AbortController
            ├─ Ctrl+C → 中断循环
            ├─ 子 Agent 传播
            └─ interrupt vs abort
```

**一句话**：StreamingToolExecutor 管"工具怎么执行"，AbortController 管"怎么停下来"，Runtime Protocol 管"引擎和 UI 怎么对话"。

---

## 核心文件清单

| 文件 | 职责 |
|------|------|
| `codo/services/tools/streaming_executor.py` | 流式工具执行器 |
| `codo/utils/abort_controller.py` | 中断控制器 |
| `codo/runtime_protocol.py` | RuntimeEvent/Command/Checkpoint/Controller |
| `codo/query_engine.py` | 创建 controller、pump 任务 |
| `codo/query.py` | query_loop、QueryPhaseTracker |
| `codo/desktop/event_handler.py` | Desktop 侧事件推送 |
| `codo/types/runtime.py` | InteractionRequest 等运行时交互类型 |
