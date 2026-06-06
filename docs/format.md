# 格式定义

> 记录项目中各种数据结构的格式定义。后续新增格式直接往里加。

---

## Session Event Log

会话事件日志，append-only JSONL 格式，每行一个事件。

定义在 `codo/session/types.py`：

```python
class SessionEvent(BaseModel):
    event_id: str
    session_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    agent_id: Optional[str] = None
```

### 所有事件类型

```json
{
  "event_id": "UUID",
  "session_id": "会话 ID",
  "event_type": "message_recorded | metadata_updated | content_replacement | todo_updated | permission_mode_changed | interaction_requested | interaction_resolved | status_changed | checkpoint_restored | interrupt_ack | turn_completed",
  "created_at": "ISO 时间戳",
  "agent_id": "Agent ID（可选）",
  "payload": {

    "message_recorded": {
      "message": {
        "uuid": "消息唯一 ID",
        "role": "user | assistant | tool",
        "content": "消息内容",
        "parent_uuid": "父消息 ID（可选）"
      }
    },

    "metadata_updated": {
      "custom_title": "自定义标题",
      "title": "标题（custom_title 优先）",
      "tag": "标签",
      "agent_name": "Agent 名称",
      "agent_color": "Agent 颜色",
      "mode": "模式",
      "last_prompt": "最后一次 prompt",
      "source": "来源"
    },

    "content_replacement": {
      "uuid": "消息 ID",
      "content": "新内容"
    },

    "todo_updated": {
      "key": "agent ID",
      "items": [
        {"content": "任务描述", "status": "pending | in_progress | completed"}
      ]
    },

    "permission_mode_changed": {
      "permission_mode": "权限模式"
    },

    "interaction_requested": {
      "request": {
        "request_id": "请求 ID",
        "type": "请求类型",
        "tool_name": "工具名称",
        "input": {"command": "..."}
      }
    },

    "interaction_resolved": {
      "request_id": "请求 ID",
      "response": "allow | deny"
    },

    "status_changed": {
      "phase": "当前阶段",
      "turn_count": 3,
      "active_tool_ids": ["toolu_1", "toolu_2"]
    },

    "checkpoint_restored": {
      "checkpoint_id": "检查点 ID"
    },

    "interrupt_ack": {
      "reason": "interrupt | abort"
    },

    "turn_completed": {
      "turn_count": 3
    }

  }
}
```

---

## Execution Context

引擎运行时上下文，贯穿整个 query 生命周期。定义在 `codo/query_engine.py` 的 `_init_runtime_state()`。

### 初始结构

顶层字段初始化后不变，`options` 在运行时会被修改。

```json
{
  "cwd": "当前工作目录（不变）",
  "session_id": "会话 ID（不变）",
  "permission_context": "ToolPermissionContext 对象（不变）",
  "abort_controller": "AbortController 对象（reset 时替换）",
  "options": {
    "api_client": "API 客户端对象",
    "model": "模型名称",
    "tools": ["工具实例列表"],
    "system_prompt": "系统 prompt",
    "normalize_question_mark": true,
    "app_state": {
      "todos": {
        "agent_id": [
          {"content": "任务描述", "status": "pending | in_progress | completed"}
        ]
      }
    }
  }
}
```

### options 运行时修改

| 字段 | 时机 | 说明 |
|------|------|------|
| `tools` | refresh_mcp_tools | 更新工具列表 |
| `system_prompt` | submit_message_stream | 每轮重新构建 |
| `model` | submit_message_stream | 同步模型名 |
| `app_state` | _restore_runtime_state | 恢复 todo 等状态 |

### 运行时动态添加的字段

| 字段 | 位置 | 说明 |
|------|------|------|
| `interaction_broker` | query_engine.py | QueryRuntimeController 对象，用于权限交互 |
| `runtime_controller` | query_engine.py | 同上 |
| `phase_tracker` | query.py | QueryPhaseTracker 对象，追踪引擎阶段 |
| `queued_commands` | query.py | 待执行的 RuntimeCommand 列表 |

---

## RuntimeCheckpoint

运行时检查点，用于重试/恢复。定义在 `codo/runtime_protocol.py`。

```python
class RuntimeCheckpoint:
    checkpoint_id: str
    phase: str
    turn_count: int
    created_at: float
    metadata: dict
```

### metadata 结构

```json
{
  "messages_state": [
    {"uuid": "消息 ID", "role": "user | assistant | tool", "content": "消息内容", "parent_uuid": "父消息 ID"}
  ],
  "message_count": 6,
  "pending_interaction": {
    "request_id": "请求 ID",
    "type": "请求类型",
    "tool_name": "工具名称",
    "input": {"command": "..."}
  },
  "resume_target": "恢复目标标识（可选）"
}
```

- `messages_state`：完整消息列表快照（深拷贝）
- `message_count`：消息数量
- `pending_interaction`：未完成的用户交互（可选）
- `resume_target`：恢复目标（可选）

---

## Messages

对话消息列表，贯穿整个 query 生命周期。定义在 `QueryState.messages`。
规范化逻辑在 `codo/services/prompt/messages.py` 的 `normalize_messages_for_api()`。

### 规范化前（QueryState.messages）

```json
[
  {
    "role": "user",
    "content": "帮我重构这个文件",
    "uuid": "msg-001",
    "type": "user"
  },

  {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "好的，我来看看"},
      {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "a.py"}}
    ],
    "uuid": "msg-002"
  },

  {
    "role": "user",
    "content": [
      {"type": "tool_result", "tool_use_id": "toolu_1", "content": "文件内容..."}
    ],
    "uuid": "msg-003"
  },

  {
    "type": "attachment",
    "attachment": {"type": "memory", "path": "/path/to/memory.md", "content": "记忆内容..."}
  },

  {
    "type": "attachment",
    "attachment": {"type": "queued_command", "prompt": "展开的命令内容", "origin": {"name": "code-review"}}
  },

  {
    "type": "attachment",
    "attachment": {"type": "ide_selection", "filename": "a.py", "text": "选中的代码", "startLine": 10, "endLine": 20}
  },

  {
    "type": "attachment",
    "attachment": {"type": "opened_file_in_ide", "filename": "a.py"}
  },

  {
    "type": "attachment",
    "attachment": {"type": "plan_mode_reminder", "full": true}
  },

  {
    "role": "user",
    "content": "虚拟消息",
    "uuid": "msg-004",
    "is_virtual": true
  },

  {
    "role": "user",
    "content": "压缩后的摘要内容...",
    "uuid": "msg-005",
    "type": "user",
    "is_compact_summary": true
  }
]
```

### 规范化后（发送给 API）

```json
[
  {
    "role": "user",
    "content": "帮我重构这个文件"
  },

  {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "好的，我来看看"},
      {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "a.py"}}
    ]
  },

  {
    "role": "user",
    "content": [
      {"type": "tool_result", "tool_use_id": "toolu_1", "content": "文件内容..."}
    ]
  },

  {
    "role": "user",
    "content": "<system-reminder>Relevant memory attached.\n记忆内容...</system-reminder>"
  }
]
```

### 规范化规则

| 规则 | 说明 |
|------|------|
| 过滤虚拟消息 | `is_virtual: true` 的消息被移除 |
| attachment 转 user | `type: "attachment"` 转为普通 user 消息，内容用 `<system-reminder>` 包裹 |
| 移除额外字段 | `uuid`、`type`、`parent_uuid`、`is_compact_summary` 等内部字段不发给 API |
| 确保交替 | 连续的同角色消息合并，保证 user/assistant 交替 |

### attachment 类型及规范化格式

| attachment 类型 | 规范化后 content |
|----------------|-----------------|
| `queued_command` | `<system-reminder>Slash command /{name} expanded...</system-reminder>\n{prompt}` |
| `ide_selection` | `<system-reminder>The user currently selected code in {filename} ({start}-{end})...</system-reminder>\n{text}` |
| `opened_file_in_ide` | `<system-reminder>The user's active IDE file is {filename}...</system-reminder>` |
| `plan_mode_reminder` | `<system-reminder>Plan mode reminder: stay in planning...</system-reminder>` |
| `memory` | `<system-reminder>Relevant memory attached from {path}.</system-reminder>\n{content}` |
| 其他 | `<system-reminder>Attachment {type}: {payload}</system-reminder>` |
```

---

## Anthropic Streaming Events

`client.messages.stream()` 返回的 SSE 事件流，每个 event 是 SDK 对象。

### event 结构

```python
# content_block_start
{
    "type": "content_block_start",
    "index": 0,
    "content_block": {
        "type": "text | thinking | tool_use",
        "text": "",                        # text 类型，初始空
        "thinking": "",                    # thinking 类型，初始空
        "id": "toolu_xxx",                 # tool_use 类型
        "name": "Read",                    # tool_use 类型
        "input": {}                        # tool_use 类型，初始空
    }
}

# content_block_delta
{
    "type": "content_block_delta",
    "index": 0,
    "delta": {
        "type": "text_delta | thinking_delta | input_json_delta",
        "text": "你好",                    # text_delta，1-4 字符
        "thinking": "让我想想...",          # thinking_delta
        "partial_json": '{"file_path":'    # input_json_delta，JSON 片段
    }
}

# content_block_stop
{
    "type": "content_block_stop",
    "index": 0
}

# message_delta
{
    "type": "message_delta",
    "delta": {
        "stop_reason": "end_turn | tool_use"
    }
}

# message_stop
{
    "type": "message_stop"
}
```

---

## Session 存储文件

每个会话 3 个文件，存放在 `{sessions_dir}/{session_id}.*`：

### 1. `{session_id}.jsonl` — 主会话文件

每行一条消息的 JSON：

```json
{"uuid": "msg-001", "role": "user", "content": "帮我重构这个文件", "parent_uuid": null, "timestamp": "2026-05-03T10:00:00"}
{"uuid": "msg-002", "role": "assistant", "content": "好的，我来看看", "parent_uuid": "msg-001", "timestamp": "2026-05-03T10:00:01"}
{"uuid": "msg-003", "role": "tool", "content": "文件内容...", "parent_uuid": "msg-002", "tool_use_id": "toolu_1", "timestamp": "2026-05-03T10:00:02"}
```

### 2. `{session_id}.events.jsonl` — 事件日志（新格式）

每行一个 SessionEvent，见上方 Session Event Log 部分。

```json
{"event_id": "evt-001", "session_id": "abc123", "event_type": "message_recorded", "payload": {"message": {"uuid": "msg-001", "role": "user", "content": "帮我重构这个文件"}}, "created_at": "2026-05-03T10:00:00", "agent_id": null}
{"event_id": "evt-002", "session_id": "abc123", "event_type": "metadata_updated", "payload": {"custom_title": "重构讨论"}, "created_at": "2026-05-03T10:00:01", "agent_id": null}
{"event_id": "evt-003", "session_id": "abc123", "event_type": "turn_completed", "payload": {"turn_count": 1}, "created_at": "2026-05-03T10:00:05", "agent_id": null}
```

### 3. `{session_id}.snapshot.json` — 快照

事件日志的物化副本，加速加载。由 `save_snapshot()` 从事件日志 replay 生成。

```json
{
  "session_id": "abc123",
  "messages": [
    {"uuid": "msg-001", "role": "user", "content": "帮我重构这个文件", "parent_uuid": null},
    {"uuid": "msg-002", "role": "assistant", "content": "好的，我来看看", "parent_uuid": "msg-001"}
  ],
  "runtime_state": {
    "app_state": {
      "todos": {
        "agent_1": [{"content": "读取文件", "status": "completed"}]
      }
    },
    "permission_mode": "auto"
  },
  "metadata": {
    "session_id": "abc123",
    "custom_title": "重构讨论",
    "tag": "refactor",
    "agent_name": "default",
    "agent_color": "#3B82F6",
    "mode": "auto",
    "last_prompt": "帮我重构这个文件",
    "updated_at": "2026-05-03T10:00:05"
  },
  "last_event_id": "evt-003",
  "created_at": "2026-05-03T10:00:00",
  "updated_at": "2026-05-03T10:00:05"
}
```

---

## RuntimeCommand

UI → 引擎的命令，通过 `_commands` 队列传递。定义在 `codo/runtime_protocol.py`。

```python
class RuntimeCommand(BaseModel):
    type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
```

### 所有命令类型

```json
{
  "type": "interrupt | resolve_interaction | retry_checkpoint",
  "payload": {

    "interrupt": {},

    "resolve_interaction": {
      "request_id": "交互请求 ID",
      "data": "用户响应（True=allow, False=deny）"
    },

    "retry_checkpoint": {
      "checkpoint_id": "检查点 ID"
    }

  }
}
```

### 命令来源

| 命令类型 | 触发场景 | payload |
|---------|---------|---------|
| `interrupt` | 用户按 Ctrl+C | 空 |
| `resolve_interaction` | 用户响应权限弹窗 | `request_id` + `data` |
| `retry_checkpoint` | 用户点击重试按钮 | `checkpoint_id` |

---

## QueryState

Query 循环的可变状态容器，每次 continue 时整体替换。定义在 `codo/query.py`。

```python
@dataclass
class QueryState:
    messages: List[Dict[str, Any]]
    turn_count: int = 1
    auto_compact_tracking: Optional[AutoCompactState] = None
    has_attempted_reactive_compact: bool = False
    max_output_tokens_recovery_count: int = 0
    max_output_tokens_override: Optional[int] = None
    phase: str = "prepare_turn"
    active_tool_ids: List[str] = field(default_factory=list)
    pending_interaction: Optional[Dict[str, Any]] = None
    checkpoint_id: Optional[str] = None
    pending_tool_use_summary: Optional[Any] = None
    stop_hook_active: Optional[bool] = None
    transition: Optional[Dict[str, Any]] = None
    active_agent_id: Optional[str] = None
    interrupt_reason: Optional[str] = None
    resume_target: Optional[str] = None
```

### 完整结构

```json
{
  "messages": [
    {"role": "user", "content": "...", "uuid": "msg_001"}
  ],

  "turn_count": 3,

  "auto_compact_tracking": {
    "compacted": false,
    "turn_counter": 3,
    "consecutive_failures": 0
  },

  "has_attempted_reactive_compact": false,
  "max_output_tokens_recovery_count": 0,
  "max_output_tokens_override": null,

  "phase": "prepare_turn | stream_assistant | collect_tool_results | dispatch_tools | compact | stop_hooks | complete | error",

  "active_tool_ids": ["toolu_1", "toolu_2"],

  "pending_interaction": {
    "type": "permission",
    "tool": "Bash",
    "command": "rm -rf /tmp",
    "request_id": "perm_001"
  },

  "checkpoint_id": "ckpt_a1b2c3d4",
  "pending_tool_use_summary": null,
  "stop_hook_active": null,

  "transition": {"reason": "max_output_tokens_recovery"},

  "active_agent_id": null,
  "interrupt_reason": "user_cancel",
  "resume_target": null
}
```

### 字段分组

| 分组 | 字段 | 说明 |
|------|------|------|
| 核心状态 | `messages`, `turn_count` | 消息历史和轮次计数 |
| Token 管理 | `auto_compact_tracking`, `has_attempted_reactive_compact`, `max_output_tokens_recovery_count`, `max_output_tokens_override` | 压缩和 token 截断恢复 |
| 执行状态 | `phase`, `active_tool_ids`, `pending_interaction`, `checkpoint_id` | 当前执行阶段和工具追踪 |
| UI 和调试 | `pending_tool_use_summary`, `stop_hook_active`, `transition` | UI 展示和状态迁移追踪 |
| 高级功能 | `active_agent_id`, `interrupt_reason`, `resume_target` | 嵌套 Agent、中断、恢复 |
