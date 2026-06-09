// @ts-nocheck
"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));

// electron/main.ts
var import_electron2 = require("electron");
var import_promises = require("fs/promises");
var import_node_path2 = __toESM(require("path"), 1);

// electron/aiBridge.ts
var import_electron = require("electron");
var import_node_child_process = require("child_process");
var import_node_net = __toESM(require("net"), 1);
var import_node_path = __toESM(require("path"), 1);
var import_node_readline = __toESM(require("readline"), 1);
var DEFAULT_PYTHON_EXECUTABLE = process.env.CODO_PYTHON || process.env.PYTHON || "python";
var BRIDGE_READY_TIMEOUT_MS = 1e4;
var SESSION_HELPER_TIMEOUT_MS = 3e4;
var BRIDGE_CONTROL_HOST = "127.0.0.1";
var DEFAULT_MANUAL_INTERACTION_ENABLED = process.env.CODO_WORKBENCH_MANUAL_INTERACTION_ENABLED ?? "false";
var WorkbenchAiBridge = class {
  constructor(getWindow, bridgeScriptPath, repoRoot, pythonExecutable = DEFAULT_PYTHON_EXECUTABLE) {
    this.getWindow = getWindow;
    this.bridgeScriptPath = bridgeScriptPath;
    this.repoRoot = repoRoot;
    this.pythonExecutable = pythonExecutable;
  }
  getWindow;
  bridgeScriptPath;
  repoRoot;
  pythonExecutable;
  child = null;
  lineReader = null;
  bridgeReady = false;
  controlPort = null;
  intentionalChildExits = /* @__PURE__ */ new WeakSet();
  /**
   * 注册 AI IPC。
   */
  registerIpc() {
    import_electron.ipcMain.handle(
      "ai:list-sessions",
      (_event, request) => this.listSessions(this.parseListSessionsRequest(request))
    );
    import_electron.ipcMain.handle(
      "ai:load-session-messages",
      (_event, request) => this.loadSessionMessages(this.parseLoadSessionMessagesRequest(request))
    );
    import_electron.ipcMain.handle(
      "ai:delete-session",
      (_event, request) => this.deleteSession(this.parseDeleteSessionRequest(request))
    );
    import_electron.ipcMain.handle(
      "ai:submit-message",
      (_event, request) => this.submitMessage(this.parseSubmitMessageRequest(request))
    );
    import_electron.ipcMain.handle(
      "ai:cancel-turn",
      (_event, request) => this.cancelTurn(this.parseCancelTurnRequest(request))
    );
    import_electron.ipcMain.handle(
      "ai:resolve-interaction",
      (_event, request) => this.resolveInteraction(this.parseResolveInteractionRequest(request))
    );
  }
  /**
   * 关闭 bridge 子进程。
   */
  dispose() {
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
  async listSessions(request) {
    const output = await this.runSessionHelper(["list-sessions", request.workspacePath]);
    return normalizeSessionListResponse(output);
  }
  /**
   * 读取某个历史会话的可展示消息。
   */
  async loadSessionMessages(request) {
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
  async deleteSession(request) {
    await this.runSessionHelper([
      "delete-session",
      request.workspacePath,
      request.sessionId
    ]);
  }
  /**
   * 提交一轮 AI 对话。
   */
  async submitMessage(request) {
    await this.ensureBridgeProcess();
    await this.writeCommand({
      type: "submit",
      request
    });
  }
  /**
   * 中断当前 AI 轮次。
   */
  async cancelTurn(request) {
    const child = this.child;
    if (child === null || child.killed || child.exitCode !== null) {
      this.emitBridgeEvent({
        kind: "interrupt-ack",
        turnId: request.turnId,
        reason: "AI bridge \u5F53\u524D\u6CA1\u6709\u8FD0\u884C\u4E2D\u7684\u8F6E\u6B21\u3002",
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
  async resolveInteraction(request) {
    await this.ensureBridgeProcess();
    await this.writeCommand({
      type: "resolve-interaction",
      request
    });
  }
  async ensureBridgeProcess() {
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
  startBridgeProcess() {
    const child = (0, import_node_child_process.spawn)(this.pythonExecutable, [this.bridgeScriptPath], {
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
    this.lineReader = import_node_readline.default.createInterface({
      input: child.stdout,
      crlfDelay: Infinity
    });
    this.lineReader.on("line", (line) => {
      this.handleBridgeLine(line);
    });
    child.stderr.on("data", (chunk) => {
      const message = String(chunk).trim();
      if (message.length > 0) {
        console.error(`[AI bridge] ${message}`);
      }
    });
    child.on("error", (error) => {
      this.emitBridgeEvent({
        kind: "bridge-error",
        message: `AI bridge \u542F\u52A8\u5931\u8D25\uFF1A${error.message}`
      });
    });
    child.on("exit", (code, signal) => {
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
          message: `AI bridge \u5DF2\u9000\u51FA\uFF08code=${code ?? "null"}, signal=${signal ?? "none"}\uFF09\u3002`
        });
      }
    });
  }
  async runSessionHelper(args) {
    const child = (0, import_node_child_process.spawn)(this.pythonExecutable, [this.getSessionBridgeScriptPath(), ...args], {
      cwd: this.repoRoot,
      env: this.createBridgeEnv(),
      stdio: ["ignore", "pipe", "pipe"]
    });
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    const exitCode = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        child.kill();
        reject(new Error("\u8BFB\u53D6\u5386\u53F2\u4F1A\u8BDD\u8D85\u65F6\u3002"));
      }, SESSION_HELPER_TIMEOUT_MS);
      child.on("error", (error) => {
        clearTimeout(timeout);
        reject(error);
      });
      child.on("exit", (code) => {
        clearTimeout(timeout);
        resolve(code);
      });
    });
    if (exitCode !== 0) {
      throw new Error(stderr.trim() || `\u5386\u53F2\u4F1A\u8BDD\u8BFB\u53D6\u5931\u8D25\uFF08code=${exitCode ?? "null"}\uFF09\u3002`);
    }
    return stdout;
  }
  getSessionBridgeScriptPath() {
    return import_node_path.default.join(import_node_path.default.dirname(this.bridgeScriptPath), "session_bridge.py");
  }
  async waitForBridgeReady() {
    const startAt = Date.now();
    while (!this.bridgeReady || this.controlPort === null) {
      if (Date.now() - startAt > BRIDGE_READY_TIMEOUT_MS) {
        throw new Error("AI bridge \u63A7\u5236\u7AEF\u53E3\u542F\u52A8\u8D85\u65F6\u3002");
      }
      await delay(25);
    }
  }
  async writeCommand(command) {
    if (this.controlPort === null) {
      throw new Error("AI bridge \u63A7\u5236\u7AEF\u53E3\u672A\u5C31\u7EEA\u3002");
    }
    const payload = `${JSON.stringify(command)}
`;
    const port = this.controlPort;
    await new Promise((resolve, reject) => {
      const socket = import_node_net.default.createConnection({
        host: BRIDGE_CONTROL_HOST,
        port
      });
      let settled = false;
      const settle = (error) => {
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
      socket.once("error", (error) => {
        settle(error);
      });
      socket.once("close", (hadError) => {
        if (!hadError) {
          settle(null);
        }
      });
    });
  }
  createBridgeEnv() {
    return {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONPATH: mergePythonPath(this.repoRoot, process.env.PYTHONPATH ?? ""),
      CODO_WORKBENCH_ROOT: this.repoRoot,
      CODO_WORKBENCH_MANUAL_INTERACTION_ENABLED: DEFAULT_MANUAL_INTERACTION_ENABLED
    };
  }
  handleBridgeLine(line) {
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
  emitBridgeEvent(event) {
    const window = this.getWindow();
    if (window === null || window.isDestroyed()) {
      return;
    }
    window.webContents.send("ai:event", event);
  }
  parseBridgeEvent(line) {
    try {
      const value = JSON.parse(line);
      if (!isRecord(value) || typeof value.kind !== "string") {
        return null;
      }
      return normalizeBridgeEvent(value);
    } catch (error) {
      console.error(`[AI bridge] \u65E0\u6CD5\u89E3\u6790\u4E8B\u4EF6\uFF1A${line}`);
      if (error instanceof Error) {
        console.error(`[AI bridge] ${error.message}`);
      }
      return null;
    }
  }
  parseSubmitMessageRequest(value) {
    if (!isRecord(value)) {
      throw new Error("AI submit \u53C2\u6570\u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
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
      permissionMode: normalizePermissionMode(value.permissionMode),
      images: normalizeImageAttachments(value.images)
    };
  }
  parseListSessionsRequest(value) {
    if (!isRecord(value)) {
      throw new Error("AI session \u53C2\u6570\u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
    }
    return {
      workspacePath: expectString(value.workspacePath, "workspacePath")
    };
  }
  parseLoadSessionMessagesRequest(value) {
    if (!isRecord(value)) {
      throw new Error("AI session messages \u53C2\u6570\u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
    }
    return {
      workspacePath: expectString(value.workspacePath, "workspacePath"),
      sessionId: expectString(value.sessionId, "sessionId")
    };
  }
  parseDeleteSessionRequest(value) {
    if (!isRecord(value)) {
      throw new Error("AI delete session \u53C2\u6570\u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
    }
    return {
      workspacePath: expectString(value.workspacePath, "workspacePath"),
      sessionId: expectString(value.sessionId, "sessionId")
    };
  }
  parseCancelTurnRequest(value) {
    if (!isRecord(value)) {
      throw new Error("AI cancel \u53C2\u6570\u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
    }
    return {
      turnId: expectString(value.turnId, "turnId")
    };
  }
  parseResolveInteractionRequest(value) {
    if (!isRecord(value)) {
      throw new Error("AI interaction \u53C2\u6570\u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
    }
    return {
      requestId: expectString(value.requestId, "requestId"),
      data: expectInteractionResponseValue(value.data, "data")
    };
  }
};
function normalizeBridgeEvent(value) {
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
        messageCount: value.messageCount === null ? null : expectNullableNumber(value.messageCount, "messageCount")
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
        pendingInteraction: value.pendingInteraction === null ? null : normalizePendingInteraction(value.pendingInteraction),
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
      throw new Error(`\u672A\u77E5 AI bridge \u4E8B\u4EF6\uFF1A${String(value.kind)}`);
  }
}
function normalizeSessionListResponse(output) {
  let parsed;
  try {
    parsed = JSON.parse(output);
  } catch (error) {
    throw new Error("\u5386\u53F2\u4F1A\u8BDD\u54CD\u5E94\u4E0D\u662F\u5408\u6CD5 JSON\u3002");
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.sessions)) {
    throw new Error("\u5386\u53F2\u4F1A\u8BDD\u54CD\u5E94\u7F3A\u5C11 sessions \u6570\u7EC4\u3002");
  }
  return parsed.sessions.map((item, index) => normalizeSessionInfo(item, index));
}
function normalizeSessionMessagesResponse(output) {
  let parsed;
  try {
    parsed = JSON.parse(output);
  } catch (error) {
    throw new Error("\u5386\u53F2\u4F1A\u8BDD\u6D88\u606F\u54CD\u5E94\u4E0D\u662F\u5408\u6CD5 JSON\u3002");
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.messages)) {
    throw new Error("\u5386\u53F2\u4F1A\u8BDD\u6D88\u606F\u54CD\u5E94\u7F3A\u5C11 messages \u6570\u7EC4\u3002");
  }
  return parsed.messages.map((item, index) => normalizeSessionMessage(item, index));
}
function normalizeSessionInfo(value, index) {
  if (!isRecord(value)) {
    throw new Error(`\u5386\u53F2\u4F1A\u8BDD ${index + 1} \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
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
function normalizeSessionMessage(value, index) {
  if (!isRecord(value)) {
    throw new Error(`\u5386\u53F2\u4F1A\u8BDD\u6D88\u606F ${index + 1} \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
  }
  return {
    id: expectString(value.id, "id"),
    role: normalizeSessionMessageRole(value.role),
    content: expectString(value.content, "content"),
    images: normalizeSessionMessageImages(value.images),
    createdAt: expectNullableString(value.createdAt, "createdAt")
  };
}
function normalizeSessionMessageImages(value) {
  if (value === void 0) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new Error("\u5386\u53F2\u4F1A\u8BDD\u6D88\u606F images \u5FC5\u987B\u662F\u6570\u7EC4\u3002");
  }
  return value.map((item, index) => normalizeSessionMessageImage(item, index));
}
function normalizeSessionMessageImage(value, index) {
  if (!isRecord(value)) {
    throw new Error(`\u5386\u53F2\u4F1A\u8BDD\u56FE\u7247 ${index + 1} \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
  }
  return {
    base64: expectString(value.base64, "base64"),
    mimeType: expectString(value.mimeType, "mimeType")
  };
}
function normalizeSessionMessageRole(value) {
  if (value === "user" || value === "assistant") {
    return value;
  }
  throw new Error("\u5386\u53F2\u4F1A\u8BDD\u6D88\u606F role \u5FC5\u987B\u662F user \u6216 assistant\u3002");
}
function expectInteractionResponseValue(value, fieldName) {
  if (value === null) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  if (!isRecord(value)) {
    throw new Error(`${fieldName} \u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u3001\u5B57\u7B26\u4E32\u5B57\u5178\u6216 null\u3002`);
  }
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== "string") {
      throw new Error(`${fieldName}.${key} \u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u3002`);
    }
    result[key] = item;
  }
  return result;
}
function normalizeImageAttachments(value) {
  if (value === void 0 || value === null) {
    return void 0;
  }
  if (!Array.isArray(value)) {
    throw new Error("images \u5FC5\u987B\u662F\u56FE\u7247\u6570\u7EC4\u3002");
  }
  return value.map((item, index) => {
    if (!isRecord(item)) {
      throw new Error(`images[${index}] \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
    }
    const base64 = expectString(item.base64, `images[${index}].base64`).trim();
    const mimeType = expectString(item.mimeType, `images[${index}].mimeType`).trim();
    if (base64.length === 0) {
      throw new Error(`images[${index}].base64 \u4E0D\u80FD\u4E3A\u7A7A\u3002`);
    }
    if (!isSupportedImageMimeType(mimeType)) {
      throw new Error(`images[${index}].mimeType \u4E0D\u652F\u6301\uFF1A${mimeType}`);
    }
    return {
      base64,
      mimeType
    };
  });
}
function isSupportedImageMimeType(value) {
  return value === "image/png" || value === "image/jpeg" || value === "image/gif" || value === "image/webp";
}
function normalizeToolSummary(value) {
  if (!isRecord(value)) {
    throw new Error("tool \u5BF9\u8C61\u5FC5\u987B\u662F\u666E\u901A\u5BF9\u8C61\u3002");
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
function normalizeRuntimePhase(value) {
  if (value === "idle" || value === "submitted" || value === "ready" || value === "prepare_turn" || value === "stream_assistant" || value === "dispatch_tools" || value === "execute_tools" || value === "wait_interaction" || value === "apply_interaction_result" || value === "collect_tool_results" || value === "compact" || value === "stop_hooks" || value === "complete" || value === "error" || value === "interrupted") {
    return value;
  }
  return "error";
}
function normalizeContentBlockType(value) {
  if (value === "text" || value === "thinking" || value === "tool_use") {
    return value;
  }
  return "unknown";
}
function normalizePermissionMode(value) {
  if (value === "auto" || value === "manual") {
    return value;
  }
  throw new Error("permissionMode \u5FC5\u987B\u662F auto \u6216 manual\u3002");
}
function normalizeRuntimeMetadata(value) {
  if (!isRecord(value)) {
    throw new Error("runtime metadata \u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
  }
  return {
    summary: expectString(value.summary, "metadata.summary"),
    reason: expectNullableString(value.reason, "metadata.reason"),
    messageCount: value.messageCount === null ? null : expectNullableNumber(value.messageCount, "metadata.messageCount"),
    toolCount: value.toolCount === null ? null : expectNullableNumber(value.toolCount, "metadata.toolCount"),
    contentBlockCount: value.contentBlockCount === null ? null : expectNullableNumber(value.contentBlockCount, "metadata.contentBlockCount")
  };
}
function normalizePendingInteraction(value) {
  if (!isRecord(value)) {
    throw new Error("pendingInteraction \u5FC5\u987B\u662F\u5BF9\u8C61\u6216 null\u3002");
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
function normalizeInteractionQuestions(value) {
  if (!Array.isArray(value)) {
    throw new Error("pendingInteraction.questions \u5FC5\u987B\u662F\u6570\u7EC4\u3002");
  }
  return value.map((item, index) => {
    if (!isRecord(item)) {
      throw new Error(`pendingInteraction.questions[${index}] \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
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
function normalizeInteractionOptions(value) {
  if (!Array.isArray(value)) {
    throw new Error("pendingInteraction.options \u5FC5\u987B\u662F\u6570\u7EC4\u3002");
  }
  return value.map((item, index) => {
    if (!isRecord(item)) {
      throw new Error(`pendingInteraction.options[${index}] \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
    }
    return {
      value: expectString(item.value, "value"),
      label: expectString(item.label, "label"),
      description: expectString(item.description, "description"),
      preview: expectString(item.preview, "preview")
    };
  });
}
function normalizeStringRecord(value, fieldName) {
  if (!isRecord(value)) {
    throw new Error(`${fieldName} \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
  }
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "string") {
      result[key] = item;
    }
  }
  return result;
}
function normalizeAgentSummary(value) {
  if (!isRecord(value)) {
    throw new Error("agent \u5BF9\u8C61\u5FC5\u987B\u662F\u666E\u901A\u5BF9\u8C61\u3002");
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
function normalizeAgentStatus(value) {
  if (value === "running" || value === "completed" || value === "error") {
    return value;
  }
  return "running";
}
function normalizeToolStatus(value) {
  if (value === "running" || value === "completed" || value === "error" || value === "cancelled") {
    return value;
  }
  return "completed";
}
function normalizeTodoItems(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item, index) => {
    if (!isRecord(item)) {
      throw new Error(`Todo \u9879 ${index + 1} \u5FC5\u987B\u662F\u5BF9\u8C61\u3002`);
    }
    return {
      content: expectString(item.content, "content"),
      activeForm: expectString(item.activeForm, "activeForm"),
      status: normalizeTodoStatus(item.status)
    };
  });
}
function normalizeTodoStatus(value) {
  if (value === "pending" || value === "in_progress" || value === "completed") {
    return value;
  }
  return "pending";
}
function normalizeReceipt(value) {
  if (!isRecord(value) || typeof value.kind !== "string") {
    return {
      kind: "unknown",
      summary: "\u672A\u77E5\u5DE5\u5177\u56DE\u6267",
      body: JSON.stringify(value)
    };
  }
  switch (value.kind) {
    case "command":
      return {
        kind: "command",
        summary: expectString(value.summary, "summary"),
        command: expectString(value.command, "command"),
        cwd: expectString(value.cwd ?? "", "cwd"),
        exitCode: expectNumber(value.exitCode, "exitCode"),
        stdout: expectString(value.stdout, "stdout"),
        stderr: expectString(value.stderr ?? "", "stderr")
      };
    case "diff":
      return {
        kind: "diff",
        summary: expectString(value.summary, "summary"),
        path: expectString(value.path, "path"),
        diffText: expectString(value.diffText, "diffText"),
        changeId: value.changeId === null ? null : expectNullableString(value.changeId, "changeId")
      };
    case "generic":
      return {
        kind: "generic",
        summary: expectString(value.summary, "summary"),
        body: expectString(value.body ?? "", "body"),
        metadata: normalizeReceiptMetadata(value.metadata)
      };
    case "agent":
      return {
        kind: "agent",
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
        kind: "unknown",
        summary: expectString(value.summary ?? "\u5DE5\u5177\u56DE\u6267", "summary"),
        body: JSON.stringify(value)
      };
  }
}
function normalizeReceiptMetadata(value) {
  if (!isRecord(value)) {
    return {};
  }
  const metadata = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean" || item === null) {
      metadata[key] = item;
    }
  }
  return metadata;
}
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function expectString(value, fieldName) {
  if (typeof value !== "string") {
    throw new Error(`${fieldName} \u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u3002`);
  }
  return value;
}
function expectNullableString(value, fieldName) {
  if (value === null || value === void 0) {
    return null;
  }
  return expectString(value, fieldName);
}
function expectNumber(value, fieldName) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    throw new Error(`${fieldName} \u5FC5\u987B\u662F\u6570\u5B57\u3002`);
  }
  return value;
}
function expectNullableNumber(value, fieldName) {
  if (value === null || value === void 0) {
    return null;
  }
  return expectNumber(value, fieldName);
}
function expectStringArray(value, fieldName) {
  if (!Array.isArray(value)) {
    throw new Error(`${fieldName} \u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u6570\u7EC4\u3002`);
  }
  return value.map((item, index) => {
    if (typeof item !== "string") {
      throw new Error(`${fieldName}[${index}] \u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u3002`);
    }
    return item;
  });
}
function mergePythonPath(repoRoot, existingPythonPath) {
  if (existingPythonPath.trim() === "") {
    return repoRoot;
  }
  return `${repoRoot}${import_node_path.default.delimiter}${existingPythonPath}`;
}
function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

// electron/main.ts
var DEV_SERVER_URL = "http://127.0.0.1:5173";
var DEFAULT_WINDOW_WIDTH = 1440;
var DEFAULT_WINDOW_HEIGHT = 900;
var MIN_WINDOW_WIDTH = 960;
var MIN_WINDOW_HEIGHT = 640;
var WORKBENCH_BACKGROUND = "#1e1e1e";
var BYTES_PER_KIBIBYTE = 1024;
var BYTES_PER_MEBIBYTE = BYTES_PER_KIBIBYTE * 1024;
var MAX_READ_FILE_BYTES = 5 * BYTES_PER_MEBIBYTE;
var WorkspacePathGuard = class {
  workspaceRoot = null;
  setWorkspaceRoot(selectedPath) {
    this.workspaceRoot = import_node_path2.default.resolve(selectedPath);
  }
  getWorkspaceRoot() {
    return this.workspaceRoot;
  }
  resolveInsideWorkspace(relativePath) {
    if (this.workspaceRoot === null) {
      throw new Error("\u8BF7\u5148\u9009\u62E9\u5DE5\u4F5C\u533A\u3002");
    }
    const normalizedRelativePath = relativePath.trim() === "" ? "." : relativePath;
    const absolutePath = import_node_path2.default.resolve(this.workspaceRoot, normalizedRelativePath);
    const rootWithSeparator = this.workspaceRoot.endsWith(import_node_path2.default.sep) ? this.workspaceRoot : `${this.workspaceRoot}${import_node_path2.default.sep}`;
    if (absolutePath !== this.workspaceRoot && !absolutePath.startsWith(rootWithSeparator)) {
      throw new Error("\u8DEF\u5F84\u8D85\u51FA\u5F53\u524D\u5DE5\u4F5C\u533A\u3002");
    }
    return absolutePath;
  }
};
var WorkspaceFileService = class {
  constructor(pathGuard) {
    this.pathGuard = pathGuard;
  }
  pathGuard;
  async selectWorkspace(ownerWindow) {
    const result = await import_electron2.dialog.showOpenDialog(ownerWindow, {
      title: "\u9009\u62E9\u5DE5\u4F5C\u533A",
      properties: ["openDirectory"]
    });
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }
    const selectedWorkspacePath = result.filePaths[0];
    this.pathGuard.setWorkspaceRoot(selectedWorkspacePath);
    return {
      name: import_node_path2.default.basename(selectedWorkspacePath),
      path: import_node_path2.default.resolve(selectedWorkspacePath)
    };
  }
  async listDirectory(relativePath) {
    const directoryPath = this.pathGuard.resolveInsideWorkspace(relativePath);
    const workspaceRoot = this.pathGuard.getWorkspaceRoot();
    if (workspaceRoot === null) {
      throw new Error("\u8BF7\u5148\u9009\u62E9\u5DE5\u4F5C\u533A\u3002");
    }
    let entries;
    try {
      entries = await (0, import_promises.readdir)(directoryPath, { withFileTypes: true });
    } catch (error) {
      throw createDirectoryReadError(error, relativePath);
    }
    return entries.map((entry) => {
      const childAbsolutePath = import_node_path2.default.join(directoryPath, entry.name);
      const childRelativePath = import_node_path2.default.relative(workspaceRoot, childAbsolutePath);
      return {
        id: childRelativePath,
        name: entry.name,
        path: childRelativePath,
        kind: entry.isDirectory() ? "folder" : "file",
        sortName: entry.name.toLowerCase()
      };
    }).sort(compareWorkspaceDirectoryEntries).map(removeSortKey);
  }
  async readWorkspaceFile(relativePath) {
    const filePath = this.pathGuard.resolveInsideWorkspace(relativePath);
    await this.assertReadableWorkspaceFile(filePath);
    const content = await (0, import_promises.readFile)(filePath, "utf8");
    return {
      name: import_node_path2.default.basename(filePath),
      path: relativePath,
      content
    };
  }
  /**
   * 校验文件是否适合直接读入编辑器。
   *
   * 工作流：
   * 1. 先读取文件元信息，不读取文件内容。
   * 2. 如果目标不是普通文件，直接拒绝。
   * 3. 如果文件超过当前限制，返回明确错误，避免 Monaco 和 renderer 被大文件拖慢。
   */
  async assertReadableWorkspaceFile(filePath) {
    const fileStats = await (0, import_promises.stat)(filePath);
    if (!fileStats.isFile()) {
      throw new Error("\u5F53\u524D\u8DEF\u5F84\u4E0D\u662F\u53EF\u6253\u5F00\u7684\u6587\u4EF6\u3002");
    }
    if (fileStats.size > MAX_READ_FILE_BYTES) {
      throw new Error(
        `\u6587\u4EF6\u8FC7\u5927\uFF1A${formatFileSize(fileStats.size)}\uFF0C\u5F53\u524D\u9650\u5236 ${formatFileSize(
          MAX_READ_FILE_BYTES
        )}\u3002`
      );
    }
  }
  /**
   * 写入工作区内单个文件。
   *
   * 工作流：
   * 1. IPC 控制器传入已经做过结构校验的写入请求。
   * 2. 路径守卫把相对路径解析为 workspace 内绝对路径。
   * 3. 使用 UTF-8 写入文本内容，写完后返回轻量结果。
   */
  async writeWorkspaceFile(request) {
    const filePath = this.pathGuard.resolveInsideWorkspace(request.path);
    await (0, import_promises.writeFile)(filePath, request.content, "utf8");
    return {
      name: import_node_path2.default.basename(filePath),
      path: request.path
    };
  }
};
var WorkbenchWindow = class {
  window = null;
  create() {
    this.window = new import_electron2.BrowserWindow({
      width: DEFAULT_WINDOW_WIDTH,
      height: DEFAULT_WINDOW_HEIGHT,
      minWidth: MIN_WINDOW_WIDTH,
      minHeight: MIN_WINDOW_HEIGHT,
      backgroundColor: WORKBENCH_BACKGROUND,
      show: false,
      webPreferences: {
        preload: import_node_path2.default.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true
      }
    });
    this.window.once("ready-to-show", () => {
      this.window?.show();
    });
    if (import_electron2.app.isPackaged) {
      void this.window.loadFile(import_node_path2.default.join(__dirname, "..", "dist", "index.html"));
    } else {
      void this.window.loadURL(DEV_SERVER_URL);
    }
    return this.window;
  }
  getRequiredWindow() {
    if (this.window === null) {
      throw new Error("\u4E3B\u7A97\u53E3\u5C1A\u672A\u521B\u5EFA\u3002");
    }
    return this.window;
  }
  getWindow() {
    return this.window;
  }
};
var WorkbenchIpcController = class {
  constructor(workbenchWindow, fileService) {
    this.workbenchWindow = workbenchWindow;
    this.fileService = fileService;
  }
  workbenchWindow;
  fileService;
  register() {
    import_electron2.ipcMain.handle("workspace:select", () => this.selectWorkspace());
    import_electron2.ipcMain.handle(
      "fs:list-directory",
      (_event, relativePath) => this.listDirectory(relativePath)
    );
    import_electron2.ipcMain.handle(
      "fs:read-file",
      (_event, relativePath) => this.readFile(relativePath)
    );
    import_electron2.ipcMain.handle(
      "fs:write-file",
      (_event, request) => this.writeFile(request)
    );
  }
  async selectWorkspace() {
    return this.fileService.selectWorkspace(this.workbenchWindow.getRequiredWindow());
  }
  async listDirectory(relativePath) {
    return this.fileService.listDirectory(this.parseRelativePath(relativePath));
  }
  async readFile(relativePath) {
    return this.fileService.readWorkspaceFile(this.parseRelativePath(relativePath));
  }
  async writeFile(request) {
    return this.fileService.writeWorkspaceFile(this.parseWriteFileRequest(request));
  }
  parseRelativePath(value) {
    if (typeof value !== "string") {
      throw new Error("\u8DEF\u5F84\u53C2\u6570\u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u3002");
    }
    return value;
  }
  /**
   * 解析写文件 IPC 入参。
   *
   * 工作流：
   * 1. IPC 边界传入的数据类型不可信，先确认它是普通对象。
   * 2. 明确取出 path/content 两个字段并校验字符串类型。
   * 3. 返回共享类型 WorkspaceWriteFileRequest，交给文件服务执行。
   */
  parseWriteFileRequest(value) {
    if (!isRecord2(value)) {
      throw new Error("\u5199\u6587\u4EF6\u53C2\u6570\u5FC5\u987B\u662F\u5BF9\u8C61\u3002");
    }
    const pathValue = value.path;
    const contentValue = value.content;
    if (typeof pathValue !== "string") {
      throw new Error("\u5199\u6587\u4EF6\u8DEF\u5F84\u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u3002");
    }
    if (typeof contentValue !== "string") {
      throw new Error("\u5199\u6587\u4EF6\u5185\u5BB9\u5FC5\u987B\u662F\u5B57\u7B26\u4E32\u3002");
    }
    return {
      path: pathValue,
      content: contentValue
    };
  }
};
function compareWorkspaceDirectoryEntries(left, right) {
  if (left.kind !== right.kind) {
    return left.kind === "folder" ? -1 : 1;
  }
  if (left.sortName < right.sortName) {
    return -1;
  }
  if (left.sortName > right.sortName) {
    return 1;
  }
  return left.name < right.name ? -1 : left.name > right.name ? 1 : 0;
}
function removeSortKey(entry) {
  return {
    id: entry.id,
    name: entry.name,
    path: entry.path,
    kind: entry.kind
  };
}
function createDirectoryReadError(error, relativePath) {
  const displayPath = relativePath.trim() === "" ? "." : relativePath;
  const errorCode = getFileSystemErrorCode(error);
  if (errorCode === "EPERM" || errorCode === "EACCES") {
    return new Error(`\u6CA1\u6709\u6743\u9650\u8BFB\u53D6\u76EE\u5F55\uFF1A${displayPath}\u3002\u8BF7\u68C0\u67E5 Windows \u6587\u4EF6\u5939\u6743\u9650\uFF0C\u6216\u8DF3\u8FC7\u8BE5\u76EE\u5F55\u3002`);
  }
  if (errorCode === "ENOENT") {
    return new Error(`\u76EE\u5F55\u4E0D\u5B58\u5728\u6216\u5DF2\u88AB\u5220\u9664\uFF1A${displayPath}\u3002\u8BF7\u5237\u65B0\u8D44\u6E90\u7BA1\u7406\u5668\u3002`);
  }
  if (error instanceof Error) {
    return new Error(`\u8BFB\u53D6\u76EE\u5F55\u5931\u8D25\uFF1A${displayPath}\u3002${error.message}`);
  }
  return new Error(`\u8BFB\u53D6\u76EE\u5F55\u5931\u8D25\uFF1A${displayPath}\u3002`);
}
function getFileSystemErrorCode(error) {
  if (!isRecord2(error)) {
    return null;
  }
  return typeof error.code === "string" ? error.code : null;
}
function isRecord2(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function formatFileSize(bytes) {
  if (bytes < BYTES_PER_KIBIBYTE) {
    return `${bytes} B`;
  }
  if (bytes < BYTES_PER_MEBIBYTE) {
    return `${(bytes / BYTES_PER_KIBIBYTE).toFixed(1)} KiB`;
  }
  return `${(bytes / BYTES_PER_MEBIBYTE).toFixed(1)} MiB`;
}
var WorkbenchMainApp = class {
  workbenchWindow = new WorkbenchWindow();
  pathGuard = new WorkspacePathGuard();
  fileService = new WorkspaceFileService(this.pathGuard);
  ipcController = new WorkbenchIpcController(
    this.workbenchWindow,
    this.fileService
  );
  // aiBridge 依赖 app.getAppPath()，必须在 app ready 之后初始化
  aiBridge = null;
  run() {
    void import_electron2.app.whenReady().then(() => {
      this.aiBridge = new WorkbenchAiBridge(
        () => this.workbenchWindow.getWindow(),
        getPythonBridgeScriptPath(),
        getCodoRepoRoot()
      );
      this.configureApplicationMenu();
      this.ipcController.register();
      this.aiBridge.registerIpc();
      this.workbenchWindow.create();
      import_electron2.app.on("activate", () => {
        if (import_electron2.BrowserWindow.getAllWindows().length === 0) {
          this.workbenchWindow.create();
        }
      });
    });
    import_electron2.app.on("window-all-closed", () => {
      if (process.platform !== "darwin") {
        import_electron2.app.quit();
      }
    });
    import_electron2.app.on("before-quit", () => {
      this.aiBridge?.dispose();
    });
  }
  /**
   * 配置应用菜单。
   *
   * 工作流：
   * 1. Electron 默认会显示 File/Edit/View/Window/Help 原生菜单。
   * 2. 当前 UI 还没有设计菜单命令体系，保留默认菜单会显得像开发壳。
   * 3. 先移除默认菜单，后续需要命令时再用自定义命令面板或正式菜单接回。
   */
  configureApplicationMenu() {
    import_electron2.Menu.setApplicationMenu(null);
  }
};
function getPythonBridgeScriptPath() {
  return import_node_path2.default.join(import_electron2.app.getAppPath(), "python", "ai_bridge.py");
}
function getCodoRepoRoot() {
  return import_node_path2.default.resolve(import_electron2.app.getAppPath(), "..");
}
new WorkbenchMainApp().run();
