import { ipcMain, type BrowserWindow } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import net from "node:net";
import path from "node:path";
import readline from "node:readline";
import type {
  AiBridgeCommand,
  AiBridgeEvent,
  AiCancelTurnRequest,
  AiLoadSessionMessagesRequest,
  AiListSessionsRequest,
  AiDeleteSessionRequest,
  AiInteractionOption,
  AiInteractionQuestion,
  AiInteractionResponseValue,
  AiResolveInteractionRequest,
  AiAgentSummary,
  AiContentBlockType,
  AiPermissionMode,
  AiPendingInteraction,
  AiReceiptMetadataValue,
  AiRuntimeMetadata,
  AiRuntimePhase,
  AiSessionInfo,
  AiSessionMessage,
  AiSubmitMessageRequest,
  AiTodoItem,
  AiTodoStatus,
  AiToolReceipt,
  AiToolSummary
} from "../shared/aiProtocol.js";

const DEFAULT_PYTHON_EXECUTABLE = process.env.CODO_PYTHON || process.env.PYTHON || "python";
const BRIDGE_READY_TIMEOUT_MS = 10000;
const SESSION_HELPER_TIMEOUT_MS = 5000;
const BRIDGE_CONTROL_HOST = "127.0.0.1";
const DEFAULT_MANUAL_INTERACTION_ENABLED =
  process.env.CODO_WORKBENCH_MANUAL_INTERACTION_ENABLED ?? "false";

/**
 * 管理 Electron 和 Python AI bridge 之间的进程通信。
 *
 * 工作流：
 * 1. 主进程按需启动 Python bridge。
 * 2. Renderer 通过 IPC 把 submit / cancel 命令交给主进程。
 * 3. 主进程通过本地 TCP 控制端口发命令，并把 stdout 事件转发给前端。
 */
export class WorkbenchAiBridge {
  private child: ChildProcessWithoutNullStreams | null = null;
  private lineReader: readline.Interface | null = null;
  private bridgeReady = false;
  private controlPort: number | null = null;
  private readonly intentionalChildExits = new WeakSet<ChildProcessWithoutNullStreams>();

  public constructor(
    private readonly getWindow: () => BrowserWindow | null,
    private readonly bridgeScriptPath: string,
    private readonly repoRoot: string,
    private readonly pythonExecutable: string = DEFAULT_PYTHON_EXECUTABLE
  ) {}

  /**
   * 注册 AI IPC。
   */
  public registerIpc(): void {
    ipcMain.handle("ai:list-sessions", (_event, request: unknown) =>
      this.listSessions(this.parseListSessionsRequest(request))
    );
    ipcMain.handle("ai:load-session-messages", (_event, request: unknown) =>
      this.loadSessionMessages(this.parseLoadSessionMessagesRequest(request))
    );
    ipcMain.handle("ai:delete-session", (_event, request: unknown) =>
      this.deleteSession(this.parseDeleteSessionRequest(request))
    );
    ipcMain.handle("ai:submit-message", (_event, request: unknown) =>
      this.submitMessage(this.parseSubmitMessageRequest(request))
    );
    ipcMain.handle("ai:cancel-turn", (_event, request: unknown) =>
      this.cancelTurn(this.parseCancelTurnRequest(request))
    );
    ipcMain.handle("ai:resolve-interaction", (_event, request: unknown) =>
      this.resolveInteraction(this.parseResolveInteractionRequest(request))
    );
  }

  /**
   * 关闭 bridge 子进程。
   */
  public dispose(): void {
    this.lineReader?.close();
    this.lineReader = null;

    if (this.child !== null && !this.child.killed) {
      this.intentionalChildExits.add(this.child);
      this.child.kill();
    }

    this.child = null;
    this.bridgeReady = false;
    this.controlPort = null;
  }

  /**
   * 列出某个工作区的历史会话。
   */
  public async listSessions(request: AiListSessionsRequest): Promise<AiSessionInfo[]> {
    const output = await this.runSessionHelper(["list-sessions", request.workspacePath]);
    return normalizeSessionListResponse(output);
  }

  /**
   * 读取某个历史会话的可展示消息。
   */
  public async loadSessionMessages(
    request: AiLoadSessionMessagesRequest
  ): Promise<AiSessionMessage[]> {
    const output = await this.runSessionHelper([
      "load-session-messages",
      request.workspacePath,
      request.sessionId
    ]);
    return normalizeSessionMessagesResponse(output);
  }

  /**
   * 删除某个历史会话。
   */
  public async deleteSession(request: AiDeleteSessionRequest): Promise<void> {
    await this.runSessionHelper([
      "delete-session",
      request.workspacePath,
      request.sessionId
    ]);
  }

  /**
   * 提交一轮 AI 对话。
   */
  public async submitMessage(request: AiSubmitMessageRequest): Promise<void> {
    await this.ensureBridgeProcess();
    await this.writeCommand({
      type: "submit",
      request
    });
  }

  /**
   * 中断当前 AI 轮次。
   */
  public async cancelTurn(request: AiCancelTurnRequest): Promise<void> {
    const child = this.child;

    if (child === null || child.killed || child.exitCode !== null) {
      this.emitBridgeEvent({
        kind: "interrupt-ack",
        turnId: request.turnId,
        reason: "AI bridge 当前没有运行中的轮次。",
        turnCount: 0
      });
      return;
    }

    await this.writeCommand({
      type: "cancel",
      turnId: request.turnId
    });
  }

  /**
   * 把用户对交互请求的回答写入 Python bridge。
   */
  public async resolveInteraction(request: AiResolveInteractionRequest): Promise<void> {
    await this.ensureBridgeProcess();
    await this.writeCommand({
      type: "resolve-interaction",
      request
    });
  }

  private async ensureBridgeProcess(): Promise<void> {
    if (this.child !== null && !this.child.killed && this.child.exitCode === null) {
      if (this.bridgeReady && this.controlPort !== null) {
        return;
      }

      await this.waitForBridgeReady();
      return;
    }

    this.startBridgeProcess();

    await this.waitForBridgeReady();
  }

  private startBridgeProcess(): void {
    const child = spawn(this.pythonExecutable, [this.bridgeScriptPath], {
      cwd: this.repoRoot,
      env: this.createBridgeEnv(),
      stdio: ["pipe", "pipe", "pipe"]
    });

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");

    this.child = child;
    this.bridgeReady = false;
    this.controlPort = null;

    this.lineReader?.close();
    this.lineReader = readline.createInterface({
      input: child.stdout,
      crlfDelay: Infinity
    });

    this.lineReader.on("line", (line: string) => {
      this.handleBridgeLine(line);
    });

    child.stderr.on("data", (chunk: string) => {
      const message = String(chunk).trim();
      if (message.length > 0) {
        console.error(`[AI bridge] ${message}`);
      }
    });

    child.on("error", (error: Error) => {
      this.emitBridgeEvent({
        kind: "bridge-error",
        message: `AI bridge 启动失败：${error.message}`
      });
    });

    child.on("exit", (code: number | null, signal: NodeJS.Signals | null) => {
      const exitedIntentionally = this.intentionalChildExits.has(child);
      this.intentionalChildExits.delete(child);
      this.child = null;
      this.bridgeReady = false;
      this.controlPort = null;
      this.lineReader?.close();
      this.lineReader = null;

      if (code !== 0 && !exitedIntentionally) {
        this.emitBridgeEvent({
          kind: "bridge-error",
          message: `AI bridge 已退出（code=${code ?? "null"}, signal=${signal ?? "none"}）。`
        });
      }
    });
  }

  private async runSessionHelper(args: string[]): Promise<string> {
    const child = spawn(this.pythonExecutable, [this.getSessionBridgeScriptPath(), ...args], {
      cwd: this.repoRoot,
      env: this.createBridgeEnv(),
      stdio: ["ignore", "pipe", "pipe"]
    });

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });

    const exitCode = await new Promise<number | null>((resolve, reject) => {
      const timeout = setTimeout(() => {
        child.kill();
        reject(new Error("读取历史会话超时。"));
      }, SESSION_HELPER_TIMEOUT_MS);

      child.on("error", (error: Error) => {
        clearTimeout(timeout);
        reject(error);
      });
      child.on("exit", (code: number | null) => {
        clearTimeout(timeout);
        resolve(code);
      });
    });

    if (exitCode !== 0) {
      throw new Error(stderr.trim() || `历史会话读取失败（code=${exitCode ?? "null"}）。`);
    }

    return stdout;
  }

  private getSessionBridgeScriptPath(): string {
    return path.join(path.dirname(this.bridgeScriptPath), "session_bridge.py");
  }

  private async waitForBridgeReady(): Promise<void> {
    const startAt = Date.now();

    while (!this.bridgeReady || this.controlPort === null) {
      if (Date.now() - startAt > BRIDGE_READY_TIMEOUT_MS) {
        throw new Error("AI bridge 控制端口启动超时。");
      }

      await delay(25);
    }
  }

  private async writeCommand(command: AiBridgeCommand): Promise<void> {
    if (this.controlPort === null) {
      throw new Error("AI bridge 控制端口未就绪。");
    }

    const payload = `${JSON.stringify(command)}\n`;
    const port = this.controlPort;

    await new Promise<void>((resolve, reject) => {
      const socket = net.createConnection({
        host: BRIDGE_CONTROL_HOST,
        port
      });
      let settled = false;

      const settle = (error: Error | null): void => {
        if (settled) {
          return;
        }

        settled = true;
        socket.removeAllListeners();
        socket.destroy();

        if (error !== null) {
          reject(error);
          return;
        }

        resolve();
      };

      socket.once("connect", () => {
        socket.end(payload);
      });
      socket.once("error", (error: Error) => {
        settle(error);
      });
      socket.once("close", (hadError: boolean) => {
        if (!hadError) {
          settle(null);
        }
      });
    });
  }

  private createBridgeEnv(): NodeJS.ProcessEnv {
    return {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONPATH: mergePythonPath(this.repoRoot, process.env.PYTHONPATH ?? ""),
      CODO_WORKBENCH_ROOT: this.repoRoot,
      CODO_WORKBENCH_MANUAL_INTERACTION_ENABLED: DEFAULT_MANUAL_INTERACTION_ENABLED
    };
  }

  private handleBridgeLine(line: string): void {
    const parsed = this.parseBridgeEvent(line);

    if (parsed === null) {
      return;
    }

    if (parsed.kind === "bridge-ready") {
      this.bridgeReady = true;
      this.controlPort = parsed.controlPort;
    }

    this.emitBridgeEvent(parsed);
  }

  private emitBridgeEvent(event: AiBridgeEvent): void {
    const window = this.getWindow();

    if (window === null || window.isDestroyed()) {
      return;
    }

    window.webContents.send("ai:event", event);
  }

  private parseBridgeEvent(line: string): AiBridgeEvent | null {
    try {
      const value: unknown = JSON.parse(line);
      if (!isRecord(value) || typeof value.kind !== "string") {
        return null;
      }

      return normalizeBridgeEvent(value);
    } catch (error) {
      console.error(`[AI bridge] 无法解析事件：${line}`);
      if (error instanceof Error) {
        console.error(`[AI bridge] ${error.message}`);
      }
      return null;
    }
  }

  private parseSubmitMessageRequest(value: unknown): AiSubmitMessageRequest {
    if (!isRecord(value)) {
      throw new Error("AI submit 参数必须是对象。");
    }

    return {
      turnId: expectString(value.turnId, "turnId"),
      prompt: expectString(value.prompt, "prompt"),
      sessionId: expectNullableString(value.sessionId, "sessionId"),
      workspaceName: expectString(value.workspaceName, "workspaceName"),
      workspacePath: expectString(value.workspacePath, "workspacePath"),
      activeFilePath: value.activeFilePath === null ? null : expectNullableString(value.activeFilePath, "activeFilePath"),
      selectedPath: value.selectedPath === null ? null : expectNullableString(value.selectedPath, "selectedPath"),
      openFilePaths: expectStringArray(value.openFilePaths, "openFilePaths"),
      permissionMode: normalizePermissionMode(value.permissionMode)
    };
  }

  private parseListSessionsRequest(value: unknown): AiListSessionsRequest {
    if (!isRecord(value)) {
      throw new Error("AI session 参数必须是对象。");
    }

    return {
      workspacePath: expectString(value.workspacePath, "workspacePath")
    };
  }

  private parseLoadSessionMessagesRequest(value: unknown): AiLoadSessionMessagesRequest {
    if (!isRecord(value)) {
      throw new Error("AI session messages 参数必须是对象。");
    }

    return {
      workspacePath: expectString(value.workspacePath, "workspacePath"),
      sessionId: expectString(value.sessionId, "sessionId")
    };
  }

  private parseDeleteSessionRequest(value: unknown): AiDeleteSessionRequest {
    if (!isRecord(value)) {
      throw new Error("AI delete session 参数必须是对象。");
    }

    return {
      workspacePath: expectString(value.workspacePath, "workspacePath"),
      sessionId: expectString(value.sessionId, "sessionId")
    };
  }

  private parseCancelTurnRequest(value: unknown): AiCancelTurnRequest {
    if (!isRecord(value)) {
      throw new Error("AI cancel 参数必须是对象。");
    }

    return {
      turnId: expectString(value.turnId, "turnId")
    };
  }

  private parseResolveInteractionRequest(value: unknown): AiResolveInteractionRequest {
    if (!isRecord(value)) {
      throw new Error("AI interaction 参数必须是对象。");
    }

    return {
      requestId: expectString(value.requestId, "requestId"),
      data: expectInteractionResponseValue(value.data, "data")
    };
  }
}

function normalizeBridgeEvent(value: Record<string, unknown>): AiBridgeEvent {
  switch (value.kind) {
    case "bridge-ready":
      return {
        kind: "bridge-ready",
        workspacePath: expectNullableString(value.workspacePath, "workspacePath"),
        sessionId: expectNullableString(value.sessionId, "sessionId"),
        controlPort: expectNullableNumber(value.controlPort, "controlPort")
      };

    case "session-title-updated":
      return {
        kind: "session-title-updated",
        workspacePath: expectString(value.workspacePath, "workspacePath"),
        sessionId: expectString(value.sessionId, "sessionId"),
        title: expectString(value.title, "title")
      };

    case "bridge-error":
      return {
        kind: "bridge-error",
        message: expectString(value.message, "message")
      };

    case "turn-started":
      return {
        kind: "turn-started",
        turnId: expectString(value.turnId, "turnId"),
        turnCount: expectNumber(value.turnCount, "turnCount"),
        messagesCount: expectNumber(value.messagesCount, "messagesCount")
      };

    case "turn-completed":
      return {
        kind: "turn-completed",
        turnId: expectString(value.turnId, "turnId"),
        reason: expectString(value.reason, "reason"),
        turnCount: expectNumber(value.turnCount, "turnCount"),
        messageCount:
          value.messageCount === null ? null : expectNullableNumber(value.messageCount, "messageCount")
      };

    case "status-changed":
      return {
        kind: "status-changed",
        turnId: expectString(value.turnId, "turnId"),
        phase: normalizeRuntimePhase(value.phase),
        statusMessage: expectString(value.statusMessage, "statusMessage"),
        turnCount: expectNumber(value.turnCount, "turnCount"),
        checkpointId: expectNullableString(value.checkpointId, "checkpointId"),
        activeToolIds: expectStringArray(value.activeToolIds, "activeToolIds"),
        activeAgentId: expectNullableString(value.activeAgentId, "activeAgentId"),
        pendingInteraction:
          value.pendingInteraction === null
            ? null
            : normalizePendingInteraction(value.pendingInteraction),
        interruptReason: expectNullableString(value.interruptReason, "interruptReason"),
        resumeTarget: expectNullableString(value.resumeTarget, "resumeTarget"),
        metadata: normalizeRuntimeMetadata(value.metadata)
      };

    case "stream-started":
      return {
        kind: "stream-started",
        turnId: expectString(value.turnId, "turnId")
      };

    case "content-block-started":
      return {
        kind: "content-block-started",
        turnId: expectString(value.turnId, "turnId"),
        index: expectNumber(value.index, "index"),
        blockType: normalizeContentBlockType(value.blockType),
        toolUseId: expectNullableString(value.toolUseId, "toolUseId"),
        toolName: expectNullableString(value.toolName, "toolName")
      };

    case "content-block-stopped":
      return {
        kind: "content-block-stopped",
        turnId: expectString(value.turnId, "turnId"),
        index: expectNumber(value.index, "index")
      };

    case "text-delta":
      return {
        kind: "text-delta",
        turnId: expectString(value.turnId, "turnId"),
        index: value.index === null ? null : expectNullableNumber(value.index, "index"),
        delta: expectString(value.delta, "delta")
      };

    case "thinking-delta":
      return {
        kind: "thinking-delta",
        turnId: expectString(value.turnId, "turnId"),
        index: value.index === null ? null : expectNullableNumber(value.index, "index"),
        delta: expectString(value.delta, "delta")
      };

    case "tool-input-delta":
      return {
        kind: "tool-input-delta",
        turnId: expectString(value.turnId, "turnId"),
        index: expectNumber(value.index, "index"),
        toolUseId: expectNullableString(value.toolUseId, "toolUseId"),
        toolName: expectNullableString(value.toolName, "toolName"),
        partialJson: expectString(value.partialJson, "partialJson"),
        accumulatedJson: expectString(value.accumulatedJson, "accumulatedJson")
      };

    case "tool-started":
      return {
        kind: "tool-started",
        turnId: expectString(value.turnId, "turnId"),
        toolUseId: expectString(value.toolUseId, "toolUseId"),
        toolName: expectString(value.toolName, "toolName"),
        inputPreview: expectString(value.inputPreview, "inputPreview"),
        status: "running"
      };

    case "tool-progress":
      return {
        kind: "tool-progress",
        turnId: expectString(value.turnId, "turnId"),
        toolUseId: expectString(value.toolUseId, "toolUseId"),
        toolName: expectString(value.toolName, "toolName"),
        progress: expectString(value.progress, "progress")
      };

    case "tool-completed":
      return {
        kind: "tool-completed",
        turnId: expectString(value.turnId, "turnId"),
        tool: normalizeToolSummary(value.tool)
      };

    case "tool-result":
      return {
        kind: "tool-result",
        turnId: expectString(value.turnId, "turnId"),
        tool: normalizeToolSummary(value.tool),
        isError: Boolean(value.isError)
      };

    case "todo-updated":
      return {
        kind: "todo-updated",
        turnId: expectString(value.turnId, "turnId"),
        key: expectString(value.key, "key"),
        items: normalizeTodoItems(value.items),
        toolUseId: expectString(value.toolUseId, "toolUseId")
      };

    case "compact":
      return {
        kind: "compact",
        turnId: expectString(value.turnId, "turnId"),
        preTokens: expectNumber(value.preTokens, "preTokens"),
        postTokens: expectNumber(value.postTokens, "postTokens")
      };

    case "agent-started":
      return {
        kind: "agent-started",
        turnId: expectString(value.turnId, "turnId"),
        agent: normalizeAgentSummary(value.agent)
      };

    case "agent-delta":
      return {
        kind: "agent-delta",
        turnId: expectString(value.turnId, "turnId"),
        agentId: expectString(value.agentId, "agentId"),
        taskId: expectNullableString(value.taskId, "taskId"),
        contentDelta: expectString(value.contentDelta, "contentDelta"),
        thinkingDelta: expectString(value.thinkingDelta, "thinkingDelta")
      };

    case "agent-tool-started":
      return {
        kind: "agent-tool-started",
        turnId: expectString(value.turnId, "turnId"),
        agentId: expectString(value.agentId, "agentId"),
        taskId: expectNullableString(value.taskId, "taskId"),
        toolUseId: expectString(value.toolUseId, "toolUseId"),
        toolName: expectString(value.toolName, "toolName"),
        inputPreview: expectString(value.inputPreview, "inputPreview")
      };

    case "agent-tool-completed":
      return {
        kind: "agent-tool-completed",
        turnId: expectString(value.turnId, "turnId"),
        agentId: expectString(value.agentId, "agentId"),
        taskId: expectNullableString(value.taskId, "taskId"),
        tool: normalizeToolSummary(value.tool)
      };

    case "agent-completed":
      return {
        kind: "agent-completed",
        turnId: expectString(value.turnId, "turnId"),
        agentId: expectString(value.agentId, "agentId"),
        taskId: expectNullableString(value.taskId, "taskId"),
        result: expectString(value.result, "result"),
        status: "completed",
        totalTokens: expectNumber(value.totalTokens, "totalTokens")
      };

    case "agent-error":
      return {
        kind: "agent-error",
        turnId: expectString(value.turnId, "turnId"),
        agentId: expectString(value.agentId, "agentId"),
        taskId: expectNullableString(value.taskId, "taskId"),
        error: expectString(value.error, "error"),
        status: "error"
      };

    case "message-stop":
      return {
        kind: "message-stop",
        turnId: expectString(value.turnId, "turnId")
      };

    case "completed":
      return {
        kind: "completed",
        turnId: expectString(value.turnId, "turnId"),
        reason: expectString(value.reason, "reason"),
        turnCount: expectNumber(value.turnCount, "turnCount")
      };

    case "interrupt-ack":
      return {
        kind: "interrupt-ack",
        turnId: expectString(value.turnId, "turnId"),
        reason: expectString(value.reason, "reason"),
        turnCount: expectNumber(value.turnCount, "turnCount")
      };

    case "error":
      return {
        kind: "error",
        turnId: expectString(value.turnId, "turnId"),
        message: expectString(value.message, "message"),
        errorType: expectNullableString(value.errorType, "errorType"),
        category: expectNullableString(value.category, "category"),
        recoverable: Boolean(value.recoverable),
        retryAttempt: value.retryAttempt === null ? null : expectNullableNumber(value.retryAttempt, "retryAttempt"),
        maxRetries: value.maxRetries === null ? null : expectNullableNumber(value.maxRetries, "maxRetries")
      };

    default:
      throw new Error(`未知 AI bridge 事件：${String(value.kind)}`);
  }
}

function normalizeSessionListResponse(output: string): AiSessionInfo[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch (error) {
    throw new Error("历史会话响应不是合法 JSON。");
  }

  if (!isRecord(parsed) || !Array.isArray(parsed.sessions)) {
    throw new Error("历史会话响应缺少 sessions 数组。");
  }

  return parsed.sessions.map((item, index) => normalizeSessionInfo(item, index));
}

function normalizeSessionMessagesResponse(output: string): AiSessionMessage[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch (error) {
    throw new Error("历史会话消息响应不是合法 JSON。");
  }

  if (!isRecord(parsed) || !Array.isArray(parsed.messages)) {
    throw new Error("历史会话消息响应缺少 messages 数组。");
  }

  return parsed.messages.map((item, index) => normalizeSessionMessage(item, index));
}

function normalizeSessionInfo(value: unknown, index: number): AiSessionInfo {
  if (!isRecord(value)) {
    throw new Error(`历史会话 ${index + 1} 必须是对象。`);
  }

  return {
    sessionId: expectString(value.sessionId, "sessionId"),
    title: expectString(value.title, "title"),
    createdAt: expectNullableString(value.createdAt, "createdAt"),
    modifiedAt: expectNullableString(value.modifiedAt, "modifiedAt"),
    messageCount: expectNumber(value.messageCount, "messageCount"),
    firstPrompt: expectNullableString(value.firstPrompt, "firstPrompt")
  };
}

function normalizeSessionMessage(value: unknown, index: number): AiSessionMessage {
  if (!isRecord(value)) {
    throw new Error(`历史会话消息 ${index + 1} 必须是对象。`);
  }

  return {
    id: expectString(value.id, "id"),
    role: normalizeSessionMessageRole(value.role),
    content: expectString(value.content, "content"),
    createdAt: expectNullableString(value.createdAt, "createdAt")
  };
}

function normalizeSessionMessageRole(value: unknown): "user" | "assistant" {
  if (value === "user" || value === "assistant") {
    return value;
  }

  throw new Error("历史会话消息 role 必须是 user 或 assistant。");
}

function expectInteractionResponseValue(
  value: unknown,
  fieldName: string
): AiInteractionResponseValue {
  if (value === null) {
    return null;
  }

  if (typeof value === "string") {
    return value;
  }

  if (!isRecord(value)) {
    throw new Error(`${fieldName} 必须是字符串、字符串字典或 null。`);
  }

  const result: Record<string, string> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== "string") {
      throw new Error(`${fieldName}.${key} 必须是字符串。`);
    }
    result[key] = item;
  }

  return result;
}

function normalizeToolSummary(value: unknown): AiToolSummary {
  if (!isRecord(value)) {
    throw new Error("tool 对象必须是普通对象。");
  }

  return {
    toolUseId: expectString(value.toolUseId, "toolUseId"),
    name: expectString(value.name, "name"),
    status: normalizeToolStatus(value.status),
    summary: expectString(value.summary, "summary"),
    detail: expectString(value.detail, "detail"),
    receipt: value.receipt === null ? null : normalizeReceipt(value.receipt)
  };
}

function normalizeRuntimePhase(value: unknown): AiRuntimePhase {
  if (
    value === "idle" ||
    value === "submitted" ||
    value === "ready" ||
    value === "prepare_turn" ||
    value === "stream_assistant" ||
    value === "dispatch_tools" ||
    value === "execute_tools" ||
    value === "wait_interaction" ||
    value === "apply_interaction_result" ||
    value === "collect_tool_results" ||
    value === "compact" ||
    value === "stop_hooks" ||
    value === "complete" ||
    value === "error" ||
    value === "interrupted"
  ) {
    return value;
  }

  return "error";
}

function normalizeContentBlockType(value: unknown): AiContentBlockType {
  if (value === "text" || value === "thinking" || value === "tool_use") {
    return value;
  }

  return "unknown";
}

function normalizePermissionMode(value: unknown): AiPermissionMode {
  if (value === "auto" || value === "manual") {
    return value;
  }

  throw new Error("permissionMode 必须是 auto 或 manual。");
}

function normalizeRuntimeMetadata(value: unknown): AiRuntimeMetadata {
  if (!isRecord(value)) {
    throw new Error("runtime metadata 必须是对象。");
  }

  return {
    summary: expectString(value.summary, "metadata.summary"),
    reason: expectNullableString(value.reason, "metadata.reason"),
    messageCount:
      value.messageCount === null
        ? null
        : expectNullableNumber(value.messageCount, "metadata.messageCount"),
    toolCount:
      value.toolCount === null
        ? null
        : expectNullableNumber(value.toolCount, "metadata.toolCount"),
    contentBlockCount:
      value.contentBlockCount === null
        ? null
        : expectNullableNumber(value.contentBlockCount, "metadata.contentBlockCount")
  };
}

function normalizePendingInteraction(value: unknown): AiPendingInteraction {
  if (!isRecord(value)) {
    throw new Error("pendingInteraction 必须是对象或 null。");
  }

  return {
    requestId: expectString(value.requestId, "pendingInteraction.requestId"),
    kind: expectString(value.kind, "pendingInteraction.kind"),
    label: expectString(value.label, "pendingInteraction.label"),
    toolName: expectString(value.toolName, "pendingInteraction.toolName"),
    toolInfo: expectString(value.toolInfo, "pendingInteraction.toolInfo"),
    message: expectString(value.message, "pendingInteraction.message"),
    questions: normalizeInteractionQuestions(value.questions),
    options: normalizeInteractionOptions(value.options),
    initialValue: expectNullableString(value.initialValue, "pendingInteraction.initialValue"),
    payload: normalizeStringRecord(value.payload, "pendingInteraction.payload")
  };
}

function normalizeInteractionQuestions(value: unknown): AiInteractionQuestion[] {
  if (!Array.isArray(value)) {
    throw new Error("pendingInteraction.questions 必须是数组。");
  }

  return value.map((item, index) => {
    if (!isRecord(item)) {
      throw new Error(`pendingInteraction.questions[${index}] 必须是对象。`);
    }

    return {
      questionId: expectString(item.questionId, "questionId"),
      header: expectString(item.header, "header"),
      question: expectString(item.question, "question"),
      options: normalizeInteractionOptions(item.options),
      multiSelect: Boolean(item.multiSelect)
    };
  });
}

function normalizeInteractionOptions(value: unknown): AiInteractionOption[] {
  if (!Array.isArray(value)) {
    throw new Error("pendingInteraction.options 必须是数组。");
  }

  return value.map((item, index) => {
    if (!isRecord(item)) {
      throw new Error(`pendingInteraction.options[${index}] 必须是对象。`);
    }

    return {
      value: expectString(item.value, "value"),
      label: expectString(item.label, "label"),
      description: expectString(item.description, "description"),
      preview: expectString(item.preview, "preview")
    };
  });
}

function normalizeStringRecord(value: unknown, fieldName: string): Record<string, string> {
  if (!isRecord(value)) {
    throw new Error(`${fieldName} 必须是对象。`);
  }

  const result: Record<string, string> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "string") {
      result[key] = item;
    }
  }
  return result;
}

function normalizeAgentSummary(value: unknown): AiAgentSummary {
  if (!isRecord(value)) {
    throw new Error("agent 对象必须是普通对象。");
  }

  return {
    agentId: expectString(value.agentId, "agentId"),
    label: expectString(value.label, "label"),
    agentType: expectString(value.agentType, "agentType"),
    mode: expectString(value.mode, "mode"),
    background: Boolean(value.background),
    status: normalizeAgentStatus(value.status),
    taskId: expectNullableString(value.taskId, "taskId"),
    currentAction: expectString(value.currentAction, "currentAction"),
    resultPreview: expectString(value.resultPreview, "resultPreview"),
    totalTokens: expectNumber(value.totalTokens, "totalTokens")
  };
}

function normalizeAgentStatus(value: unknown): "running" | "completed" | "error" {
  if (value === "running" || value === "completed" || value === "error") {
    return value;
  }

  return "running";
}

function normalizeToolStatus(value: unknown): "running" | "completed" | "error" | "cancelled" {
  if (value === "running" || value === "completed" || value === "error" || value === "cancelled") {
    return value;
  }

  return "completed";
}

function normalizeTodoItems(value: unknown): AiTodoItem[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.map((item, index) => {
    if (!isRecord(item)) {
      throw new Error(`Todo 项 ${index + 1} 必须是对象。`);
    }

    return {
      content: expectString(item.content, "content"),
      activeForm: expectString(item.activeForm, "activeForm"),
      status: normalizeTodoStatus(item.status)
    };
  });
}

function normalizeTodoStatus(value: unknown): AiTodoStatus {
  if (value === "pending" || value === "in_progress" || value === "completed") {
    return value;
  }

  return "pending";
}

function normalizeReceipt(value: unknown): AiToolReceipt {
  if (!isRecord(value) || typeof value.kind !== "string") {
    return {
      kind: "unknown" as const,
      summary: "未知工具回执",
      body: JSON.stringify(value)
    };
  }

  switch (value.kind) {
    case "command":
      return {
        kind: "command" as const,
        summary: expectString(value.summary, "summary"),
        command: expectString(value.command, "command"),
        cwd: expectString(value.cwd ?? "", "cwd"),
        exitCode: expectNumber(value.exitCode, "exitCode"),
        stdout: expectString(value.stdout, "stdout"),
        stderr: expectString(value.stderr ?? "", "stderr")
      };
    case "diff":
      return {
        kind: "diff" as const,
        summary: expectString(value.summary, "summary"),
        path: expectString(value.path, "path"),
        diffText: expectString(value.diffText, "diffText"),
        changeId: value.changeId === null ? null : expectNullableString(value.changeId, "changeId")
      };
    case "generic":
      return {
        kind: "generic" as const,
        summary: expectString(value.summary, "summary"),
        body: expectString(value.body ?? "", "body"),
        metadata: normalizeReceiptMetadata(value.metadata)
      };
    case "agent":
      return {
        kind: "agent" as const,
        summary: expectString(value.summary, "summary"),
        agentId: expectString(value.agentId, "agentId"),
        agentType: expectString(value.agentType, "agentType"),
        mode: expectString(value.mode, "mode"),
        taskId: value.taskId === null ? null : expectNullableString(value.taskId, "taskId"),
        background: Boolean(value.background),
        status: expectString(value.status, "status"),
        resultPreview: expectString(value.resultPreview, "resultPreview"),
        totalTokens: expectNumber(value.totalTokens, "totalTokens")
      };
    default:
      return {
        kind: "unknown" as const,
        summary: expectString(value.summary ?? "工具回执", "summary"),
        body: JSON.stringify(value)
      };
  }
}

function normalizeReceiptMetadata(value: unknown): Record<string, AiReceiptMetadataValue> {
  if (!isRecord(value)) {
    return {};
  }

  const metadata: Record<string, AiReceiptMetadataValue> = {};
  for (const [key, item] of Object.entries(value)) {
    if (
      typeof item === "string" ||
      typeof item === "number" ||
      typeof item === "boolean" ||
      item === null
    ) {
      metadata[key] = item;
    }
  }
  return metadata;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function expectString(value: unknown, fieldName: string): string {
  if (typeof value !== "string") {
    throw new Error(`${fieldName} 必须是字符串。`);
  }

  return value;
}

function expectNullableString(value: unknown, fieldName: string): string | null {
  if (value === null || value === undefined) {
    return null;
  }

  return expectString(value, fieldName);
}

function expectNumber(value: unknown, fieldName: string): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    throw new Error(`${fieldName} 必须是数字。`);
  }

  return value;
}

function expectNullableNumber(value: unknown, fieldName: string): number | null {
  if (value === null || value === undefined) {
    return null;
  }

  return expectNumber(value, fieldName);
}

function expectStringArray(value: unknown, fieldName: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${fieldName} 必须是字符串数组。`);
  }

  return value.map((item, index) => {
    if (typeof item !== "string") {
      throw new Error(`${fieldName}[${index}] 必须是字符串。`);
    }

    return item;
  });
}

function mergePythonPath(repoRoot: string, existingPythonPath: string): string {
  if (existingPythonPath.trim() === "") {
    return repoRoot;
  }

  return `${repoRoot}${path.delimiter}${existingPythonPath}`;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
