import type {
  AiBridgeEvent,
  AiCancelTurnRequest,
  AiLoadSessionMessagesRequest,
  AiListSessionsRequest,
  AiDeleteSessionRequest,
  AiResolveInteractionRequest,
  AiSessionInfo,
  AiSessionMessage,
  AiSubmitMessageRequest
} from "./aiProtocol.js";

export interface WorkspaceInfo {
  name: string;
  path: string;
}

export type WorkspaceDirectoryEntryKind = "folder" | "file";

export interface WorkspaceDirectoryEntry {
  id: string;
  name: string;
  path: string;
  kind: WorkspaceDirectoryEntryKind;
}

export interface WorkspaceReadFileResult {
  name: string;
  path: string;
  content: string;
}

export interface WorkspaceWriteFileRequest {
  path: string;
  content: string;
}

export interface WorkspaceWriteFileResult {
  name: string;
  path: string;
}

export interface CodoWorkspaceApi {
  /**
   * 让用户通过系统窗口选择一个工作区目录。
   *
   * 工作流：
   * 1. Renderer 调用 preload 暴露的 API。
   * 2. Main process 打开系统目录选择器。
   * 3. 用户确认后返回工作区名称和绝对路径；用户取消时返回 null。
   */
  selectWorkspace(): Promise<WorkspaceInfo | null>;

  /**
   * 读取工作区内某个目录的第一层条目。
   *
   * 工作流：
   * 1. 入参必须是相对当前 workspace 的路径。
   * 2. Main process 校验路径不能逃出 workspace。
   * 3. 返回文件和文件夹元信息，不返回文件内容。
   */
  listDirectory(relativePath: string): Promise<WorkspaceDirectoryEntry[]>;

  /**
   * 读取工作区内单个文件内容。
   *
   * 工作流：
   * 1. 用户点击文件后调用。
   * 2. Main process 校验路径边界。
   * 3. 返回文件名、相对路径和文本内容。
   */
  readFile(relativePath: string): Promise<WorkspaceReadFileResult>;

  /**
   * 写入工作区内单个文件内容。
   *
   * 工作流：
   * 1. Renderer 传入相对 workspace 的文件路径和编辑器当前内容。
   * 2. Main process 校验路径不能逃出 workspace。
   * 3. 写入成功后返回文件名和相对路径，不返回文件内容。
   */
  writeFile(request: WorkspaceWriteFileRequest): Promise<WorkspaceWriteFileResult>;
}

export interface CodoAiApi {
  /**
   * 列出当前工作区的历史 AI 会话。
   *
   * 工作流：
   * 1. Renderer 只传已选择 workspace 的绝对路径。
   * 2. Main process 调用 workbench Python helper，复用 codo 的 SessionManager。
   * 3. 返回轻量会话元信息，不读取完整消息内容。
   */
  listSessions(request: AiListSessionsRequest): Promise<AiSessionInfo[]>;

  /**
   * 读取某个历史会话的可展示消息。
   *
   * 工作流：
   * 1. Renderer 传入 workspacePath 和 sessionId。
   * 2. Main process 调用 workbench Python helper 读取 Codo 会话文件。
   * 3. 返回 user/assistant 文本消息，不把工具结果伪装成用户消息。
   */
  loadSessionMessages(request: AiLoadSessionMessagesRequest): Promise<AiSessionMessage[]>;

  /**
   * 删除指定的 AI 会话。
   *
   * 工作流：
   * 1. Renderer 传入 workspacePath 和 sessionId。
   * 2. Main process 调用 workbench Python helper 删除会话文件。
   * 3. 删除成功后不返回任何内容。
   */
  deleteSession(request: AiDeleteSessionRequest): Promise<void>;

  /**
   * 向 Python AI bridge 提交一轮用户消息。
   *
   * 工作流：
   * 1. Renderer 生成 turnId 并带上当前 workspace 上下文。
   * 2. Main process 启动或复用 Python bridge。
   * 3. Python bridge 通过异步事件流把回复、工具和 todo 状态回传。
   */
  submitMessage(request: AiSubmitMessageRequest): Promise<void>;

  /**
   * 中断当前 AI 轮次。
   *
   * 工作流：
   * 1. Renderer 只传 turnId，不直接操作 Python 子进程。
   * 2. Main process 把中断命令写入 bridge stdin。
   * 3. Python bridge 再调用 QueryEngine 的 interrupt 流程。
   */
  cancelTurn(request: AiCancelTurnRequest): Promise<void>;

  /**
   * 回答 runtime 发起的交互请求。
   *
   * 工作流：
   * 1. Renderer 从 pendingInteraction 卡片收集用户选择或答案。
   * 2. Main process 把 requestId 和结构化答案写入 Python bridge。
   * 3. Python bridge 调用 QueryEngine.resolve_interaction 让 AI 主循环继续。
   */
  resolveInteraction(request: AiResolveInteractionRequest): Promise<void>;

  /**
   * 订阅 AI bridge 推回的事件。
   *
   * 返回值是取消订阅函数，组件卸载时必须调用，避免重复监听。
   */
  onEvent(listener: (event: AiBridgeEvent) => void): () => void;
}

export interface CodoWorkbenchApi {
  workspace: CodoWorkspaceApi;
  ai: CodoAiApi;
}
