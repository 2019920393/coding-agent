# 执行链路阅读路线

## 目标

这份文档只服务于一个目标：

看懂项目的**运行时执行链路**，不展开 CLI / TUI 的实现细节。

这里的“执行链路”特指：

1. `QueryEngine` 如何建立一次对话所需的运行环境；
2. `query_loop` 如何驱动一轮或多轮模型调用；
3. 模型产出的 `tool_use` 如何进入工具执行层；
4. 工具如何完成校验、权限检查、调用、结果回填；
5. compact / memory / session 等分支如何接到主链上。

---

## 先建立总图

先把主链记成下面这条，不要一开始陷进细节：

`QueryEngine`
-> 准备 `execution_context / tools / session / permission / model`
-> 进入 `query() / query_loop()`
-> 组装 system prompt / messages / attachments / tools schema
-> 调模型流式接口
-> 遇到 `tool_use`
-> 交给 `StreamingToolExecutor`
-> `validate_input -> permission -> tool.call`
-> 产出 `tool_result / receipt / runtime event`
-> 回填到消息列表
-> 决定是否继续下一轮
-> 必要时走 compact / memory / persistence 分支

阅读时始终围绕这条主线，不要先看 UI 代码。

---

## 阅读原则

### 原则 1：先主干，后分支

先只追 happy path：

1. 用户输入进入系统；
2. 模型返回文本或工具调用；
3. 工具执行；
4. 结果回填；
5. 下一轮继续或结束。

在主干没看懂之前，不要先看：

- `cli/tui/*`
- `services/tools/error_handler.py`
- `services/tools/concurrency.py`
- `services/tools/change_review.py`

这些文件重要，但不适合当第一站。

### 原则 2：按“问题”读，不按文件从头读到尾

每读一个文件，都回答这几个问题：

1. 这个文件在主链的哪一段？
2. 它接收什么输入？
3. 它产出什么输出？
4. 它修改了哪些共享状态？
5. 它把控制权交给谁？

### 原则 3：每一轮只解决一个理解目标

不要一次想看懂整个项目。

建议按下面的阶段推进，每一阶段只追一个目标。

---

## 推荐阅读顺序

## 第 0 步：辅助预热

### 文件

- `docs/alignment/QUERY_LOOP_COMPLETION_SUMMARY.md`
- `docs/alignment/ATTACHMENT_ISOLATION.md`

### 这一轮要解决的问题

1. 当前 `query_loop` 的阶段划分是什么？
2. attachment 为什么不能直接持久化进消息主链？
3. 这个项目在执行链上已经做过哪些重构？

### 读完后你应该知道

- `query.py` 里不是“随便拼流程”，而是已经有明确阶段；
- attachments 是临时注入，不是持久消息；
- 后面看 `query.py` 时，脑子里要有“每一轮分成哪几段”的框架。

---

## 第 1 步：看高层总控

### 文件

- `codo/query_engine.py`

### 建议优先阅读的方法

- `QueryEngine.__init__`
- `QueryEngine.submit_message_stream`
- `QueryEngine.submit_message`
- `QueryEngine.compact`

### 这一轮要解决的问题

1. `execution_context` 是在哪里初始化的？
2. tools、model、cwd、session、permission_context 是怎么挂进去的？
3. QueryEngine 自己负责什么，不负责什么？
4. 它是怎么把控制权交给 `query()` 的？

### 读完后你应该知道

- `QueryEngine` 是会话级总控，不是工具执行器；
- 运行态共享上下文的源头就是这里的 `execution_context`；
- 工具池、session storage、compact、memory state 都是在这里接入主链的。

---

## 第 2 步：看 Query 主循环

### 文件

- `codo/query.py`

### 建议优先阅读的方法

- `query`
- `query_loop`
- `QueryPhaseTracker` 相关逻辑

### 这一轮要解决的问题

1. 一轮 query 从哪里开始？
2. messages、attachments、system prompt、tool schemas 在什么时候进入 API 调用？
3. `StreamingToolExecutor` 是在哪一步被创建的？
4. 工具结果是怎么增量回流到消息链里的？
5. 什么条件下继续下一轮，什么条件下终止？

### 读完后你应该知道

- 项目的主循环几乎都在这里；
- 这是“模型调用”和“工具执行”之间的总编排点；
- 后面看工具层时，你要一直回到这里确认工具结果是如何被消费的。

---

## 第 3 步：看 Prompt / Attachment 装配

### 文件

- `codo/services/prompt/assembler.py`
- `codo/services/attachments.py`

### 建议优先阅读的方法

- `PromptAssembler.assemble_request`
- `PromptAssembler.assemble_api_request`
- `get_attachment_messages`
- `create_attachment_message`

### 这一轮要解决的问题

1. 真正发给模型的请求体是怎么组装出来的？
2. system prompt 和 messages 是在哪一层合并的？
3. IDE selection、queued commands、plan 等附加上下文是怎么注入的？
4. 哪些内容属于持久上下文，哪些属于本轮临时上下文？

### 读完后你应该知道

- `query.py` 决定何时装配；
- `assembler.py` / `attachments.py` 决定装配成什么样；
- prompt 层和执行层是分开的，不要混着理解。

---

## 第 4 步：看工具契约层

### 文件

- `codo/tools/base.py`

### 建议优先阅读的方法

- `ToolUseContext`
- `Tool.call`
- `Tool.execute`
- `Tool.check_permissions`
- `Tool.validate_input`
- `Tool.build_default_receipt`

### 这一轮要解决的问题

1. 一个“工具”在这个系统里必须满足什么接口？
2. `ToolUseContext` 到底承载哪些运行态信息？
3. `execute()` 和 `call()` 的分层是什么？
4. receipt、validation、permission 这些扩展点分别在哪一层生效？

### 读完后你应该知道

- 工具层的统一契约是什么；
- 为什么最近要统一 `ToolUseContext`；
- 后面不管看哪个具体工具，都能按同一套接口去理解。

---

## 第 5 步：看工具执行主线

### 文件

- `codo/services/tools/streaming_executor.py`

### 建议优先阅读的方法

- `StreamingToolExecutor.__init__`
- `register_tool`
- `_process_queue`
- `_execute_tool_with_abort`
- `_execute_tool`
- `get_completed_results`
- `get_remaining_results`

### 这一轮要解决的问题

1. 模型吐出 `tool_use` 后，工具是怎么被注册和执行的？
2. 并发安全和非并发安全工具是怎么区分的？
3. `validate_input`、`permission`、`call` 的顺序是什么？
4. 结构化 receipt、tool summary、runtime event 是在哪一层产出的？
5. 工具执行失败时，状态机如何推进？

### 读完后你应该知道

- 这是运行时工具链的主入口；
- 主链路里真正的工具调用几乎都要经过这里；
- 如果以后你要查“工具为什么没执行/为什么状态不对”，大概率先回到这个文件。

---

## 第 6 步：看权限判定

### 文件

- `codo/services/tools/permission_checker.py`

### 建议优先阅读的方法

- `has_permissions_to_use_tool`
- `check_path_safety`
- `create_default_permission_context`

### 这一轮要解决的问题

1. allow / ask / deny 的判定顺序是什么？
2. 工具自己的 `check_permissions()` 与全局 permission rule 的关系是什么？
3. safety check 在哪一层插入？
4. `permission_context` 是如何作用到每次工具调用上的？

### 读完后你应该知道

- 权限系统并不是写在某个工具里，而是一个统一前置层；
- 工具特定权限只是其中一部分，不是全部。

---

## 第 7 步：看 compact 分支

### 文件

- `codo/services/compact/microcompact.py`
- `codo/services/compact/prompt.py`
- 如有需要再回看 `codo/query_engine.py` 的 `compact()`

### 建议优先阅读的方法

- `should_compact_tool_result`
- `compact_tool_result_content`
- `microcompact_if_needed`

### 这一轮要解决的问题

1. microcompact 在主链的哪个阶段执行？
2. 为什么要压缩旧工具结果？
3. 哪些结果可以压，哪些不该压？
4. 压缩后对后续模型上下文有什么影响？

### 读完后你应该知道

- compact 不是一个独立小功能，而是控制上下文长度的运行时策略；
- 它直接影响多轮会话可持续性。

---

## 第 8 步：看批处理编排层

### 文件

- `codo/services/tools/orchestration.py`

### 建议优先阅读的方法

- `partition_tool_calls`
- `execute_single_tool`
- `run_batch_concurrently`
- `run_batch_serially`

### 这一轮要解决的问题

1. 这个文件和 `StreamingToolExecutor` 的边界是什么？
2. 什么情况下走这条编排链？
3. 它如何复用 `Tool.execute()` 和权限检查？
4. 它和主流式执行链相比，多了哪些批处理语义？

### 读完后你应该知道

- `orchestration.py` 重要，但它不是理解主链的第一站；
- 它更偏“批量工具执行框架”，而 `StreamingToolExecutor` 更贴近 query loop 主流程。

---

## 第 9 步：最后再补状态分支

### 文件

- `codo/session/storage.py`
- `codo/session/types.py`
- `codo/services/memory/*`
- `codo/team/*`

### 这一轮要解决的问题

1. session 是如何持久化、恢复、命名和绑定 cwd 的？
2. memory 是在什么时候预取、什么时候消费、什么时候提取的？
3. 子代理是如何复用父上下文的？

### 读完后你应该知道

- 这些都不是 query loop 主干，但它们会在主链中插入额外状态；
- 到这一步再补，理解成本最低。

---

## 当前阶段不建议先读的文件

这些文件先不要作为第一站：

- `codo/services/tools/error_handler.py`
- `codo/services/tools/concurrency.py`
- `codo/services/tools/change_review.py`
- `codo/cli/tui/*`

原因很简单：

它们解决的是某个局部问题，而不是“整个项目是怎么跑起来的”。

---

## 最短阅读路径

如果你只想先抓住主干，只读下面 5 个文件：

1. `codo/query_engine.py`
2. `codo/query.py`
3. `codo/tools/base.py`
4. `codo/services/tools/streaming_executor.py`
5. `codo/services/tools/permission_checker.py`

读完这 5 个，再回头看 compact / session / memory / team。

---

## 建议的后续阅读方式

后续我们按这份路线一轮一轮读。

建议顺序：

1. 先从 `QueryEngine` 开始；
2. 再读 `query_loop`；
3. 然后读 `ToolUseContext` 与 `Tool`；
4. 再读 `StreamingToolExecutor`；
5. 最后补权限与 compact。

每一轮阅读都按这个输出格式整理：

1. 这个文件在主链上的位置；
2. 它接收什么输入；
3. 它修改什么状态；
4. 它把控制权交给谁；
5. 这个文件最容易看晕的点是什么。

这样可以避免重新陷回“看了很多文件，但脑子里没有主链”的状态。
