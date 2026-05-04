# 会话系统设计演练：从零开始

> 本文档还原了从零设计会话系统的完整思考过程。
> 每个阶段都有：问题 → 思考 → 代码 → 下一个问题。

---

## 阶段 0：需求分析

### 我要解决什么问题？

```
用户关掉终端，下次打开，能继续上次的对话。
```

拆解成子需求：
1. 对话消息要存到磁盘（持久化）
2. 能读回来，显示在屏幕上（恢复）
3. 能找到"上一个会话"或"指定会话"（查询）
4. 不同项目互不干扰（隔离）

### 我该选什么存储格式？

问自己：数据长什么样？

```
一条一条的消息，按时间顺序追加，不会随机修改。
```

这是典型的 **日志型数据**。对比几种方案：

| 方案 | 优点 | 缺点 |
|------|------|------|
| SQLite | 支持复杂查询 | 需要 schema migration，追加写入不如日志流畅 |
| JSON 文件 | 人类可读 | 追加写入要重写整个文件 |
| **JSONL** | 追加写入高效，每行独立解析 | 不支持随机访问（但我不需要） |

选 JSONL。每行一个 JSON 对象，追加写入就是 `write(line + '\n')`。

### 我的项目在磁盘上长什么样？

```
~/.codo/sessions/           ← 全局根目录
  <project-hash>/           ← 项目隔离
    abc123.jsonl            ← 会话文件
    def456.jsonl            ← 另一个会话
```

为什么用 project-hash？因为同一个用户可能有多个项目，不能混在一起。

---

## 阶段 1：最小可用版本

### 现在我要写代码了，从哪里开始？

**先写类型，再写逻辑。** 因为类型定义了"数据长什么样"，逻辑是"怎么操作数据"。

```python
# types.py — 先定义数据长什么样
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class Message:
    """一条消息"""
    role: str              # "user" 或 "assistant"
    content: Any           # 文本字符串，或 content blocks 列表
    uuid: Optional[str] = None  # 消息唯一标识
```

然后写最核心的存储类：

```python
# storage.py — 只有 3 个功能：存、读、找文件
import json
from pathlib import Path
from typing import List, Dict, Any

class SessionStorage:
    """
    会话存储 - 最简版本

    只做一件事：把消息写到 JSONL 文件，再读回来。
    """

    def __init__(self, session_id: str, cwd: str):
        """
        Args:
            session_id: 会话 ID（就是文件名）
            cwd: 当前工作目录（用来算 project hash）
        """
        self.session_id = session_id
        self.cwd = cwd
        # 文件路径：~/.codo/sessions/<project-hash>/<session_id>.jsonl
        self.session_file = self._get_session_file_path()

    def _get_session_file_path(self) -> Path:
        """计算会话文件的路径"""
        import hashlib
        # 用 cwd 的 hash 作为项目目录名
        project_hash = hashlib.md5(self.cwd.encode()).hexdigest()[:8]
        sessions_dir = Path.home() / ".codo" / "sessions" / project_hash
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir / f"{self.session_id}.jsonl"

    def save_message(self, role: str, content: Any, uuid: str = None):
        """
        保存一条消息

        就是往文件末尾追加一行 JSON。
        """
        entry = {
            "type": "message",
            "role": role,
            "content": content,
        }
        if uuid:
            entry["uuid"] = uuid

        # 追加写入
        with open(self.session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load_messages(self) -> List[Dict[str, Any]]:
        """
        加载所有消息

        就是读文件，逐行解析 JSON，过滤出消息类型的记录。
        """
        if not self.session_file.exists():
            return []

        messages = []
        with open(self.session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("type") == "message":
                    messages.append(record)

        return messages
```

### 用起来是什么感觉？

```python
# 创建会话
storage = SessionStorage(session_id="abc123", cwd="/home/user/project")

# 存消息
storage.save_message("user", "你好")
storage.save_message("assistant", "你好！我是 Codo。")
storage.save_message("user", "帮我写一个函数")
storage.save_message("assistant", "好的，这是一个计算斐波那契的函数...")

# 读消息
messages = storage.load_messages()
for msg in messages:
    print(f"[{msg['role']}] {msg['content']}")
```

磁盘上的文件长这样：

```jsonl
{"type": "message", "role": "user", "content": "你好"}
{"type": "message", "role": "assistant", "content": "你好！我是 Codo。"}
{"type": "message", "role": "user", "content": "帮我写一个函数"}
{"type": "message", "role": "assistant", "content": "好的，这是一个计算斐波那契的函数..."}
```

### 这个版本能用，但有什么问题？

1. **不知道哪个是最新的会话** — 只能手动指定 session_id
2. **没有分支** — 如果用户编辑了第 2 条消息，后面的消息怎么办？
3. **没有元数据** — 标题、标签存在哪？
4. **加载慢** — 每次都要从头读整个文件

先不管这些，让最小版本跑起来。然后逐个解决。

---

## 阶段 2：会话管理 — 多会话支持

### 问题：怎么找到"上一个会话"？

用户打开终端，输入 `codo --continue`，系统要自动找到最近的会话。

### 思考

我需要一个函数，扫描项目目录下的所有 `.jsonl` 文件，按修改时间排序，返回最新的那个。

```python
# query.py — 会话查询
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import hashlib

@dataclass
class SessionInfo:
    """会话的基本信息（用于列表展示）"""
    session_id: str        # 文件名（不含扩展名）
    summary: str           # 摘要（第一行用户消息）
    last_modified: float   # 最后修改时间
    file_size: int         # 文件大小
    custom_title: str = None  # 用户自定义标题

def get_sessions_dir(cwd: str) -> Path:
    """获取项目的会话目录"""
    project_hash = hashlib.md5(cwd.encode()).hexdigest()[:8]
    return Path.home() / ".codo" / "sessions" / project_hash

def list_all_sessions(cwd: str) -> List[SessionInfo]:
    """
    列出项目的所有会话

    思路：扫描目录，读每个文件的 stat 信息，按修改时间排序。
    """
    sessions_dir = get_sessions_dir(cwd)
    if not sessions_dir.exists():
        return []

    sessions = []
    for file in sessions_dir.glob("*.jsonl"):
        # 读文件的 stat
        stat = file.stat()
        # 读第一行用户消息作为摘要
        summary = _extract_summary(file)

        sessions.append(SessionInfo(
            session_id=file.stem,  # 文件名去掉 .jsonl
            summary=summary,
            last_modified=stat.st_mtime,
            file_size=stat.st_size,
        ))

    # 按修改时间倒序（最新的在前）
    sessions.sort(key=lambda s: s.last_modified, reverse=True)
    return sessions

def get_last_session(cwd: str) -> Optional[SessionInfo]:
    """获取最近的会话"""
    sessions = list_all_sessions(cwd)
    return sessions[0] if sessions else None

def find_session_by_id(session_id: str, cwd: str) -> Optional[SessionInfo]:
    """按 ID 查找会话"""
    sessions = list_all_sessions(cwd)
    for s in sessions:
        if s.session_id == session_id:
            return s
    return None

def _extract_summary(file_path: Path) -> str:
    """读文件的第一条用户消息作为摘要"""
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line.strip())
            if record.get("type") == "message" and record.get("role") == "user":
                content = record.get("content", "")
                if isinstance(content, str):
                    return content[:100]
                elif isinstance(content, list) and content:
                    return content[0].get("text", "")[:100]
    return ""
```

### 用起来是什么感觉？

```python
# 继续上一个会话
last = get_last_session("/home/user/project")
if last:
    storage = SessionStorage(last.session_id, "/home/user/project")
    messages = storage.load_messages()
    print(f"继续会话: {last.summary}")

# 按 ID 恢复
session = find_session_by_id("abc123", "/home/user/project")
if session:
    storage = SessionStorage(session.session_id, "/home/user/project")
```

---

## 阶段 3：消息链 — 分支和追溯

### 问题：什么是"编辑中间消息"？

这是终端里的一个交互功能。你在对话时，不是只能一直往下聊，可以上翻到之前的某条消息，改掉它，然后重新发。

举个具体场景：

```
你: 帮我写一个登录函数          ← 轮次 1
AI: 好的，用 session 实现...    ← 轮次 2
你: 不对，用 JWT                ← 轮次 3
AI: 好的，用 JWT 实现...        ← 轮次 4
你: 把过期时间改成 7 天          ← 轮次 5
AI: 好的，改成 7 天...          ← 轮次 6
```

聊到第 6 轮你觉得不对劲，想回到第 3 轮重新来。你按上箭头，光标回到第 3 轮，把 "不对，用 JWT" 改成 "不对，用 OAuth2"，然后回车。

这时候对话就**分叉**了：

```
轮次1: "帮我写登录函数"
  ├── 轮次3(旧): "用JWT" → 轮次4(旧) → 轮次5(旧) → 轮次6(旧)   ← 旧分支，废弃
  └── 轮次3(新): "用OAuth2" → 轮次4(新): "..."                   ← 新分支，继续
```

两条分支共用同一个父节点（轮次2），但走向不同。

### 如果没有分支机制会怎样？

编辑完旧消息还在，新消息也追加进去，AI 的上下文就乱了——它同时看到 "用JWT" 和 "用OAuth2"，不知道该听哪个。

所以需要 `parent_uuid` 链表：从最新的叶子节点往回追溯，只走新分支，旧分支自动被忽略。

### 思考

用**链表结构**：每条消息有 `uuid`（自己的 ID）和 `parent_uuid`（父消息的 ID）。

```
msg-1 (parent: null)
  └── msg-2 (parent: msg-1)
        └── msg-3 (parent: msg-2)
              └── msg-4 (parent: msg-3)    ← 旧分支
              └── msg-3-new (parent: msg-2) ← 新分支
                    └── msg-5 (parent: msg-3-new)
```

要显示哪条链路？从最新的叶子节点往回追溯。

### 代码

```python
# 在 types.py 中添加
@dataclass
class Message:
    role: str
    content: Any
    uuid: str                    # 必须有
    parent_uuid: str = None      # 父消息的 UUID
    type: str = "message"        # 类型标识
```

```python
# storage.py — 消息链处理（实际项目中放在 storage.py 里，没有单独的 chain.py）
from typing import List, Dict, Any, Optional, Set

def build_conversation_chain(
    messages: List[Dict[str, Any]],
    leaf_uuid: str
) -> List[Dict[str, Any]]:
    """
    从叶子节点往回追溯，构建对话链路。

    思路：
    1. 建一个 uuid → message 的索引
    2. 从 leaf_uuid 开始，沿着 parent_uuid 往回走
    3. 反转，得到从头到尾的链路

    Args:
        messages: 所有消息（包括分支的）
        leaf_uuid: 叶子节点的 UUID

    Returns:
        从根到叶的消息列表
    """
    # 建索引
    msg_map = {msg["uuid"]: msg for msg in messages}

    # 从叶子往回走
    chain = []
    current = leaf_uuid
    while current and current in msg_map:
        chain.append(msg_map[current])
        current = msg_map[current].get("parent_uuid")

    # 反转（从头到尾）
    chain.reverse()
    return chain

def find_leaf_nodes(messages: List[Dict[str, Any]]) -> List[str]:
    """
    找到所有叶子节点。

    叶子节点 = 没有任何消息的 parent_uuid 指向它。

    思路：
    1. 收集所有被引用的 uuid（作为 parent_uuid 出现的）
    2. 不在被引用集合中的 uuid 就是叶子
    """
    all_uuids = set()
    referenced_uuids = set()

    for msg in messages:
        uuid = msg.get("uuid")
        parent = msg.get("parent_uuid")
        if uuid:
            all_uuids.add(uuid)
        if parent:
            referenced_uuids.add(parent)

    # 叶子 = 所有 uuid - 被引用的 uuid
    leaves = all_uuids - referenced_uuids
    return list(leaves)
```

### 用起来是什么感觉？

```python
# 加载所有消息（包括分支）
all_messages = storage.load_messages()

# 找到叶子节点
leaves = find_leaf_nodes(all_messages)

# 构建最新的一条链路
latest_leaf = leaves[-1]  # 按时间排序，取最新的
chain = build_conversation_chain(all_messages, latest_leaf)

# 显示对话
for msg in chain:
    print(f"[{msg['role']}] {msg['content']}")
```

磁盘上的文件长这样：

```jsonl
{"type":"message","role":"user","content":"你好","uuid":"msg-1"}
{"type":"message","role":"assistant","content":"你好！","uuid":"msg-2","parent_uuid":"msg-1"}
{"type":"message","role":"user","content":"帮我写代码","uuid":"msg-3","parent_uuid":"msg-2"}
{"type":"message","role":"assistant","content":"好的...","uuid":"msg-4","parent_uuid":"msg-3"}
{"type":"message","role":"user","content":"帮我写测试","uuid":"msg-3-new","parent_uuid":"msg-2"}
{"type":"message","role":"assistant","content":"好的，写测试...","uuid":"msg-5","parent_uuid":"msg-3-new"}
```

显示时只会显示：msg-1 → msg-2 → msg-3-new → msg-5（跳过了旧的 msg-3 和 msg-4）。

---

## 阶段 4：元数据 — 标题、标签、状态

### 问题：标题和标签是干什么的？

**标题** — 给会话起个名字，方便找。

```
没有标题时，用户看到的是：
  - "你好"           ← 只能靠第一句话猜
  - "帮我写代码"
  - "bug 修复"

有标题后：
  - "登录模块重构"
  - "首页性能优化"
  - "数据库迁移"
```

用户输入 `codo -r "登录"` 就能精确找到那个会话。没有标题就只能靠第一句话模糊匹配，很不靠谱。

**标签** — 分类。比如 `bug-fix`、`feature`、`refactor`，后续可以按标签筛选历史会话。

### 问题：这些元数据存在哪？

两个选择：
1. 单独一个文件存元数据
2. 和消息存在同一个 JSONL 里

选方案 2，因为：
- 只需要管理一个文件
- 追加写入一样简单
- 用 `type` 字段区分即可

### 思考

在 JSONL 中混入不同类型的记录：

```jsonl
{"type":"message","role":"user","content":"你好","uuid":"msg-1"}
{"type":"custom-title","custom_title":"我的项目","session_id":"abc123"}
{"type":"tag","tag":"bug-fix","session_id":"abc123"}
{"type":"agent-name","agent_name":"code-reviewer","session_id":"abc123"}
{"type":"mode","mode":"plan","session_id":"abc123"}
```

读取时，按 `type` 分别处理。

### 代码

```python
# types.py — 添加元数据类型
from typing import Literal

@dataclass
class CustomTitleEntry:
    type: Literal["custom-title"] = "custom-title"
    custom_title: str = ""
    session_id: str = ""
    timestamp: str = None

@dataclass
class TagEntry:
    type: Literal["tag"] = "tag"
    tag: str = ""
    session_id: str = ""
    timestamp: str = None

@dataclass
class AgentNameEntry:
    type: Literal["agent-name"] = "agent-name"
    agent_name: str = ""
    session_id: str = ""
    timestamp: str = None

@dataclass
class ModeEntry:
    type: Literal["mode"] = "mode"
    mode: str = ""
    session_id: str = ""
    timestamp: str = None
```

```python
# 在 SessionStorage 中添加元数据方法

def save_title(self, title: str, source: str = "user"):
    """保存标题"""
    entry = {
        "type": "custom-title" if source == "user" else "ai-title",
        "custom_title": title,
        "source": source,
        "session_id": self.session_id,
        "timestamp": datetime.now().isoformat(),
    }
    self._append_entry(entry)

def save_tag(self, tag: str):
    """保存标签"""
    entry = {
        "type": "tag",
        "tag": tag,
        "session_id": self.session_id,
        "timestamp": datetime.now().isoformat(),
    }
    self._append_entry(entry)

def save_mode(self, mode: str):
    """保存模式"""
    entry = {
        "type": "mode",
        "mode": mode,
        "session_id": self.session_id,
        "timestamp": datetime.now().isoformat(),
    }
    self._append_entry(entry)

def _append_entry(self, entry: dict):
    """通用的追加写入"""
    with open(self.session_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

### 元数据怎么读回来？

```python
# restore.py — 从文件中提取元数据

def extract_metadata_from_transcript(records: list) -> dict:
    """
    从会话记录中提取所有元数据。

    思路：遍历所有记录，按 type 分类存储。
    """
    metadata = {
        "custom_titles": {},   # {session_id: title}
        "tags": {},            # {session_id: tag}
        "agent_names": {},     # {session_id: name}
        "modes": {},           # {session_id: mode}
    }

    for record in records:
        record_type = record.get("type")

        if record_type == "custom-title":
            session_id = record.get("session_id")
            title = record.get("custom_title")
            if session_id and title:
                metadata["custom_titles"][session_id] = title

        elif record_type == "tag":
            session_id = record.get("session_id")
            tag = record.get("tag")
            if session_id and tag:
                metadata["tags"][session_id] = tag

        elif record_type == "agent-name":
            session_id = record.get("session_id")
            name = record.get("agent_name")
            if session_id and name:
                metadata["agent_names"][session_id] = name

        elif record_type == "mode":
            session_id = record.get("session_id")
            mode = record.get("mode")
            if session_id and mode:
                metadata["modes"][session_id] = mode

    return metadata
```

---

## 阶段 5：查询系统 — 找会话

### 问题：用户说 "恢复标题叫 XXX 的会话"

需要支持按标题搜索。

### 思考

已经有 `list_all_sessions()` 可以列出所有会话。搜索就是在列表里过滤。

```python
# query.py — 添加搜索功能

def search_sessions_by_title(
    query: str,
    cwd: str,
    exact: bool = False
) -> List[SessionInfo]:
    """
    按标题搜索会话。

    Args:
        query: 搜索关键词
        cwd: 项目目录
        exact: True=精确匹配, False=模糊匹配

    Returns:
        匹配的会话列表
    """
    sessions = list_all_sessions(cwd)
    results = []

    for session in sessions:
        # 优先用自定义标题，没有就用摘要
        title = session.custom_title or session.summary
        if not title:
            continue

        if exact:
            if title == query:
                results.append(session)
        else:
            if query.lower() in title.lower():
                results.append(session)

    return results

def validate_uuid(text: str) -> bool:
    """检查字符串是否是合法的 UUID"""
    try:
        UUID(text)
        return True
    except ValueError:
        return False
```

### 用起来是什么感觉？

```python
# 按 UUID 恢复
if validate_uuid(input_str):
    session = find_session_by_id(input_str, cwd)

# 按标题搜索
else:
    matches = search_sessions_by_title(input_str, cwd)
    if len(matches) == 1:
        session = matches[0]
    elif len(matches) > 1:
        print("找到多个匹配，请选择：")
        for i, s in enumerate(matches):
            print(f"  {i+1}. {s.custom_title or s.summary}")
    else:
        print("未找到匹配的会话")
```

---

## 阶段 6：恢复系统 — 重建状态

### 问题：加载会话后，怎么恢复所有状态？

不只是消息，还有：
- TODO 列表
- Agent 设置（explore/code-reviewer）
- 元数据（标题、标签）

### 思考

写一个统一的恢复函数，把所有状态从文件中提取出来：

```python
# restore.py — 会话恢复

def load_session_for_resume(
    session_id: Optional[str],
    project_dir: str
) -> Optional[Dict[str, Any]]:
    """
    加载会话用于恢复。

    这是恢复系统的入口函数。

    Args:
        session_id: 会话 ID（None 表示找最近的）
        project_dir: 项目目录

    Returns:
        恢复数据字典，失败返回 None
    """
    # 步骤 1: 找到会话
    if session_id is None:
        session_info = get_last_session(project_dir)
    else:
        session_info = find_session_by_id(session_id, project_dir)

    if not session_info:
        return None

    # 步骤 2: 读取文件
    file_path = get_session_file_path(session_info.session_id, project_dir)
    if not file_path.exists():
        return None

    records = parse_jsonl_transcript(str(file_path))

    # 步骤 3: 提取各种状态
    messages = extract_messages_from_transcript(records)
    todos = extract_todos_from_transcript(records)
    agent_setting = extract_agent_setting_from_transcript(records)
    metadata = extract_metadata_from_transcript(records)

    # 步骤 4: 返回
    return {
        "session_info": session_info,
        "messages": messages,
        "todos": todos,
        "agent_setting": agent_setting,
        "metadata": metadata,
        "file_path": str(file_path),
    }
```

### 怎么提取 TODO？

TODO 是在 assistant 消息的 tool_use 块里：

```json
{
    "type": "assistant",
    "content": [
        {
            "type": "tool_use",
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "写测试", "status": "pending"},
                    {"content": "重构", "status": "in_progress"}
                ]
            }
        }
    ]
}
```

提取逻辑：

```python
def extract_todos_from_transcript(records: list) -> list:
    """
    从后往前找最后一个 TodoWrite，提取 todos。

    为什么从后往前？因为要最新的状态。
    """
    for record in reversed(records):
        if record.get("role") != "assistant":
            continue

        content = record.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if (isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "TodoWrite"):
                return block.get("input", {}).get("todos", [])

    return []
```

---

## 阶段 7：性能优化 — 快照机制

### 问题：加载慢慢在哪里？

慢在**遍历**。假设一个会话有 200 条消息 + 30 条元数据：

```
文件内容（JSONL）：
line 1:   {"type":"message", ...}     ← 第 1 条消息
line 2:   {"type":"message", ...}     ← 第 2 条消息
...
line 200: {"type":"message", ...}     ← 第 200 条消息
line 201: {"type":"custom-title", ...}
line 202: {"type":"tag", ...}
...
line 230: {"type":"mode", ...}
```

每次加载都要：

```
1. 读整个文件（230 行）           ← 磁盘 IO
2. 逐行解析 JSON（230 次）        ← CPU
3. 遍历找最后一条 TODO            ← 从头扫到尾
4. 遍历找 agent 设置              ← 又从头扫到尾
5. 遍历找元数据                   ← 又从头扫到尾
6. 构建消息链                     ← 遍历所有消息建索引
```

**没有快照**：每次打开终端都要走这个流程，几百毫秒。
**有快照**：直接读一个 JSON 文件，几毫秒。

### 思考

引入**快照**：定期把当前状态保存到一个单独的文件，加载时先读快照。

```
快照文件 = 当前状态的缓存
加载时 = 读快照 + 追加读后面的事件
```

```python
# snapshot.json — 当前状态的快照
{
    "session_id": "abc123",
    "messages": [
        {"uuid": "msg-1", "role": "user", "content": "你好"},
        {"uuid": "msg-2", "role": "assistant", "content": "你好！"}
    ],
    "metadata": {
        "session_id": "abc123",
        "custom_title": "我的项目"
    },
    "runtime_state": {
        "app_state": {
            "todos": {
                "abc123": [
                    {"content": "写测试", "status": "pending"}
                ]
            }
        }
    },
    "updated_at": "2024-01-01T12:00:00"
}
```

### 加载逻辑

```python
def load_messages(self) -> List[Dict[str, Any]]:
    """
    加载消息，优先用快照。

    思路：
    1. 有快照 → 读快照（快）
    2. 没快照 → 从事件日志重建（慢但准确）
    """
    # 先试快照
    snapshot = self.load_snapshot()
    if snapshot and snapshot.messages:
        return snapshot.messages

    # 没有快照，从事件日志重建
    events = self.load_events()
    messages = []
    for event in events:
        if event.event_type == "message_recorded":
            messages.append(event.payload["message"])

    return messages

def save_snapshot(self):
    """
    保存当前状态的快照。

    每次有新消息或元数据变更时调用。
    """
    snapshot = {
        "session_id": self.session_id,
        "messages": self._get_current_messages(),
        "metadata": self._get_current_metadata(),
        "updated_at": datetime.now().isoformat(),
    }
    with open(self.snapshot_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
```

---

## 阶段 8：事件日志 — 为什么要第三个文件？

### 问题：已经有 JSONL 和快照了，为什么还要事件日志？

先看三个文件各自的角色：

```
transcript.jsonl   ← 消息 + 元数据混在一起，给"恢复对话"用
snapshot.json      ← 最新状态的缓存，给"快速加载"用
events.jsonl       ← 运行时事件，给"恢复 UI 状态"用
```

JSONL 存的是**对话内容**（用户说了什么，AI 回了什么）。
事件日志存的是**运行时事件**（TODO 更新了、权限弹窗了、中断了）。

举个例子：用户在对话过程中，AI 调用了 TodoWrite 更新了任务列表。这个 TODO 变更不是对话内容，而是**运行时状态变更**。如果只存 JSONL，TODO 的变更历史就丢了。

### 事件日志里有什么？

```jsonl
{"event_type":"message_recorded","payload":{"message":{...}}}
{"event_type":"message_recorded","payload":{"message":{...}}}
{"event_type":"todo_updated","payload":{"key":"abc123","items":[...]}}
{"event_type":"interaction_requested","payload":{"request":{...}}}
{"event_type":"interrupt_ack","payload":{"checkpoint_id":"cp-1"}}
{"event_type":"status_changed","payload":{"phase":"complete"}}
```

每种事件类型对应一种运行时状态变更：

| 事件类型 | 含义 |
|---------|------|
| `message_recorded` | 新消息写入 |
| `metadata_updated` | 标题/标签/模式变更 |
| `todo_updated` | TODO 列表更新 |
| `interaction_requested` | 权限弹窗请求 |
| `interaction_resolved` | 权限弹窗关闭 |
| `interrupt_ack` | 用户中断了 AI |
| `status_changed` | 运行阶段变更（idle/running/complete） |
| `checkpoint_restored` | 从检查点恢复 |
| `content_replacement` | 工具结果被截断 |

### 代码

```python
# 事件日志的写入 — 追加
def append_event(self, event_type: str, payload: dict) -> SessionEvent:
    """追加运行时事件到 append-only event log。"""
    event = SessionEvent(
        event_id=str(uuid4()),
        session_id=self.session_id,
        event_type=event_type,
        payload=payload,
        created_at=datetime.now().isoformat(),
    )
    with open(self.event_log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event.model_dump()) + "\n")
    return event

# 事件日志的读取 — 逐行解析
def load_events(self) -> List[SessionEvent]:
    """加载当前会话的事件日志。"""
    events = []
    if not self.event_log_file.exists():
        return events
    with open(self.event_log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(SessionEvent.model_validate_json(line))
            except Exception:
                continue
    return events
```

### 消息写入时，同时写两个地方

```python
def record_messages(self, messages, parent_uuid=None):
    """记录消息链"""
    # 1. 写入 JSONL（给对话恢复用）
    last_uuid = self._insert_message_chain(new_messages, current_parent)

    # 2. 写入事件日志（给运行时状态恢复用）
    for message in new_messages:
        self.append_event("message_recorded", {
            "message": dict(message),
            "parent_uuid": message.get("parent_uuid"),
        })

    # 3. 更新快照（给快速加载用）
    self.save_snapshot()
```

**一句话**：JSONL 存"说了什么"，事件日志存"发生了什么"，快照存"现在是什么状态"。

---

## 阶段 9：运行时状态 — TODO、权限、中断

### 问题：除了对话内容，还有哪些状态需要恢复？

用户关掉终端再打开，不只要恢复对话，还要恢复：

1. **TODO 列表** — AI 之前设置的任务进度
2. **权限弹窗** — AI 请求执行某个操作，用户还没回复
3. **运行阶段** — AI 正在运行？被中断了？已完成？
4. **检查点** — 中断后可以从哪个点重试

这些统称为**运行时状态**。

### 运行时状态长什么样？

```python
runtime_state = {
    "app_state": {
        "todos": {
            "abc123": [                    # session_id 作为 key
                {"content": "写测试", "status": "pending"},
                {"content": "重构", "status": "in_progress"}
            ]
        }
    },
    "pending_interaction": {               # 待处理的权限弹窗
        "request_id": "req-1",
        "kind": "permission",
        "message": "Allow writing files?",
        "options": [...]
    },
    "runtime_phase": "running",            # 当前阶段
    "last_checkpoint_id": "cp-1",          # 最后一个检查点
    "retry_checkpoint_id": "cp-1",         # 可重试的检查点
    "replay_events": [...]                 # 需要重放的事件
}
```

### 怎么从事件日志派生运行时状态？

遍历事件日志，按事件类型更新状态：

```python
def build_runtime_state_from_events(events: list) -> dict:
    """从事件日志派生运行时状态。"""
    runtime_state = {
        "app_state": {"todos": {}},
        "pending_interaction": None,
        "runtime_phase": None,
        "last_checkpoint_id": None,
        "retry_checkpoint_id": None,
        "replay_events": [],
    }
    todos = runtime_state["app_state"]["todos"]

    for event in events:
        if event.event_type == "todo_updated":
            # TODO 更新 → 替换整个列表
            key = event.payload.get("key", "")
            items = event.payload.get("items", [])
            todos[key] = items

        elif event.event_type == "interaction_requested":
            # 权限弹窗 → 记录待处理请求
            runtime_state["pending_interaction"] = event.payload.get("request")

        elif event.event_type == "interaction_resolved":
            # 权限弹窗关闭 → 清除待处理
            runtime_state["pending_interaction"] = None

        elif event.event_type == "interrupt_ack":
            # 用户中断 → 标记为中断状态
            runtime_state["runtime_phase"] = "interrupted"
            runtime_state["pending_interaction"] = None
            checkpoint_id = event.payload.get("checkpoint_id", "")
            if checkpoint_id:
                runtime_state["retry_checkpoint_id"] = checkpoint_id

        elif event.event_type == "status_changed":
            # 阶段变更 → 更新 phase
            phase = event.payload.get("phase", "")
            if phase:
                runtime_state["runtime_phase"] = phase

        elif event.event_type == "turn_completed":
            # 一轮完成 → 清除临时状态
            runtime_state["runtime_phase"] = "complete"
            runtime_state["pending_interaction"] = None

    return runtime_state
```

### 加载时怎么用？

```python
def load_runtime_state(self) -> dict:
    """加载运行时状态，优先用快照。"""
    events = self.load_events()
    snapshot = self.load_snapshot()

    # 快照是最新的 → 直接用快照的 runtime_state
    if snapshot and snapshot.last_event_id == events[-1].event_id:
        return snapshot.runtime_state

    # 快照不是最新的 → 从事件日志重新派生
    return build_runtime_state_from_events(events)
```

**一句话**：运行时状态 = 事件日志的"最新快照"。每次读事件日志，重新计算出当前状态。

---

## 阶段 10：物化和规范化 — 处理边界情况

### 问题 1：会话文件什么时候创建？

如果用户打开终端就关掉，什么都没说，不应该创建空的会话文件。

所以引入**延迟创建**（lazy materialization）：

```python
class SessionStorage:
    def __init__(self, session_id, cwd):
        self.session_file = None  # 不立即创建文件
        self.pending_entries = []  # 缓存待写入的条目

    def record_messages(self, messages, ...):
        # 第一条真正的消息到来时，才创建文件
        if self.session_file is None and self.should_materialize(messages):
            self.materialize_session_file()
        # ...

    def should_materialize(self, messages):
        """只有 user/assistant 消息才触发创建"""
        return any(
            msg.get("role") in ("user", "assistant")
            for msg in messages
        )
```

**为什么？** 避免磁盘上出现一堆空文件。

### 问题 2：消息格式不统一怎么办？

不同来源的消息格式可能不一样：

```python
# 来源 A：content 是字符串
{"role": "user", "content": "你好", "uuid": "msg-1"}

# 来源 B：content 是列表
{"role": "user", "content": [{"type": "text", "text": "你好"}], "uuid": "msg-1"}

# 来源 C：缺少字段
{"role": "user", "content": "你好"}  # 没有 uuid

# 来源 D：字段名不对
{"type": "user", "content": "你好", "uuid": "msg-1"}  # type 而不是 role
```

所以需要**规范化**：

```python
def _normalize_message_record(raw_message, *, fallback_parent_uuid=None, fallback_timestamp=None):
    """把各种格式的消息统一成标准格式。"""
    if not isinstance(raw_message, dict):
        return None  # 不是字典，丢弃

    # 统一 type 和 role
    msg_type = str(raw_message.get("type") or raw_message.get("role") or "").strip().lower()
    if msg_type not in {"user", "assistant"}:
        return None  # 不是对话消息，丢弃

    # 必须有 uuid
    msg_uuid = raw_message.get("uuid")
    if not msg_uuid:
        return None  # 没有 uuid，丢弃

    # 构建标准化的消息
    normalized = dict(raw_message)
    normalized["type"] = msg_type
    normalized["role"] = msg_type
    normalized["uuid"] = str(msg_uuid)

    # 补全缺失的 parent_uuid
    if fallback_parent_uuid and not normalized.get("parent_uuid"):
        normalized["parent_uuid"] = fallback_parent_uuid

    # 补全缺失的 timestamp
    if not normalized.get("timestamp"):
        normalized["timestamp"] = fallback_timestamp or datetime.now().isoformat()

    # 规范化 content
    normalized["content"] = _normalize_message_content(normalized.get("content"))
    return normalized
```

### 问题 3：快照里的消息可能损坏怎么办？

快照是手动覆盖写的，如果写入过程中断了，可能有损坏的消息：

```json
{
    "messages": [
        {"role": "user", "content": "你好", "uuid": "msg-1"},     // 正常
        "bad-message-entry",                                        // 损坏！
        {"role": "assistant", "content": "...", "uuid": "msg-2"},  // 正常
        {"role": "assistant", "content": "..."}                     // 缺少 uuid
    ]
}
```

所以需要**清洗**：

```python
def _sanitize_snapshot_messages(raw_messages):
    """清洗快照中的消息列表，丢弃损坏的条目。"""
    if not isinstance(raw_messages, list):
        return []
    sanitized = []
    for raw_message in raw_messages:
        normalized = _normalize_message_record(raw_message)
        if normalized is not None:
            sanitized.append(normalized)
    return sanitized
```

**一句话**：规范化 = "不管输入长什么样，输出都是标准格式"。这是防御性编程的核心。

---

## 阶段 11：会话管理器和加载系统

### 问题：怎么统一管理一个项目的所有会话？

需要一个高层接口，不关心底层是 JSONL 还是事件日志还是快照：

```python
class SessionManager:
    """高层会话管理接口"""

    @staticmethod
    def list_sessions(cwd=None) -> List[dict]:
        """列出项目的所有会话"""
        directory = get_sessions_dir(cwd or os.getcwd())
        sessions = []
        for session_file in directory.glob("*.jsonl"):
            if session_file.name.endswith(".events.jsonl"):
                continue  # 跳过事件日志文件
            storage = SessionStorage(session_file.stem, cwd)
            info = storage.get_session_info()
            if info.get("exists"):
                sessions.append(info)
        sessions.sort(key=lambda x: x.get("modified") or "", reverse=True)
        return sessions

    @staticmethod
    def get_latest_session(cwd=None) -> Optional[str]:
        """获取最近的会话 ID"""
        sessions = SessionManager.list_sessions(cwd)
        return sessions[0]["session_id"] if sessions else None

    @staticmethod
    def delete_session(session_id, cwd=None):
        """删除会话（包括所有相关文件）"""
        SessionStorage(session_id, cwd or os.getcwd()).delete_session()
```

### 问题：加载会话有三条路径

根据数据来源不同，有三种加载方式：

```python
# 路径 1：从 JSONL 文件加载（最完整）
def load_session_from_file(session_file, session_id) -> LoadedSession:
    """从 JSONL 文件读取所有记录，解析消息和元数据。"""
    messages = []
    metadata = SessionMetadata(session_id=session_id)
    leaf_uuids = set()

    for line in open(session_file):
        entry = json.loads(line)
        if entry["type"] in ("user", "assistant"):
            # 消息 → 加入消息列表
            msg = TranscriptMessage.model_validate(_normalize_message_record(entry))
            messages.append(msg)
            # 更新叶子节点
            if msg.parent_uuid:
                leaf_uuids.discard(msg.parent_uuid)
            leaf_uuids.add(msg.uuid)
        elif entry["type"] == "custom-title":
            metadata.custom_title = entry.get("custom_title")
        # ... 其他元数据类型

    return LoadedSession(
        session_id=session_id,
        messages=messages,
        metadata=metadata,
        leaf_uuids=list(leaf_uuids),
    )

# 路径 2：从事件日志加载（JSONL 不存在时的回退）
def load_session_from_events(session_id, events) -> Optional[LoadedSession]:
    """从事件日志重建会话状态。"""
    messages = []
    message_map = {}
    metadata = SessionMetadata(session_id=session_id)
    leaf_uuids = set()

    for event in events:
        if event.event_type == "message_recorded":
            msg = TranscriptMessage.model_validate(
                _normalize_message_record(event.payload["message"])
            )
            message_map[msg.uuid] = msg
            if msg.parent_uuid:
                leaf_uuids.discard(msg.parent_uuid)
            leaf_uuids.add(msg.uuid)
        elif event.event_type == "metadata_updated":
            if "custom_title" in event.payload:
                metadata.custom_title = event.payload["custom_title"]
            # ... 其他元数据

    messages = list(message_map.values())
    return LoadedSession(
        session_id=session_id,
        messages=messages,
        metadata=metadata,
        leaf_uuids=list(leaf_uuids),
    )

# 路径 3：从快照加载（最快）
# 直接读 snapshot.json，反序列化为 SessionSnapshot
```

### 三个文件的关系

```
                    写入时
                      │
                      ▼
    ┌─────────────────────────────────────┐
    │          SessionStorage             │
    │                                     │
    │  record_messages()                  │
    │    ├─→ transcript.jsonl  (追加)      │
    │    ├─→ events.jsonl      (追加)      │
    │    └─→ snapshot.json     (覆盖)      │
    └─────────────────────────────────────┘

                    读取时
                      │
                      ▼
    ┌─────────────────────────────────────┐
    │          load_messages()            │
    │                                     │
    │  1. 快照存在且最新？ → 读快照 (快)   │
    │  2. 事件日志存在？   → 从事件重建    │
    │  3. JSONL 存在？     → 从文件解析    │
    │  4. 都没有？         → 返回空        │
    └─────────────────────────────────────┘
```

**一句话**：三层回退——快照最快，事件日志次之，JSONL 最慢但最完整。

---

## 阶段 12：导出功能 — 对话变成文档

### 问题：用户想把对话导出为 Markdown 或纯文本

这是个独立的功能，不涉及存储，只涉及格式转换。

### 三种格式

```
Markdown (.md)  — 带标题、格式，适合分享
纯文本 (.txt)   — 无格式，适合粘贴
JSON (.json)    — 原始数据，适合程序处理
```

### 代码

```python
# export.py — 格式转换

def messages_to_markdown(messages: list) -> str:
    """消息列表 → Markdown"""
    lines = ["# 对话记录\n"]
    lines.append(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("---\n")

    for msg in messages:
        if msg.get("role") not in ("user", "assistant"):
            continue
        if msg.get("isMeta"):  # 跳过系统注入的消息
            continue

        # 角色标题
        if msg["role"] == "user":
            lines.append("\n## 用户\n")
        else:
            lines.append("\n## 助手\n")

        # 内容
        content = msg.get("content", "")
        if isinstance(content, str):
            lines.append(content)
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    lines.append(block["text"])
                elif block.get("type") == "tool_use":
                    lines.append(f"*[工具调用: {block['name']}]*")

    return "\n".join(lines)

def messages_to_plain_text(messages: list) -> str:
    """消息列表 → 纯文本"""
    lines = []
    for msg in messages:
        if msg.get("role") not in ("user", "assistant"):
            continue
        prefix = "Human: " if msg["role"] == "user" else "Assistant: "
        content = msg.get("content", "")
        if isinstance(content, str):
            lines.append(f"{prefix}{content}")
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    lines.append(f"{prefix}{block['text']}")
    return "\n\n".join(lines)

def export_session(messages, output_path, format="txt") -> str:
    """导出会话到文件"""
    if format == "md":
        content = messages_to_markdown(messages)
    elif format == "json":
        content = json.dumps({"messages": messages}, ensure_ascii=False, indent=2)
    else:
        content = messages_to_plain_text(messages)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path
```

### 文件名怎么生成？

```python
def generate_default_filename(messages, extension=".txt") -> str:
    """生成默认文件名：时间戳 + 第一条消息摘要"""
    timestamp = format_timestamp()                    # "2024-01-01-120000"
    first_prompt = extract_first_prompt(messages)     # "帮我写登录函数"
    sanitized = sanitize_filename(first_prompt)       # "帮我写登录函数"

    if sanitized:
        return f"{timestamp}-{sanitized}{extension}"
    return f"conversation-{timestamp}{extension}"
```

**一句话**：导出 = 消息列表 → 格式化字符串 → 写文件。

---

## 阶段 13：完整架构

### 现在的文件结构

```
session/
├── __init__.py     # 模块入口，导出所有公开接口
├── types.py        # 数据结构定义（~15 个类）
├── storage.py      # 核心读写（最复杂，~1300 行）
├── query.py        # 会话查找（按 ID、标题、最近）
├── restore.py      # 状态恢复（从 JSONL 提取消息/TODO/元数据）
├── title.py        # 标题生成（调 AI）
└── export.py       # 导出为 markdown/txt/json
```

### 三个存储文件

```
~/.codo/sessions/<project-hash>/
  <session-id>.jsonl           ← transcript：消息 + 元数据
  <session-id>.events.jsonl    ← event log：运行时事件
  <session-id>.snapshot.json   ← snapshot：最新状态缓存
```

### 数据流

```
写入流程：
用户输入 → SessionStorage.record_messages()
           ├─→ transcript.jsonl    (追加写，存对话内容)
           ├─→ events.jsonl        (追加写，存运行时事件)
           └─→ snapshot.json       (覆盖写，缓存最新状态)

读取流程（三层回退）：
SessionStorage.load_messages()
  1. 快照存在且最新？ → 读 snapshot.json     (最快，几ms)
  2. 事件日志存在？   → 从 events.jsonl 重建 (次之)
  3. JSONL 存在？     → 从 transcript 解析   (最慢但最完整)
  4. 都没有？         → 返回空列表
```

### 关键设计决策回顾

| 决策 | 选择 | 原因 |
|------|------|------|
| 存储格式 | JSONL | 追加写入简单，日志型数据 |
| 消息链 | uuid + parent_uuid | 支持分支编辑 |
| 元数据存储 | 和消息混存在同一个 JSONL | 只管理一个文件 |
| 事件日志 | 独立的 events.jsonl | 运行时事件和对话内容分离 |
| 快照机制 | 独立的 snapshot.json | 避免每次都从头遍历 |
| 文件创建 | 延迟创建（lazy） | 避免空文件 |
| 消息格式 | 规范化（normalize） | 统一不同来源的格式 |
| 加载策略 | 三层回退 | 快照最快，事件日志次之，JSONL 最完整 |
| 项目隔离 | cwd hash | 不同项目互不干扰 |
| 命名风格 | snake_case | Python 惯例 |

---

## 总结：设计思路的套路

```
1. 用一句话描述要解决的问题
2. 选数据格式（日志型→JSONL，关系型→SQLite）
3. 设计目录结构（需求 → 文件路径）
4. 写最小可用版本（10-20 行能跑）
5. 逐个加需求（每加一个，问"还有什么情况没处理"）
6. 拆模块（文件超过 300 行就开始拆）
7. 优化性能（引入缓存/快照）
```

**关键心态**：不是一开始就想好所有细节，而是先跑起来，再迭代。
