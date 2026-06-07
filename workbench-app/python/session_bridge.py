from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence
from urllib.parse import unquote

SCRIPT_PATH = Path(__file__).resolve()
WORKBENCH_ROOT = SCRIPT_PATH.parent.parent
REPO_ROOT = WORKBENCH_ROOT.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codo.session.storage import SessionManager, SessionStorage, get_sessions_dir  # noqa: E402


@dataclass(frozen=True)
class WorkbenchSessionInfo:
    """Workbench 侧使用的历史会话元信息。"""

    session_id: str
    title: str
    created_at: str | None
    modified_at: str | None
    message_count: int
    first_prompt: str | None

    def to_dict(self) -> dict[str, Any]:
        """转成前端协议使用的 camelCase 字段。"""
        return {
            "sessionId": self.session_id,
            "title": self.title,
            "createdAt": self.created_at,
            "modifiedAt": self.modified_at,
            "messageCount": self.message_count,
            "firstPrompt": self.first_prompt,
        }


@dataclass(frozen=True)
class WorkbenchSessionMessage:
    """Workbench 右侧对话流使用的历史消息。"""

    message_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str | None

    def to_dict(self) -> dict[str, Any]:
        """转成前端协议使用的 camelCase 字段。"""
        return {
            "id": self.message_id,
            "role": self.role,
            "content": self.content,
            "createdAt": self.created_at,
        }


class SessionBridgeApp:
    """
    Workbench 会话查询入口。

    工作流：
    1. Electron 通过子进程传入 workspacePath。
    2. 这里复用 codo.session.storage 读取会话列表和消息链。
    3. stdout 只输出 JSON，方便 Electron 做严格解析。
    """

    def run(self, argv: Sequence[str]) -> int:
        """执行命令行入口。"""
        if len(argv) == 3 and argv[1] == "list-sessions":
            workspace_path = self.resolve_workspace_path(argv[2])
            sessions = self.list_sessions(workspace_path)
            self.write_json({"sessions": [session.to_dict() for session in sessions]})
            return 0

        if len(argv) == 4 and argv[1] == "load-session-messages":
            # 添加调试信息输出到 stderr
            self.write_error(f"[DEBUG] 接收到的原始参数: argv[3]={repr(argv[3])}")
            workspace_path = self.resolve_workspace_path(argv[2])
            session_id = self.resolve_session_id(argv[3])
            self.write_error(f"[DEBUG] 解析后的 session_id: {repr(session_id)}")
            messages = self.load_session_messages(workspace_path, session_id)
            self.write_json({"messages": [message.to_dict() for message in messages]})
            return 0

        if len(argv) == 4 and argv[1] == "delete-session":
            workspace_path = self.resolve_workspace_path(argv[2])
            session_id = self.resolve_session_id(argv[3])
            self.delete_session(workspace_path, session_id)
            self.write_json({"success": True})
            return 0

        self.write_error(
            "用法：session_bridge.py list-sessions <workspace_path> 或 "
            "session_bridge.py load-session-messages <workspace_path> <session_id> 或 "
            "session_bridge.py delete-session <workspace_path> <session_id>"
        )
        return 2

    def resolve_session_id(self, raw_session_id: str) -> str:
        """
        校验历史会话 ID。

        工作流：
        1. sessionId 来自前端历史列表，但仍然在 helper 边界校验。
        2. 只允许文件名安全字符，避免拼出路径分隔符。
        3. 真实文件位置仍由 SessionStorage/get_sessions_dir 决定。
        4. 尝试 URL 解码，以防前端进行了编码。
        5. 清理不可见字符（控制字符、零宽空格等）。
        """
        # 尝试 URL 解码
        try:
            decoded_session_id = unquote(raw_session_id)
        except Exception:
            decoded_session_id = raw_session_id

        # 清理不可见字符和控制字符
        # 只保留可打印字符和标准空白字符
        cleaned_session_id = "".join(
            char for char in decoded_session_id
            if char.isprintable() or char in (" ", "\t")
        )

        session_id = cleaned_session_id.strip()
        if session_id == "":
            raise ValueError("session_id 不能为空。")

        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if any(char not in allowed for char in session_id):
            # 增强错误消息，显示实际的非法字符和完整的 session_id
            illegal_chars = [char for char in session_id if char not in allowed]
            illegal_chars_repr = ", ".join([repr(char) for char in illegal_chars])
            raise ValueError(
                f"session_id 包含非法字符：{illegal_chars_repr}。"
                f"完整 session_id：{repr(session_id)}。"
                f"原始输入：{repr(raw_session_id)}"
            )

        return session_id

    def resolve_workspace_path(self, raw_path: str) -> Path:
        """
        解析 Electron 传入的 workspace 路径。

        工作流：
        1. 路径为空直接拒绝。
        2. 解析成绝对路径，避免不同 cwd 下行为不一致。
        3. 必须是目录；会话列表只允许绑定到一个明确工作区。
        """
        if raw_path.strip() == "":
            raise ValueError("workspace_path 不能为空。")

        workspace_path = Path(raw_path).expanduser().resolve()
        if not workspace_path.is_dir():
            raise ValueError(f"workspace_path 不是有效目录：{workspace_path}")

        return workspace_path

    def list_sessions(self, workspace_path: Path) -> list[WorkbenchSessionInfo]:
        """
        列出工作区历史会话。

        工作流：
        1. 使用 codo 的 SessionManager，保持 CLI 和 Workbench 的会话来源一致。
        2. 只保留 UI 必需字段，避免前端接收存储层原始字典。
        3. SessionManager 已按 modified 倒序排序，所以第一个就是最近会话。
        """
        raw_sessions = SessionManager.list_sessions(str(workspace_path))
        return [self.normalize_session_info(item, workspace_path) for item in raw_sessions]

    def load_session_messages(
        self,
        workspace_path: Path,
        session_id: str,
    ) -> list[WorkbenchSessionMessage]:
        """
        读取历史会话中适合右侧对话流展示的消息。

        工作流：
        1. 复用 SessionStorage.load_messages() 获取当前叶子链消息。
        2. user 只展示真实用户文本；工具结果类 user 消息不展示。
        3. assistant 只提取 text block，工具调用 block 留给工具摘要 UI 表达。
        """
        storage = SessionStorage(session_id=session_id, cwd=str(workspace_path))
        raw_messages = storage.load_messages()
        messages: list[WorkbenchSessionMessage] = []

        for index, raw_message in enumerate(raw_messages):
            message = self.normalize_session_message(raw_message, index)
            if message is not None:
                messages.append(message)

        return messages

    def delete_session(self, workspace_path: Path, session_id: str) -> None:
        """
        删除指定的历史会话。

        工作流：
        1. 构造会话文件路径。
        2. 删除 .jsonl 会话文件。
        3. 如果存在对应的 .meta.json 文件也一并删除。
        """
        sessions_dir = get_sessions_dir(str(workspace_path))
        session_file = sessions_dir / f"{session_id}.jsonl"
        meta_file = sessions_dir / f"{session_id}.meta.json"

        if session_file.exists():
            session_file.unlink()

        if meta_file.exists():
            meta_file.unlink()

    def normalize_session_message(
        self,
        value: dict[str, Any],
        index: int,
    ) -> WorkbenchSessionMessage | None:
        """把存储层消息转成右侧对话消息。"""
        role = str(value.get("role") or value.get("type") or "").strip().lower()
        if role not in {"user", "assistant"}:
            return None

        content = self.extract_message_content(role, value.get("content"))
        if content is None:
            return None

        message_id = str(value.get("uuid") or f"history-{index}")
        return WorkbenchSessionMessage(
            message_id=f"history-{message_id}",
            role="user" if role == "user" else "assistant",
            content=content,
            created_at=to_nullable_string(value.get("timestamp")),
        )

    def extract_message_content(self, role: str, value: Any) -> str | None:
        """
        提取历史消息文本。

        工作流：
        1. user 字符串消息去掉 Workbench 上下文包装。
        2. user 工具结果消息是 list，不当作用户发言展示。
        3. assistant list 只拼接 text block，避免把 tool_use JSON 展示成回复。
        """
        if isinstance(value, str):
            text = normalize_prompt_title(value) if role == "user" else value.strip()
            return text if text is not None and text.strip() else None

        if not isinstance(value, list):
            return None

        if role == "user":
            return None

        text_parts: list[str] = []
        for block in value:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = to_nullable_string(block.get("text"))
            if text is not None:
                text_parts.append(text)

        content = "\n\n".join(text_parts).strip()
        return content if content else None

    def normalize_session_info(
        self,
        value: dict[str, Any],
        workspace_path: Path,
    ) -> WorkbenchSessionInfo:
        """把 SessionManager 的原始字典收敛成 WorkbenchSessionInfo。"""
        session_id = str(value.get("session_id", "") or "")
        first_prompt = (
            self.extract_user_prompt(workspace_path, session_id)
            or to_nullable_string(value.get("first_prompt"))
        )
        title = build_session_title(
            session_id=session_id,
            ai_title=to_nullable_string(value.get("ai_title")),
            user_title=to_nullable_string(value.get("user_title")),
            first_prompt=first_prompt,
        )

        return WorkbenchSessionInfo(
            session_id=session_id,
            title=title,
            created_at=to_nullable_string(value.get("created")),
            modified_at=to_nullable_string(value.get("modified")),
            message_count=to_int(value.get("message_count")),
            first_prompt=first_prompt,
        )

    def extract_user_prompt(self, workspace_path: Path, session_id: str) -> str | None:
        """
        从会话文件提取更适合 UI 展示的用户问题。

        工作流：
        1. 只扫描 JSONL 前几条记录，避免加载完整会话。
        2. 优先读取第一条 user content，其次读取 last-prompt。
        3. Workbench 包装过的 prompt 会抽出 `【用户请求】` 后面的真实问题。
        """
        if not session_id:
            return None

        session_file = get_sessions_dir(str(workspace_path)) / f"{session_id}.jsonl"
        if not session_file.exists():
            return None

        try:
            with session_file.open("r", encoding="utf-8") as file:
                for index, line in enumerate(file):
                    if index >= 12:
                        break
                    prompt = self.parse_prompt_record(line)
                    if prompt is not None:
                        return prompt
        except OSError:
            return None

        return None

    def parse_prompt_record(self, line: str) -> str | None:
        """解析 JSONL 中可能包含用户 prompt 的记录。"""
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None

        if not isinstance(record, dict):
            return None

        record_type = str(record.get("type", "") or "")
        if record_type == "user":
            return normalize_prompt_title(record.get("content"))

        if record_type == "last-prompt":
            return normalize_prompt_title(record.get("last_prompt"))

        return None

    def write_json(self, payload: dict[str, Any]) -> None:
        """输出一行 JSON 给 Electron。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def write_error(self, message: str) -> None:
        """错误信息只写 stderr，避免污染 stdout JSON。"""
        sys.stderr.write(message + "\n")
        sys.stderr.flush()

def build_session_title(
    *,
    session_id: str,
    ai_title: str | None,
    user_title: str | None,
    first_prompt: str | None,
) -> str:
    """按优先级生成历史会话标题。"""
    for title in (user_title, ai_title, first_prompt):
        if title is not None and title.strip():
            return title.strip()

    if session_id:
        return f"会话 {session_id[:8]}"

    return "未命名会话"


def normalize_prompt_title(value: Any) -> str | None:
    """
    提取适合历史列表展示的 prompt 摘要。

    工作流：
    1. Workbench prompt 如果包含 `【用户请求】`，只取该段后面的真实问题。
    2. 多行文本取第一段非空内容。
    3. 标题最长保留 64 个字符，避免撑开历史菜单。
    """
    text = to_nullable_string(value)
    if text is None:
        return None

    marker = "【用户请求】"
    if marker in text:
        text = text.split(marker, 1)[1].strip()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return truncate_title(stripped, 64)

    return None


def truncate_title(value: str, max_length: int) -> str:
    """把历史会话标题截断到固定长度。"""
    if len(value) <= max_length:
        return value
    return f"{value[:max_length]}…"


def to_nullable_string(value: Any) -> str | None:
    """把存储层值转成可空字符串。"""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def to_int(value: Any) -> int:
    """把存储层数字字段转成整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """脚本入口。"""
    app = SessionBridgeApp()
    try:
        return app.run(sys.argv if argv is None else argv)
    except Exception as exc:  # noqa: BLE001
        app.write_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
