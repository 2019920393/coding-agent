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
