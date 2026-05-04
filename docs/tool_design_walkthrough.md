# 工具系统设计演练：从 0 到 1

> 和会话系统一样，本文档用"遇到问题 → 思考 → 写代码 → 下一个问题"的方式，带你从零设计整个工具系统。

---

## Stage 0：为什么需要工具系统？

### 问题

Claude API 只能输出文字。但我们需要 AI 能：
- 读写文件
- 执行 shell 命令
- 搜索代码
- 创建子 Agent

怎么办？

### 思考

Anthropic API 提供了 **tool_use** 机制：

```
1. 你在请求时告诉 API："我有这些工具"（tool definitions）
2. API 返回时可能说："我想调用这个工具"（tool_use block）
3. 你执行工具，把结果发回去（tool_result block）
4. API 继续推理
```

所以工具系统的核心是：
1. **定义工具**：告诉 AI 有什么工具、怎么用
2. **执行工具**：AI 决定调用时，真正去执行
3. **返回结果**：把执行结果格式化后发回给 AI

```
┌─────────────────────────────────────────────────────┐
│                    Claude API                        │
│                                                     │
│  请求: "我有 Bash, Read, Edit, Write..."            │
│  响应: tool_use { name: "Bash", input: {cmd: "ls"} }│
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│              工具执行系统                              │
│                                                     │
│  1. 找到 BashTool                                    │
│  2. 验证输入                                          │
│  3. 检查权限                                          │
│  4. 执行命令                                          │
│  5. 返回结果                                          │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  响应: tool_result { content: "file1.py\nfile2.py" } │
│  → 发回 API，继续推理                                 │
└─────────────────────────────────────────────────────┘
```

### 需要解决的问题

1. 工具怎么定义？（输入输出格式）
2. 工具怎么注册？（AI 怎么知道有哪些工具）
3. 工具怎么执行？（单个工具的执行流程）
4. 多个工具怎么编排？（并发还是串行）
5. 权限怎么控制？（哪些操作需要用户确认）
6. 结果怎么发回？（格式化给 AI 看）

一个一个来。

---

## Stage 1：工具的定义 — Tool 基类

### 问题

每个工具做的事情不同（Bash 执行命令，Read 读文件），但它们有共同的接口。怎么设计？

### 思考

用**抽象基类 + 泛型**。每个工具需要：
- **输入类型**：Pydantic BaseModel（可以自动验证、转 JSON Schema）
- **输出类型**：任意类型
- **核心方法**：`call()` 执行、`description()` 简短描述、`prompt()` 给 AI 看的详细说明

```python
# codo/tools/types.py

InputT = TypeVar('InputT', bound=BaseModel)   # 输入必须是 Pydantic 模型
OutputT = TypeVar('OutputT')                    # 输出任意类型
ProgressT = TypeVar('ProgressT', bound=BaseModel)  # 进度报告

@dataclass
class ToolResult(Generic[OutputT]):
    data: Optional[OutputT] = None    # 成功时的输出
    error: Optional[str] = None       # 失败时的错误
    # ... 其他字段
```

### 核心代码

```python
# codo/tools/base.py

class Tool(ABC, Generic[InputT, OutputT, ProgressT]):
    # --- 属性（必须实现）---
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称，如 'Bash', 'Read'"""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> type[InputT]:
        """输入 schema（Pydantic 模型类）"""
        pass

    # --- 核心方法（必须实现）---
    @abstractmethod
    async def call(self, args, context, can_use_tool, parent_message, on_progress) -> ToolResult:
        """执行工具，返回结果"""
        pass

    @abstractmethod
    async def description(self, input, options) -> str:
        """简短描述（UI 显示用）"""
        pass

    @abstractmethod
    async def prompt(self, options) -> str:
        """详细说明（发给 AI 的 tool description）"""
        pass

    @abstractmethod
    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        """把工具输出转成 API 的 tool_result 格式"""
        pass

    # --- 默认方法（可覆盖）---
    def is_concurrency_safe(self, input) -> bool:
        """是否可以并发执行？默认 False（安全优先）"""
        return False

    def is_read_only(self, input) -> bool:
        """是否只读？默认 False"""
        return False

    def requires_permission(self, input) -> bool:
        """是否需要权限检查？默认 True（安全优先）"""
        return True

    def get_context_modifier(self, input, result, context) -> Optional[Callable]:
        """执行后是否要修改上下文？默认不修改"""
        return None
```

### 设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 输入类型 | Pydantic BaseModel | 自动验证 + 自动转 JSON Schema |
| 默认并发安全 | False | 安全优先，只读工具自己覆盖为 True |
| 默认需要权限 | True | 安全优先，完全安全的工具自己覆盖为 False |

### 下一个问题

工具定义好了，怎么让 AI 知道有哪些工具？

---

## Stage 2：工具注册 — BUILTIN_TOOLS

### 问题

我们有 15 个工具。AI 需要知道"有哪些工具可用"，执行时需要"按名字找到工具实例"。怎么管理？

### 思考

最简单的方式：一个**列表**就是注册表。

```python
# codo/tools/__init__.py

BUILTIN_TOOLS = [
    bash_tool,           # 执行 shell 命令
    read_tool,           # 读文件
    edit_tool,           # 编辑文件（字符串替换）
    write_tool,          # 写文件
    glob_tool,           # 文件名匹配
    grep_tool,           # 内容搜索
    agent_tool,          # 子 Agent
    lsp_tool,            # 语言服务器
    todo_write_tool,     # TODO 列表
    web_fetch_tool,      # 抓取网页
    ask_user_question_tool,  # 向用户提问
    enter_plan_mode_tool,    # 进入计划模式
    exit_plan_mode_tool,     # 退出计划模式
    skill_tool,          # 技能调用
    notebook_edit_tool,  # 编辑 Jupyter notebook
]
```

然后提供两个函数：

```python
# codo/tools_registry.py

def get_all_tools() -> List[Tool]:
    """获取所有工具（返回副本，防止误改全局列表）"""
    return list(BUILTIN_TOOLS)

def find_tool_by_name(tools, name) -> Tool | None:
    """按名字查找工具"""
    for tool in tools:
        if tool.name == name:
            return tool
    return None
```

### 为什么要统一来源？

**问题**：如果 prompt 里说"你有 Bash 工具"，但执行时找不到 BashTool，就会出错。

**解决**：`BUILTIN_TOOLS` 是唯一的 source of truth。prompt 生成和工具执行都从这里取工具列表。

```
BUILTIN_TOOLS ──→ get_all_tools() ──→ tools_to_api_schemas() ──→ 发给 API
                ──→ get_all_tools() ──→ find_tool_by_name()   ──→ 执行时查找
```

### 下一个问题

工具注册好了。AI 返回 `tool_use` 时，我们怎么执行它？

---

## Stage 3：AI 怎么调用工具 — 从 tool_use 到执行

### 问题

AI 返回了一个 `tool_use` block：

```json
{
    "type": "tool_use",
    "id": "toolu_abc123",
    "name": "Bash",
    "input": {"command": "ls -la"}
}
```

我们怎么处理？

### 思考

AI 可能一次返回**多个** tool_use block。比如：

```
tool_use[1]: Read("src/main.py")
tool_use[2]: Grep("TODO", path="src/")
tool_use[3]: Bash("npm test")
```

Read 和 Grep 都是只读的，可以**并发**。Bash 是写操作，必须**串行**。

所以流程是：
1. 收集所有 tool_use
2. **分区**：把可以并发的分一组，不能并发的单独一组
3. 逐组执行

```
输入: [Read, Grep, Bash, Read]
         ↓ 分区
批次1: [Read, Grep]  → 并发执行
批次2: [Bash]        → 串行执行
批次3: [Read]        → 串行执行（和 Bash 分开了）
```

### 核心代码

```python
# codo/services/tools/orchestration.py

def partition_tool_calls(tool_calls, context) -> List[Batch]:
    batches = []

    for tool_call in tool_calls:
        tool = find_tool_by_name(tool_pool, tool_call["name"])
        parsed_input = tool.input_schema(**tool_call["input"])
        is_safe = tool.is_concurrency_safe(parsed_input)

        task = ToolExecutionTask(
            tool_use_id=tool_call["id"],
            tool_name=tool_call["name"],
            tool_input=tool_call["input"],
            is_concurrency_safe=is_safe,
        )

        if is_safe and batches and batches[-1].is_concurrency_safe:
            # 连续的并发安全工具，合并到上一个批次
            batches[-1].add_task(task)
        else:
            # 创建新批次
            batches.append(Batch(is_concurrency_safe=is_safe, tasks=[task]))

    return batches
```

### 分区规则

```
[Read, Read, Bash, Grep, Read]
    ↓
[Read+Read]  → 并发批次（两个都是并发安全）
[Bash]       → 串行批次（非并发安全）
[Grep]       → 串行批次（虽然安全，但前面是非安全的，不能合并）
[Read]       → 串行批次（同上）
```

关键：**只有连续的并发安全工具才合并**。一旦遇到非安全工具，后面的重新开始分组。

### 下一个问题

分区好了，单个工具怎么执行？

---

## Stage 4：单个工具的执行流程 — execute_single_tool

### 问题

一个工具从"收到调用"到"返回结果"，中间要经过哪些步骤？

### 思考

一个工具的执行不是简单的 `result = tool.call()`。中间要：
1. 检查是否被取消
2. 运行前置 Hook
3. 检查权限
4. 执行
5. 截断过大的结果
6. 运行后置 Hook
7. 保存结果
8. 获取上下文修改器

### 核心代码

```python
# codo/services/tools/orchestration.py

async def execute_single_tool(task, context, pre_hooks, post_hooks, post_failure_hooks):
    try:
        # Step 0: 检查是否被取消
        if abort_controller and abort_controller.is_aborted():
            raise Exception("操作已中止")

        # Step 1: 运行 PreToolUse Hooks
        if pre_hooks:
            hook_decision = await run_pre_tool_use_hooks(...)
            if hook_decision.behavior == "deny":
                raise PermissionError("Hook 拒绝执行")
            if hook_decision.updated_input:
                task.tool_input = hook_decision.updated_input  # Hook 可以修改输入

        # Step 2: 查找工具实例
        tool = find_tool_by_name(tool_pool, task.tool_name)
        if not tool:
            raise Exception(f"工具未找到: {task.tool_name}")

        # Step 3: 权限检查
        if tool.requires_permission(task.tool_input):
            decision = await has_permissions_to_use_tool(tool, task.tool_input, context)
            if decision.behavior == "deny":
                raise PermissionError("权限被拒绝")
            if decision.behavior == "ask":
                choice = await prompt_permission(...)  # 弹出交互式提示
                # allow_once / allow_always / deny / abort

        # Step 4: 执行工具
        result = await tool.execute(task.tool_input, tool_context)

        # Step 4.5: 截断过大的结果
        if max_size != float('inf'):
            result = result_storage.maybe_truncate_result(result, max_size)

        # Step 5: 运行 PostToolUse Hooks
        if post_hooks:
            await run_post_tool_use_hooks(...)

        # Step 6: 保存结果
        task.result = result
        task.status = ExecutionStatus.COMPLETED

        # Step 7: 获取上下文修改器
        modifier = tool.get_context_modifier(task.tool_input, result, context)
        if modifier:
            task.context_modifier = ContextModifier(modify_fn=modifier)

    except Exception as e:
        # 失败时运行 PostToolUseFailure Hooks
        if post_failure_hooks:
            await run_post_tool_use_failure_hooks(...)
        task.status = ExecutionStatus.FAILED
        task.error = e
```

### 执行流程图

```
tool_use 到达
    │
    ▼
┌──────────────────┐
│ 检查 AbortController │ ← 用户可能点了取消
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 运行 PreToolUse Hooks │ ← 用户自定义的前置逻辑
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 查找工具实例        │ ← 从 BUILTIN_TOOLS 找
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 权限检查           │ ← 需要用户确认吗？
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 执行工具 call()    │ ← 真正干活
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 截断大结果         │ ← 防止 context 溢出
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 运行 PostToolUse Hooks │ ← 用户自定义的后置逻辑
└──────┬───────────┘
       ▼
┌──────────────────┐
│ 保存结果 + 上下文修改 │
└──────────────────┘
```

### 下一个问题

多个批次怎么串起来执行？

---

## Stage 5：批量执行 — run_tools_batch

### 问题

分区后的多个批次，怎么执行？每个批次内部怎么并发？

### 思考

```
批次执行规则：
1. 批次之间：串行（按顺序）
2. 批次内部：
   - 并发批次 → asyncio.gather 并发执行
   - 串行批次 → 逐个执行
```

### 核心代码

```python
# codo/services/tools/orchestration.py

async def run_tools_batch(tool_calls, context, max_concurrency, pre_hooks, post_hooks, post_failure_hooks):
    # Step 1: 分区
    batches = partition_tool_calls(tool_calls, context)

    # Step 2: 创建执行队列（控制最大并发数）
    queue = ToolExecutionQueue(max_concurrency)

    # Step 3: 逐批次执行
    for batch in batches:
        if batch.is_concurrency_safe:
            await run_batch_concurrently(batch, context, queue, ...)
        else:
            await run_batch_serially(batch, context, queue, ...)

    # Step 4: 聚合上下文修改器
    updated_context = aggregate_context_modifiers(batches, context)

    # Step 5: 返回结果
    return OrchestrationResult(batches=batches, ...)
```

### 并发执行 vs 串行执行

```python
async def run_batch_concurrently(batch, context, queue, ...):
    """批次内所有任务并发执行"""
    queue.add_tasks(batch.tasks)
    await asyncio.gather(
        *[execute_with_control(task) for task in batch.tasks],
        return_exceptions=True
    )

async def run_batch_serially(batch, context, queue, ...):
    """批次内任务逐个执行"""
    for task in batch.tasks:
        queue.add_task(task)
        await execute_single_tool(task, context, ...)
```

### 执行示例

```
AI 返回: [Read("a.py"), Read("b.py"), Bash("npm test"), Grep("TODO")]

分区:
  Batch 0: [Read, Read]  → 并发安全 ✓
  Batch 1: [Bash]        → 非并发安全
  Batch 2: [Grep]        → 并发安全（但和 Bash 不连续）

执行:
  Batch 0: asyncio.gather(Read("a.py"), Read("b.py"))  → 两个同时跑
  Batch 1: await Bash("npm test")                       → 等 Bash 完成
  Batch 2: await Grep("TODO")                           → 最后跑 Grep
```

### 下一个问题

权限检查具体怎么做？

---

## Stage 6：权限系统

### 问题

有些工具是安全的（Read 读文件），有些是危险的（Bash 执行 rm -rf）。怎么控制？

### 思考

**三层权限检查**：

```
Phase 1: 规则检查
  ① 有 deny 规则？→ 拒绝
  ② 有 ask 规则？→ 询问用户
  ③ 工具自己的 check_permissions() 返回什么？
  ④ 文件路径安全检查（.git/, .env 等敏感路径）→ 询问

Phase 2: 模式检查
  ⑤ bypassPermissions 模式？→ 允许
  ⑥ 有 always-allow 规则？→ 允许

Phase 3: 默认
  ⑦ 以上都没匹配 → 询问用户
```

### 核心代码

```python
# codo/services/tools/permission_checker.py

async def has_permissions_to_use_tool(tool, input, context):
    # Phase 1: 规则检查
    # 1. deny 规则
    deny_rule = get_deny_rule_for_tool(tool.name)
    if deny_rule:
        return create_deny_decision(...)

    # 2. ask 规则
    ask_rule = get_ask_rule_for_tool(tool.name)
    if ask_rule:
        return create_ask_decision(...)

    # 3. 工具自己的权限检查
    tool_result = await tool.check_permissions(input, context)
    if tool_result.decision:
        return tool_result.decision

    # 4. 文件路径安全检查
    if hasattr(input, 'file_path'):
        safety = check_path_safety(input.file_path, context.cwd)
        if safety:
            return safety  # .git/, .env → ask

    # Phase 2: 模式检查
    if context.mode == "bypassPermissions":
        return create_allow_decision()

    if tool_always_allowed_rule(tool.name):
        return create_allow_decision()

    # Phase 3: 默认
    return create_ask_decision(...)  # 询问用户
```

### 用户被询问时的选择

```python
# codo/services/tools/permission_prompt.py

class PermissionChoice(Enum):
    ALLOW_ONCE = "allow_once"      # 这次允许
    ALLOW_ALWAYS = "allow_always"  # 以后都允许（添加规则）
    DENY = "deny"                  # 拒绝
    ABORT = "abort"                # 中止整个查询
```

### 各工具的权限策略

| 工具 | requires_permission | is_read_only | is_concurrency_safe |
|------|---------------------|--------------|---------------------|
| Read | True | True | True |
| Glob | True | True | True |
| Grep | True | True | True |
| Edit | True | False | False |
| Write | True | False | False |
| Bash | True | False | False |
| Agent | **False** | True | False |

注意：AgentTool 的 `requires_permission = False`，因为子 Agent 内部的工具会自己检查权限。

### 下一个问题

权限之外，用户还想在工具执行前后做一些自定义逻辑。怎么做？

---

## Stage 7：Hook 系统

### 问题

用户想：
- 在 Bash 执行前检查命令是否安全
- 在文件写入后自动格式化
- 在工具失败时发送通知

这些"在工具执行前后插入自定义逻辑"的需求，用 Hook 实现。

### 思考

三种 Hook 事件：

```
PreToolUse   → 工具执行前（可以拒绝、修改输入）
PostToolUse  → 工具执行后（可以修改输出、添加上下文）
PostToolUseFailure → 工具失败时（可以触发重试）
```

Hook 从 `.codo/settings.json` 加载：

```json
{
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "check-bash-safety.sh", "timeout": 5000}
                ]
            }
        ],
        "PostToolUse": [...],
        "PostToolUseFailure": [...]
    }
}
```

### Hook 执行流程

```
1. 过滤：找到匹配当前工具名的 Hook
2. 并发执行：每个 Hook 作为子进程运行，输入通过 stdin 传 JSON
3. 解析输出：stdout 必须是 JSON，包含 permissionDecision, updatedInput 等
4. 聚合结果：多个 Hook 的决策合并，deny > ask > allow
```

### Hook 结果类型

```python
@dataclass
class HookResult:
    outcome: str                    # "success" | "blocking" | "cancelled"
    permission_behavior: str        # "allow" | "deny" | "ask"
    updated_input: Optional[dict]   # 修改后的工具输入
    additional_context: Optional[str]  # 额外上下文（给 AI 看）
    prevent_continuation: bool      # 是否阻止后续执行
    retry: bool                     # 是否重试
```

### 聚合规则

多个 Hook 返回结果时，优先级：**deny > ask > allow**

```
Hook 1 返回: allow
Hook 2 返回: deny
Hook 3 返回: ask
→ 最终结果: deny（deny 优先）
```

### 下一个问题

工具执行完了，结果怎么发回给 AI？

---

## Stage 8：结果返回给 AI — tool_result 格式

### 问题

工具返回了一个 `ToolResult(data=BashOutput(stdout="hello"))`。怎么转成 API 能理解的格式？

### 思考

Anthropic API 要求 tool_result 的格式是：

```json
{
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_abc123",
            "content": "hello\n",      // 给 AI 看的文本
            "is_error": false
        }
    ]
}
```

所以需要把 `ToolResult` 转成这个格式。

### 两种路径

**路径 1：工具自己的 `map_tool_result_to_tool_result_block_param()`**

每个工具实现这个方法，定义自己的格式化逻辑。

**路径 2：流式执行器的 `_format_tool_result()`**

`StreamingToolExecutor` 有自己的格式化逻辑，按工具类型分别处理：
- Bash → stdout + stderr
- Read → 文件内容
- Edit → diff + 行数变化
- Write → diff

### 结果大小截断

如果工具返回的结果太大（比如读了一个 10MB 的文件），会撑爆 context window。

```python
# 在 execute_single_tool 中
max_size = tool.max_result_size_chars  # 每个工具定义自己的限制
if max_size != float('inf'):
    result = result_storage.maybe_truncate_result(result, max_size)
```

截断后的内容会被持久化到文件，AI 只看到截断后的内容。

### 下一个问题

有些工具执行后需要改变"环境"。比如 `cd /foo` 后，后续工具应该在 `/foo` 目录执行。怎么做？

---

## Stage 9：上下文修改器 — Context Modifier

### 问题

用户执行了 `Bash("cd /tmp")`。之后的 `Read("test.txt")` 应该读 `/tmp/test.txt`，而不是原来的目录。

### 思考

工具可以返回一个 **context modifier** — 一个函数，接收当前上下文，返回修改后的上下文。

```python
# codo/tools/bash_tool/bash_tool.py

class BashTool(Tool[...]):
    def get_context_modifier(self, input_data, result, context):
        """检测 cd 命令，返回修改 cwd 的函数"""
        if result.error or result.data.exitCode != 0:
            return None

        command = input_data.command.strip()
        if command.startswith('cd '):
            target_dir = command[3:].strip()
            if not os.path.isabs(target_dir):
                target_dir = os.path.join(context.get('cwd'), target_dir)

            def modify_context(ctx):
                new_ctx = ctx.copy()
                new_ctx['cwd'] = target_dir
                return new_ctx

            return modify_context  # 返回一个函数

        return None
```

### 修改器怎么生效？

```python
# codo/services/tools/orchestration.py

def aggregate_context_modifiers(batches, initial_context):
    """按批次顺序应用所有修改器"""
    context = initial_context.copy()
    for batch in batches:
        for modifier in batch.get_context_modifiers():
            context = modifier.apply(context)
    return context
```

### 执行顺序保证

```
Batch 0: [Bash("cd /tmp")]
Batch 1: [Read("test.txt")]

执行 Batch 0 → context_modifier 把 cwd 改成 /tmp
执行 Batch 1 → Read 用新的 cwd (/tmp) 拼接路径
```

修改器按批次顺序应用，所以 Batch 1 能看到 Batch 0 的修改。

### 目前唯一使用 context modifier 的工具

只有 **BashTool** 使用了这个机制（检测 `cd` 命令）。其他工具不需要修改上下文。

### 下一个问题

看几个具体的工具实现，理解模式。

---

## Stage 10：具体工具实现 — 三个典型例子

### 10.1 ReadTool — 最简单的工具

```python
@build_tool(
    name="Read",
    max_result_size_chars=30000,
    input_schema=ReadToolInput,
)
class ReadTool(Tool[ReadToolInput, ReadToolOutput, None]):
    # 只读 + 并发安全
    def is_concurrency_safe(self, input): return True
    def is_read_only(self, input): return True

    async def call(self, args, context, ...):
        # 1. 验证路径（绝对路径、存在、不是目录）
        # 2. 检查去重（避免重复读同一个文件）
        # 3. 根据文件类型选择读取方式（普通文件/PDF/图片/notebook）
        # 4. 返回 ToolResult(data=ReadToolOutput(content=...))

    async def prompt(self, options):
        return "Reads a file from the local filesystem. ..."  # ~50 行详细说明
```

**特点**：最安全的工具，只读、并发安全、不需要特殊权限。

### 10.2 EditTool — 有副作用的工具

```python
@build_tool(
    name="Edit",
    max_result_size_chars=10000,
    input_schema=EditToolInput,
)
class EditTool(Tool[EditToolInput, EditToolOutput, None]):
    # 非并发安全 + 非只读
    def is_concurrency_safe(self, input): return False
    def is_read_only(self, input): return False

    async def call(self, args, context, ...):
        # 1. 验证文件之前被读过（防止编辑未知文件）
        # 2. 检查文件修改时间（防止并发编辑冲突）
        # 3. 执行字符串替换
        # 4. 生成 unified diff
        # 5. 写入文件
        # 6. 返回 ToolResult(data=EditToolOutput(diff=..., linesChanged=...))
```

**特点**：有副作用（写文件），所以必须串行执行。

### 10.3 AgentTool — 最复杂的工具

```python
@build_tool(
    name="Agent",
    max_result_size_chars=50000,
    input_schema=AgentToolInput,
    is_concurrency_safe=lambda input: False,  # 非并发安全
)
class AgentTool(Tool[AgentToolInput, AgentToolOutput, None]):
    def requires_permission(self, input): return False  # 子 Agent 自己检查权限
    def is_read_only(self, input): return True  # Agent 本身不修改文件

    async def call(self, args, context, ...):
        # 1. 确定模式（fresh vs fork）
        # 2. 获取 API client 和工具列表
        # 3. 运行子 Agent 对话循环
        # 4. 返回子 Agent 的最终结果
```

**特点**：`requires_permission = False`，因为子 Agent 内部的工具会自己检查权限。如果 AgentTool 也要权限，用户会被双重询问。

### 工具模式总结

```
简单工具（Read, Glob, Grep）:
  - 只读 → is_read_only = True
  - 并发安全 → is_concurrency_safe = True
  - 需要权限 → requires_permission = True（走通用权限检查）

写入工具（Edit, Write, Bash）:
  - 有副作用 → is_read_only = False
  - 非并发安全 → is_concurrency_safe = False
  - 需要权限 → requires_permission = True

特殊工具（Agent）:
  - 本身只读 → is_read_only = True
  - 非并发安全 → is_concurrency_safe = False
  - 不需要权限 → requires_permission = False（子工具自己检查）
```

### 下一个问题

我们有两种执行工具的方式：批处理和流式。它们有什么区别？

---

## Stage 11：批处理 vs 流式执行

### 问题

工具执行有两种模式：
1. **批处理** (`run_tools_batch`)：简单的批量执行
2. **流式** (`StreamingToolExecutor`)：和 API 流式响应配合的执行器

它们有什么区别？

### 关键事实：都要等完整 input 才能执行

不管是哪种模式，都**必须等所有 tool_use 块的 input JSON 完整后才能执行**。

```
API 流式响应:
  content_block_start { name: "Read", id: "toolu_1" }  ← 此时只知道名字，input 还是 {}
  content_block_start { name: "Grep", id: "toolu_2" }  ← 同上
  ... input delta 逐步拼装 ...
  final_message { 完整 tool_use blocks }                 ← 此时才有完整 input
    │
    ▼  必须到这里才能开始执行
```

你不能在第一个 tool_use 块还没收完时就执行它——input JSON 还不完整。

### 那"流式"是什么意思？

流式执行器的"流式"指的是**注册先行 + 结果流式返回**：

```
流式模式 (StreamingToolExecutor):
  ① streaming 阶段：注册工具（知道有哪些工具要执行，但不执行）
  ② final_message：拿到完整 input，统一启动执行
  ③ 结果流式返回：工具完成一个就更新 UI，不用等全部完成
```

### 两者的区别

| 特性 | 批处理 | 流式 |
|------|--------|------|
| 工具来源 | 自己从 context 解析 | 由调用方注册 |
| 执行时机 | 等完整 input 后执行 | 等完整 input 后执行（一样） |
| Hook 支持 | 完整（Pre/Post/Failure） | 只有权限检查 |
| Context Modifier | 完整支持 | 未实现 |
| 结果返回 | 一起返回 | 逐个返回（边完成边更新 UI） |
| 代码位置 | orchestration.py | streaming_executor.py |

### 流式执行器的实际用法

```python
# 1. 创建执行器
executor = StreamingToolExecutor(tools=tool_pool)

# 2. streaming 阶段：注册工具（不执行）
for event in api_stream:
    if event.type == "content_block_start":
        executor.register_tool(block, assistant_message)  # 只注册

# 3. final_message 拿到完整 input 后：启动执行
await executor._process_queue()  # 此时才真正执行

# 4. 监听结果（逐个返回）
while not executor.all_done():
    result = await executor.get_next_result()
    update_ui(result)  # 边完成边更新
```

### 下一个问题

整个系统的架构是什么样的？

---

## Stage 12：完整架构总结

### 工具系统的完整数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                        请求阶段                                    │
│                                                                 │
│  BUILTIN_TOOLS                                                  │
│      │                                                          │
│      ▼                                                          │
│  tools_to_api_schemas()                                         │
│      │  调用 tool.prompt() 获取 AI 面向的描述                      │
│      │  调用 Pydantic → JSON Schema 转换                         │
│      ▼                                                          │
│  API 请求: tools=[{name, description, input_schema}, ...]       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        响应阶段                                    │
│                                                                 │
│  API 返回: tool_use blocks [{id, name, input}, ...]             │
│      │                                                          │
│      ▼                                                          │
│  partition_tool_calls()                                         │
│      │  按并发安全性分组                                           │
│      ▼                                                          │
│  ┌─────────────────────────────────────────────┐                │
│  │  Batch 0 (并发): [Read, Grep]               │                │
│  │      │                                       │                │
│  │      ▼ asyncio.gather()                     │                │
│  │  ┌─────────┐  ┌─────────┐                   │                │
│  │  │ Read    │  │ Grep    │                   │                │
│  │  │ call()  │  │ call()  │                   │                │
│  │  └────┬────┘  └────┬────┘                   │                │
│  │       └──────┬─────┘                         │                │
│  └──────────────┼──────────────────────────────┘                │
│                 ▼                                                │
│  ┌─────────────────────────────────────────────┐                │
│  │  Batch 1 (串行): [Bash]                     │                │
│  │      │                                       │                │
│  │      ▼                                       │                │
│  │  ┌─────────┐                                 │                │
│  │  │ Bash    │                                 │                │
│  │  │ call()  │                                 │                │
│  │  └────┬────┘                                 │                │
│  │       │  get_context_modifier() → 修改 cwd    │                │
│  └───────┼─────────────────────────────────────┘                │
│          ▼                                                       │
│  aggregate_context_modifiers()                                   │
│      │  按批次顺序应用上下文修改                                     │
│      ▼                                                           │
│  format_tool_result()                                            │
│      │  转成 API 的 tool_result 格式                               │
│      ▼                                                           │
│  追加到 messages，发回 API                                         │
└─────────────────────────────────────────────────────────────────┘
```

### 核心文件清单

| 文件 | 职责 |
|------|------|
| `codo/tools/types.py` | 类型定义（ToolResult, ValidationResult） |
| `codo/tools/base.py` | Tool 抽象基类、ToolUseContext、build_tool 装饰器 |
| `codo/tools/__init__.py` | BUILTIN_TOOLS 列表（唯一 source of truth） |
| `codo/tools_registry.py` | get_all_tools(), find_tool_by_name() |
| `codo/services/tools/orchestration.py` | 分区、批量执行、上下文聚合 |
| `codo/services/tools/permission_checker.py` | 三层权限检查 |
| `codo/services/tools/permission_prompt.py` | 交互式权限提示 |
| `codo/services/tools/hooks.py` | Hook 执行引擎 |
| `codo/services/tools/hooks_loader.py` | 从 settings.json 加载 Hook 配置 |
| `codo/services/tools/streaming_executor.py` | 流式执行器 |
| `codo/services/prompt/tools.py` | tool_to_api_schema() — 工具转 API 格式 |
| `codo/tools/bash_tool/bash_tool.py` | BashTool 实现 |
| `codo/tools/read_tool/read_tool.py` | ReadTool 实现 |
| `codo/tools/edit_tool/edit_tool.py` | EditTool 实现 |

### 设计决策总结

| 决策 | 选择 | 原因 |
|------|------|------|
| 工具基类 | 抽象类 + 泛型 | 类型安全 + 统一接口 |
| 输入类型 | Pydantic BaseModel | 自动验证 + 自动转 JSON Schema |
| 注册方式 | 列表（BUILTIN_TOOLS） | 简单，唯一 source of truth |
| 默认并发安全 | False | 安全优先（fail-closed） |
| 默认需要权限 | True | 安全优先（fail-closed） |
| 分区策略 | 连续并发安全工具合并 | 最大化并发，但保证安全 |
| 权限检查 | 三层（规则→模式→默认） | 灵活 + 安全 |
| Hook 系统 | 子进程执行 JSON stdin/stdout | 隔离 + 可扩展 |
| 上下文修改 | 函数式（返回新 dict） | 不可变，避免副作用传播 |
| 结果截断 | 按工具定义的最大值 | 防止 context window 溢出 |

### 如果你要新增一个工具

1. 创建 `codo/tools/my_tool/` 目录
2. 定义 `MyToolInput(BaseModel)` 和 `MyToolOutput`
3. 继承 `Tool[MyToolInput, MyToolOutput, None]`
4. 实现必须的方法：`name`, `input_schema`, `call()`, `description()`, `prompt()`, `map_tool_result_to_tool_result_block_param()`
5. 覆盖默认方法：`is_concurrency_safe()`, `is_read_only()`, `requires_permission()`
6. 在 `codo/tools/__init__.py` 的 `BUILTIN_TOOLS` 列表中添加

```python
@build_tool(
    name="MyTool",
    max_result_size_chars=10000,
    input_schema=MyToolInput,
)
class MyTool(Tool[MyToolInput, MyToolOutput, None]):
    def is_concurrency_safe(self, input): return True
    def is_read_only(self, input): return True

    async def call(self, args, context, can_use_tool, parent_message, on_progress):
        # 你的逻辑
        return ToolResult(data=MyToolOutput(result="..."))

    async def description(self, input, options):
        return "简短描述"

    async def prompt(self, options):
        return "给 AI 看的详细说明..."

    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content.result}
```

---

> **和会话系统的对比**：会话系统解决的是"怎么保存和恢复对话状态"，工具系统解决的是"怎么让 AI 执行实际操作"。两者是互补的 — 会话系统保存工具的调用记录，工具系统产生的结果又被保存到会话中。
