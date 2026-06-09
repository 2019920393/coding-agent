import type {
  AiAgentSummary,
  AiBridgeEvent,
  AiImageAttachment,
  AiPermissionMode,
  AiPendingInteraction,
  AiRuntimeMetadata,
  AiRuntimePhase,
  AiSessionInfo,
  AiSessionMessage,
  AiTodoItem,
  AiToolReceipt,
  AiToolSummary
} from "../../shared/aiProtocol";

const MAX_VISIBLE_MESSAGES = 40;
const MAX_VISIBLE_TOOL_SUMMARIES = 20;
const MAX_VISIBLE_EXECUTION_EVENTS = 80;
const MAX_VISIBLE_TOOL_INPUTS = 12;
const MAX_VISIBLE_ACTIVITY_CARDS = 40;

export type AiConversationStatus =
  | "idle"
  | "ready"
  | "streaming"
  | "running-tools"
  | "completed"
  | "cancelling"
  | "error";

export type AiMessageRole = "assistant" | "user" | "system";

export type AiMessageStatus = "complete" | "streaming" | "error";

export type AiExecutionEventStatus = "running" | "completed" | "error" | "info";

export type AiActivityCardKind = "tool" | "todo" | "agent";

export type AiActivityCardStatus = "running" | "completed" | "error" | "pending";

export type AiSessionLoadStatus = "idle" | "loading" | "ready" | "error";

export type AiExecutionEventKind =
  | "turn"
  | "phase"
  | "stream"
  | "content"
  | "tool"
  | "todo"
  | "agent"
  | "error";

export interface AiConversationMessage {
  id: string;
  role: AiMessageRole;
  content: string;
  images: AiImageAttachment[];
  createdAt: string;
  status: AiMessageStatus;
}

export interface AiActivityCard {
  id: string;
  turnId: string;
  anchorMessageId: string | null;
  kind: AiActivityCardKind;
  title: string;
  summary: string;
  detail: string;
  status: AiActivityCardStatus;
  createdAt: string;
  sourceId: string;
  receipt: AiToolReceipt | null;
}

export interface AiExecutionEvent {
  id: string;
  kind: AiExecutionEventKind;
  title: string;
  detail: string;
  status: AiExecutionEventStatus;
  createdAt: string;
  sourceId: string;
}

export interface AiTodoGroup {
  key: string;
  items: AiTodoItem[];
  updatedAt: string;
}

export interface AiToolInputState {
  index: number;
  toolUseId: string | null;
  toolName: string | null;
  accumulatedJson: string;
  updatedAt: string;
}

export interface AiRuntimeState {
  phase: AiRuntimePhase;
  turnCount: number | null;
  checkpointId: string | null;
  activeToolIds: string[];
  activeAgentId: string | null;
  pendingInteraction: AiPendingInteraction | null;
  metadata: AiRuntimeMetadata | null;
}

export interface AiPanelState {
  workspaceName: string | null;
  workspacePath: string | null;
  sessions: AiSessionInfo[];
  selectedSessionId: string | null;
  sessionStatus: AiSessionLoadStatus;
  sessionMessage: string;
  permissionMode: AiPermissionMode;
  activeTurnId: string | null;
  activeAssistantMessageId: string | null;
  assistantMessageSequence: number;
  status: AiConversationStatus;
  statusMessage: string;
  runtime: AiRuntimeState;
  messages: AiConversationMessage[];
  activityCards: AiActivityCard[];
  executionEvents: AiExecutionEvent[];
  toolSummaries: AiToolSummary[];
  toolInputs: AiToolInputState[];
  todoGroups: AiTodoGroup[];
  agents: AiAgentSummary[];
}

export type AiPanelAction =
  | {
      type: "workspace/changed";
      workspaceName: string | null;
      workspacePath: string | null;
      createdAt: string;
    }
  | { type: "session/load-started"; createdAt: string }
  | { type: "session/list-loaded"; sessions: AiSessionInfo[]; createdAt: string }
  | {
      type: "session/messages-loaded";
      sessionId: string;
      messages: AiSessionMessage[];
      createdAt: string;
    }
  | { type: "session/load-failed"; message: string; createdAt: string }
  | { type: "session/messages-load-failed"; sessionId: string; message: string; createdAt: string }
  | { type: "session/selected"; sessionId: string; createdAt: string }
  | { type: "session/new-started"; createdAt: string }
  | { type: "permission-mode/changed"; mode: AiPermissionMode; createdAt: string }
  | {
      type: "turn/submitted";
      turnId: string;
      prompt: string;
      images: AiImageAttachment[];
      createdAt: string;
    }
  | { type: "turn/cancel-started"; createdAt: string }
  | { type: "turn/event-received"; event: AiBridgeEvent; createdAt: string }
  | { type: "turn/local-error"; message: string; createdAt: string };

/**
 * 创建右侧 AI 面板初始状态。
 *
 * 工作流：
 * 1. 应用启动时还没有工作区，也没有 codo runtime。
 * 2. 对话区显示一条固定提示。
 * 3. 后续所有 AI 状态都由 bridge 事件驱动，避免前端自己编造运行时事实。
 */
export function createInitialAiPanelState(): AiPanelState {
  return {
    workspaceName: null,
    workspacePath: null,
    sessions: [],
    selectedSessionId: null,
    sessionStatus: "idle",
    sessionMessage: "未选择工作区。",
    permissionMode: "auto",
    activeTurnId: null,
    activeAssistantMessageId: null,
    assistantMessageSequence: 0,
    status: "idle",
    statusMessage: "请选择工作区后开始对话。",
    runtime: createIdleRuntimeState(),
    messages: [
      createAssistantMessage(
        "assistant-welcome",
        "选择工作区后，我可以结合当前项目进行代码理解、修改建议和任务拆解。",
        "complete",
        formatCurrentTime()
      )
    ],
    activityCards: [],
    executionEvents: [],
    toolSummaries: [],
    toolInputs: [],
    todoGroups: [],
    agents: []
  };
}

/**
 * AI 面板状态 reducer。
 *
 * 工作流：
 * 1. 用户提交 prompt 时创建用户消息和 assistant 流式占位。
 * 2. `text_delta` 只追加正文，不混入工具、Todo、Agent 信息。
 * 3. codo runtime 事件进入 executionEvents / toolSummaries / todoGroups / agents。
 */
export function aiPanelReducer(
  state: AiPanelState,
  action: AiPanelAction
): AiPanelState {
  switch (action.type) {
    case "workspace/changed":
      return createWorkspaceReadyState(
        action.workspaceName,
        action.workspacePath,
        state.permissionMode,
        action.createdAt
      );

    case "session/load-started":
      return {
        ...state,
        sessions: [],
        selectedSessionId: null,
        sessionStatus: "loading",
        sessionMessage: "正在读取历史会话..."
      };

    case "session/list-loaded":
      return {
        ...state,
        sessions: action.sessions,
        selectedSessionId: action.sessions[0]?.sessionId ?? null,
        sessionStatus: "ready",
        sessionMessage:
          action.sessions.length > 0
            ? `已恢复最近会话：${action.sessions[0].title}`
            : "当前工作区没有历史会话，将创建新会话。",
        messages: [
          createAssistantMessage(
            "assistant-session-ready",
            action.sessions.length > 0
              ? `正在恢复历史会话「${action.sessions[0].title}」...`
              : buildSessionReadyMessage(null),
            "complete",
            action.createdAt
          )
        ]
      };

    case "session/messages-loaded":
      if (state.selectedSessionId !== action.sessionId) {
        return state;
      }

      return {
        ...state,
        sessionStatus: "ready",
        sessionMessage: `已恢复历史会话：${formatSelectedSessionTitle(state.sessions, action.sessionId)}`,
        messages: buildHistoryMessages(action.messages, action.createdAt)
      };

    case "session/load-failed":
      return {
        ...state,
        sessions: [],
        selectedSessionId: null,
        sessionStatus: "error",
        sessionMessage: action.message
      };

    case "session/messages-load-failed":
      if (state.selectedSessionId !== action.sessionId) {
        return state;
      }

      return {
        ...state,
        sessionStatus: "error",
        sessionMessage: action.message,
        messages: [
          createSystemMessage(
            `system-session-load-error-${action.sessionId}`,
            `历史会话已选择，但消息正文恢复失败：${action.message}`,
            "error",
            action.createdAt
          )
        ]
      };

    case "session/selected":
      return {
        ...state,
        selectedSessionId: action.sessionId,
        sessionStatus: "ready",
        sessionMessage: `正在恢复历史会话：${formatSelectedSessionTitle(state.sessions, action.sessionId)}`,
        runtime: createIdleRuntimeState(),
        messages: [
          createAssistantMessage(
            `assistant-session-selected-${action.sessionId}`,
            `正在恢复历史会话「${formatSelectedSessionTitle(state.sessions, action.sessionId)}」...`,
            "complete",
            action.createdAt
          )
        ],
        activityCards: [],
        executionEvents: [],
        toolSummaries: [],
        toolInputs: [],
        todoGroups: [],
        agents: []
      };

    case "session/new-started":
      return createNewSessionState(state, action.createdAt);

    case "permission-mode/changed":
      return {
        ...state,
        permissionMode: action.mode,
        statusMessage: formatPermissionModeMessage(action.mode)
      };

    case "turn/submitted":
      return {
        ...state,
        activeTurnId: action.turnId,
        activeAssistantMessageId: `assistant-${action.turnId}-1`,
        assistantMessageSequence: 1,
        status: "streaming",
        statusMessage: "正在发送给 AI...",
        runtime: {
          ...state.runtime,
          phase: "submitted",
          turnCount: null,
          checkpointId: null,
          activeToolIds: [],
          activeAgentId: null,
          pendingInteraction: null,
          metadata: null
        },
        executionEvents: trimExecutionEvents([
          ...state.executionEvents,
          createExecutionEvent(
            `submitted-${action.turnId}`,
            "turn",
            "用户请求已提交",
            action.prompt,
            "running",
            action.createdAt,
            `submitted-${action.turnId}`
          )
        ]),
        toolSummaries: [],
        toolInputs: [],
        todoGroups: [],
        agents: [],
        messages: trimMessages([
          ...state.messages,
          createUserMessage(
            `user-${action.turnId}`,
            action.prompt,
            action.createdAt,
            action.images
          ),
          createAssistantMessage(`assistant-${action.turnId}-1`, "", "streaming", action.createdAt)
        ])
      };

    case "turn/cancel-started":
      return {
        ...state,
        status: "cancelling",
        statusMessage: "正在中断当前 AI 轮次...",
        executionEvents: trimExecutionEvents([
          ...state.executionEvents,
          createExecutionEvent(
            `cancel-${state.activeTurnId ?? "none"}-${Date.now()}`,
            "turn",
            "用户请求中断",
            "已向 codo runtime 发送 interrupt。",
            "running",
            action.createdAt,
            `cancel-${state.activeTurnId ?? "none"}`
          )
        ])
      };

    case "turn/event-received":
      return applyBridgeEvent(state, action.event, action.createdAt);

    case "turn/local-error":
      return appendSystemError(state, action.message, action.createdAt);
  }
}

/**
 * 判断当前 AI 是否正在执行，供发送按钮和停止按钮使用。
 */
export function isAiTurnActive(state: AiPanelState): boolean {
  return state.activeTurnId !== null;
}

/**
 * 忽略旧轮次迟到事件。
 *
 * 工作流：
 * 1. 用户点击停止后，bridge 会立即发 interrupt-ack 释放 UI。
 * 2. Python runtime 取消过程中可能还有旧 turn 的 status/tool/text 事件迟到。
 * 3. 这些事件不能再把新会话或空闲状态改回“回复中”。
 */
function isStaleTurnEvent(state: AiPanelState, event: AiBridgeEvent): boolean {
  if (!("turnId" in event)) {
    return false;
  }

  if (state.activeTurnId === null) {
    return true;
  }

  return event.turnId !== state.activeTurnId;
}

/**
 * 根据 bridge 事件更新 UI 状态。
 */
function applyBridgeEvent(
  state: AiPanelState,
  event: AiBridgeEvent,
  createdAt: string
): AiPanelState {
  if (isStaleTurnEvent(state, event)) {
    return state;
  }

  switch (event.kind) {
    case "bridge-ready":
      const shouldUpdateSession =
        event.sessionId !== null && event.workspacePath === state.workspacePath;
      const bridgeSessionId = shouldUpdateSession ? event.sessionId : null;

      return {
        ...state,
        selectedSessionId: bridgeSessionId ?? state.selectedSessionId,
        status:
          state.activeTurnId === null && state.status === "idle" ? "ready" : state.status,
        statusMessage: state.activeTurnId === null ? "AI bridge 已就绪。" : state.statusMessage,
        sessionMessage:
          bridgeSessionId !== null
            ? `当前会话：${formatSelectedSessionTitle(state.sessions, bridgeSessionId)}`
            : state.sessionMessage
      };

    case "session-title-updated":
      if (event.workspacePath !== state.workspacePath) {
        return state;
      }

      return {
        ...state,
        sessions: updateSessionTitle(state.sessions, event.sessionId, event.title),
        sessionMessage:
          state.selectedSessionId === event.sessionId
            ? `当前会话：${event.title}`
            : state.sessionMessage
      };

    case "bridge-error":
      return appendSystemError(state, event.message, createdAt);

    case "turn-started":
      return {
        ...state,
        activeTurnId: event.turnId,
        status: "streaming",
        statusMessage: `第 ${event.turnCount} 轮开始。`,
        runtime: {
          ...state.runtime,
          turnCount: event.turnCount
        },
        executionEvents: appendExecutionEvent(
          state.executionEvents,
          createExecutionEvent(
            `turn-started-${event.turnId}-${event.turnCount}`,
            "turn",
            `第 ${event.turnCount} 轮开始`,
            `messages=${event.messagesCount}`,
            "running",
            createdAt,
            `turn-started-${event.turnId}-${event.turnCount}`
          )
        )
      };

    case "turn-completed":
      return {
        ...state,
        messages: markAssistantMessageComplete(state.messages, event.turnId),
        executionEvents: appendExecutionEvent(
          state.executionEvents,
          createExecutionEvent(
            `turn-completed-${event.turnId}-${event.turnCount}`,
            "turn",
            "codo runtime 轮次完成",
            buildTurnCompletedDetail(event.reason, event.messageCount),
            "completed",
            createdAt,
            `turn-completed-${event.turnId}-${event.turnCount}`
          )
        )
      };

    case "status-changed":
      return applyStatusChangedEvent(state, event, createdAt);

    case "stream-started":
      return appendRuntimeEvent(state, {
        id: `stream-started-${event.turnId}`,
        kind: "stream",
        title: "模型流式请求开始",
        detail: "正在等待模型返回 content blocks。",
        status: "running",
        sourceId: `stream-started-${event.turnId}`,
        createdAt
      });

    case "content-block-started":
      return appendRuntimeEvent(state, {
        id: `content-block-${event.turnId}-${event.index}`,
        kind: "content",
        title: formatContentBlockTitle(event.blockType),
        detail: formatContentBlockDetail(event.toolName, event.toolUseId),
        status: "running",
        sourceId: `content-block-${event.turnId}-${event.index}`,
        createdAt
      });

    case "content-block-stopped":
      return upsertExecutionEvent(state, {
        id: `content-block-stop-${event.turnId}-${event.index}`,
        kind: "content",
        title: `内容块 ${event.index} 已结束`,
        detail: "该 block 的流式内容已经结束。",
        status: "completed",
        sourceId: `content-block-${event.turnId}-${event.index}`,
        createdAt
      });

    case "text-delta":
      return appendAssistantDeltaToState(
        {
          ...state,
          status: "streaming",
          statusMessage: "AI 正在流式输出..."
        },
        event.turnId,
        event.delta,
        createdAt
      );

    case "thinking-delta":
      return upsertExecutionEvent(
        {
          ...state,
          status: "streaming",
          statusMessage: "AI 正在思考..."
        },
        {
          id: `thinking-${event.turnId}-${event.index ?? 0}`,
          kind: "stream",
          title: "模型 thinking 流",
          detail: event.delta,
          status: "running",
          sourceId: `thinking-${event.turnId}-${event.index ?? 0}`,
          createdAt
        }
      );

    case "tool-input-delta":
      return applyToolInputDelta(state, event, createdAt);

    case "tool-started":
      return upsertActivityCard(
        upsertToolSummary(
          appendRuntimeEvent(state, {
            id: `tool-started-${event.toolUseId}`,
            kind: "tool",
            title: `工具开始：${event.toolName}`,
            detail: event.inputPreview,
            status: "running",
            sourceId: `tool-${event.toolUseId}`,
            createdAt
          }),
          {
            toolUseId: event.toolUseId,
            name: event.toolName,
            status: event.status,
            summary: event.inputPreview || `正在调用 ${event.toolName}`,
            detail: event.inputPreview,
            receipt: null
          }
        ),
        createActivityCard(
          `tool-${event.turnId}-${event.toolUseId}`,
          event.turnId,
          "tool",
          formatToolWorkTitle(event.toolName),
          event.inputPreview || `正在调用 ${event.toolName}`,
          event.inputPreview,
          "running",
          createdAt,
          `tool-${event.turnId}-${event.toolUseId}`,
          null
        )
      );

    case "tool-progress":
      return upsertActivityCard(
        mergeToolProgress(
          upsertExecutionEvent(state, {
            id: `tool-progress-${event.toolUseId}`,
            kind: "tool",
            title: `工具进度：${event.toolName}`,
            detail: event.progress,
            status: "running",
            sourceId: `tool-${event.toolUseId}`,
            createdAt
          }),
          event.toolUseId,
          event.toolName,
          event.progress
        ),
        createActivityCard(
          `tool-${event.turnId}-${event.toolUseId}`,
          event.turnId,
          "tool",
          formatToolWorkTitle(event.toolName),
          event.progress || `正在执行 ${event.toolName}`,
          event.progress,
          "running",
          createdAt,
          `tool-${event.turnId}-${event.toolUseId}`,
          null
        )
      );

    case "tool-completed":
      return upsertActivityCard(
        upsertToolSummary(
          upsertExecutionEvent(state, {
            id: `tool-completed-${event.tool.toolUseId}`,
            kind: "tool",
            title: `工具完成：${event.tool.name}`,
            detail: event.tool.detail || event.tool.summary,
            status: event.tool.status === "error" ? "error" : "completed",
            sourceId: `tool-${event.tool.toolUseId}`,
            createdAt
          }),
          event.tool
        ),
        createToolActivityCard(event.turnId, event.tool, createdAt)
      );

    case "tool-result":
      return upsertActivityCard(
        upsertToolSummary(
          appendRuntimeEvent(state, {
            id: `tool-result-${event.tool.toolUseId}-${Date.now()}`,
            kind: "tool",
            title: `工具结果回写：${event.tool.name}`,
            detail: event.tool.detail || event.tool.summary,
            status: event.isError ? "error" : "completed",
            sourceId: `tool-result-${event.tool.toolUseId}`,
            createdAt
          }),
          event.tool
        ),
        createToolActivityCard(
          event.turnId,
          {
            ...event.tool,
            status: event.isError ? "error" : event.tool.status
          },
          createdAt
        )
      );

    case "todo-updated":
      return upsertActivityCard(
        applyTodoUpdated(state, event.key, event.items, event.toolUseId, createdAt),
        createActivityCard(
          `todo-${event.turnId}-${event.key}`,
          event.turnId,
          "todo",
          "更新任务板",
          `${event.items.length} 个任务`,
          event.items.map((item) => `${formatTodoStatusText(item.status)} ${item.content}`).join("\n"),
          "completed",
          createdAt,
          `todo-${event.turnId}-${event.key}`,
          null
        )
      );

    case "compact":
      return appendRuntimeEvent(state, {
        id: `compact-${event.turnId}-${Date.now()}`,
        kind: "phase",
        title: "上下文已压缩",
        detail: `tokens: ${event.preTokens} -> ${event.postTokens}`,
        status: "completed",
        sourceId: `compact-${event.turnId}`,
        createdAt
      });

    case "agent-started":
      return upsertActivityCard(
        applyAgentStarted(state, event.agent, createdAt),
        createActivityCard(
          `agent-${event.turnId}-${event.agent.agentId}`,
          event.turnId,
          "agent",
          `启动 ${event.agent.label}`,
          event.agent.currentAction || buildAgentDetail(event.agent),
          buildAgentDetail(event.agent),
          "running",
          createdAt,
          `agent-${event.turnId}-${event.agent.agentId}`,
          null
        )
      );

    case "agent-delta":
      return upsertActivityCard(
        applyAgentDelta(state, event.agentId, event.taskId, event.contentDelta, event.thinkingDelta, createdAt),
        createActivityCard(
          `agent-${event.turnId}-${event.agentId}`,
          event.turnId,
          "agent",
          `Agent ${event.agentId}`,
          event.contentDelta || event.thinkingDelta || "Agent 正在运行",
          event.contentDelta || event.thinkingDelta,
          "running",
          createdAt,
          `agent-${event.turnId}-${event.agentId}`,
          null
        )
      );

    case "agent-tool-started":
      return appendRuntimeEvent(
        updateAgentAction(state, event.agentId, `工具开始：${event.toolName}`),
        {
          id: `agent-tool-started-${event.agentId}-${event.toolUseId}`,
          kind: "agent",
          title: `Agent 工具开始：${event.toolName}`,
          detail: event.inputPreview,
          status: "running",
          sourceId: `agent-tool-${event.agentId}-${event.toolUseId}`,
          createdAt
        }
      );

    case "agent-tool-completed":
      return upsertExecutionEvent(
        updateAgentAction(state, event.agentId, `工具完成：${event.tool.name}`),
        {
          id: `agent-tool-completed-${event.agentId}-${event.tool.toolUseId}`,
          kind: "agent",
          title: `Agent 工具完成：${event.tool.name}`,
          detail: event.tool.detail || event.tool.summary,
          status: event.tool.status === "error" ? "error" : "completed",
          sourceId: `agent-tool-${event.agentId}-${event.tool.toolUseId}`,
          createdAt
        }
      );

    case "agent-completed":
      return upsertActivityCard(
        applyAgentCompleted(state, event.agentId, event.taskId, event.result, event.totalTokens, createdAt),
        createActivityCard(
          `agent-${event.turnId}-${event.agentId}`,
          event.turnId,
          "agent",
          `Agent ${event.agentId} 完成`,
          event.result,
          event.result,
          "completed",
          createdAt,
          `agent-${event.turnId}-${event.agentId}`,
          null
        )
      );

    case "agent-error":
      return upsertActivityCard(
        applyAgentError(state, event.agentId, event.taskId, event.error, createdAt),
        createActivityCard(
          `agent-${event.turnId}-${event.agentId}`,
          event.turnId,
          "agent",
          `Agent ${event.agentId} 失败`,
          event.error,
          event.error,
          "error",
          createdAt,
          `agent-${event.turnId}-${event.agentId}`,
          null
        )
      );

    case "message-stop":
      return appendRuntimeEvent(
        {
          ...state,
          statusMessage: "回复正文已生成，正在处理后续动作。"
        },
        {
          id: `message-stop-${event.turnId}-${Date.now()}`,
          kind: "stream",
          title: "模型消息结束",
          detail: "如果包含工具调用，接下来会进入工具执行阶段。",
          status: "completed",
          sourceId: `message-stop-${event.turnId}`,
          createdAt
        }
      );

    case "completed":
      return {
        ...appendRuntimeEvent(state, {
          id: `completed-${event.turnId}`,
          kind: "turn",
          title: "AI 轮次结束",
          detail: getCompletedMessage(event.reason),
          status: "completed",
          sourceId: `completed-${event.turnId}`,
          createdAt
        }),
        activeTurnId: null,
        activeAssistantMessageId: null,
        status: "completed",
        statusMessage: getCompletedMessage(event.reason),
        runtime: {
          ...state.runtime,
          phase: "complete",
          activeToolIds: [],
          activeAgentId: null,
          pendingInteraction: null
        },
        messages: markAssistantMessageComplete(state.messages, event.turnId)
      };

    case "interrupt-ack":
      return {
        ...appendRuntimeEvent(state, {
          id: `interrupt-${event.turnId}`,
          kind: "turn",
          title: "AI 轮次已中断",
          detail: event.reason,
          status: "completed",
          sourceId: `interrupt-${event.turnId}`,
          createdAt
        }),
        activeTurnId: null,
        activeAssistantMessageId: null,
        status: "completed",
        statusMessage: "当前 AI 轮次已中断。",
        runtime: {
          ...state.runtime,
          phase: "interrupted",
          activeToolIds: [],
          pendingInteraction: null
        },
        messages: markAssistantMessageComplete(state.messages, event.turnId)
      };

    case "error":
      if (event.recoverable) {
        return appendRuntimeEvent(
          {
            ...state,
            status: "streaming",
            statusMessage: `可恢复错误，正在重试：${event.message}`
          },
          {
            id: `recoverable-error-${event.turnId}-${Date.now()}`,
            kind: "error",
            title: "可恢复错误",
            detail: event.message,
            status: "running",
            sourceId: `recoverable-error-${event.turnId}`,
            createdAt
          }
        );
      }

      return appendSystemError(
        {
          ...state,
          activeTurnId: null,
          activeAssistantMessageId: null,
          messages: markAssistantMessageComplete(state.messages, event.turnId)
        },
        event.message,
        createdAt
      );
  }
}

function createIdleRuntimeState(): AiRuntimeState {
  return {
    phase: "idle",
    turnCount: null,
    checkpointId: null,
    activeToolIds: [],
    activeAgentId: null,
    pendingInteraction: null,
    metadata: null
  };
}

function createWorkspaceReadyState(
  workspaceName: string | null,
  workspacePath: string | null,
  permissionMode: AiPermissionMode,
  createdAt: string
): AiPanelState {
  const content =
    workspaceName === null
      ? "选择工作区后，我可以结合当前项目进行代码理解、修改建议和任务拆解。"
      : `已连接到工作区 ${workspaceName}。我会基于 codo runtime 展示正文流、工具、Todo 和 Agent 状态。`;

  return {
    workspaceName,
    workspacePath,
    sessions: [],
    selectedSessionId: null,
    sessionStatus: workspaceName === null ? "idle" : "loading",
    sessionMessage: workspaceName === null ? "未选择工作区。" : "正在读取历史会话...",
    permissionMode,
    activeTurnId: null,
    activeAssistantMessageId: null,
    assistantMessageSequence: 0,
    status: workspaceName === null ? "idle" : "ready",
    statusMessage: workspaceName === null ? "请选择工作区后开始对话。" : "可以开始对话。",
    runtime: {
      ...createIdleRuntimeState(),
      phase: workspaceName === null ? "idle" : "ready"
    },
    messages: [createAssistantMessage("assistant-workspace", content, "complete", createdAt)],
    activityCards: [],
    executionEvents: [],
    toolSummaries: [],
    toolInputs: [],
    todoGroups: [],
    agents: []
  };
}

/**
 * 切到一个尚未创建的会话。
 *
 * 工作流：
 * 1. 前端先清空当前对话流和运行态，让用户看到“新会话”空白起点。
 * 2. `selectedSessionId` 置为 null，下一次发送消息时由 Python runtime 创建真实会话。
 * 3. 历史会话列表保留，用户仍可通过“恢复”切回旧会话。
 */
function createNewSessionState(state: AiPanelState, createdAt: string): AiPanelState {
  const message =
    state.workspacePath === null
      ? "先选择工作区，再开始新的 AI 会话。"
      : "已切换到新会话。下一条消息会创建新的上下文。";

  return {
    ...state,
    selectedSessionId: null,
    sessionStatus: state.workspacePath === null ? "idle" : "ready",
    sessionMessage:
      state.workspacePath === null ? "未选择工作区。" : "新会话将在首次发送后创建。",
    activeTurnId: null,
    activeAssistantMessageId: null,
    assistantMessageSequence: 0,
    status: state.workspacePath === null ? "idle" : "ready",
    statusMessage: state.workspacePath === null ? "请选择工作区后开始对话。" : "新会话已准备好。",
    runtime: {
      ...createIdleRuntimeState(),
      phase: state.workspacePath === null ? "idle" : "ready"
    },
    messages: [
      createAssistantMessage("assistant-new-session", message, "complete", createdAt)
    ],
    activityCards: [],
    executionEvents: [],
    toolSummaries: [],
    toolInputs: [],
    todoGroups: [],
    agents: []
  };
}

function applyStatusChangedEvent(
  state: AiPanelState,
  event: Extract<AiBridgeEvent, { kind: "status-changed" }>,
  createdAt: string
): AiPanelState {
  if (event.phase === "interrupted") {
    const interruptedState: AiPanelState = {
      ...state,
      activeTurnId: null,
      activeAssistantMessageId: null,
      runtime: {
        phase: "interrupted",
        turnCount: event.turnCount,
        checkpointId: event.checkpointId,
        activeToolIds: [],
        activeAgentId: null,
        pendingInteraction: null,
        metadata: event.metadata
      },
      status: "completed",
      statusMessage: "当前 AI 轮次已中断。",
      messages: markAssistantMessageComplete(state.messages, event.turnId)
    };

    return appendRuntimeEvent(interruptedState, {
      id: `phase-${event.turnId}-interrupted-${event.checkpointId ?? "none"}`,
      kind: "phase",
      title: "阶段：中断",
      detail: event.metadata.summary || event.statusMessage,
      status: "completed",
      sourceId: `phase-${event.turnId}-interrupted-${event.checkpointId ?? "none"}`,
      createdAt
    });
  }

  // 当阶段变为 complete 或 error 时，将当前 turn 的所有 assistant 消息标记为完成
  const shouldCompleteMessages = event.phase === "complete" || event.phase === "error";
  const updatedMessages = shouldCompleteMessages
    ? markAssistantMessageComplete(state.messages, event.turnId)
    : state.messages;

  const nextState: AiPanelState = {
    ...state,
    runtime: {
      phase: event.phase,
      turnCount: event.turnCount,
      checkpointId: event.checkpointId,
      activeToolIds: event.activeToolIds,
      activeAgentId: event.activeAgentId,
      pendingInteraction: event.pendingInteraction,
      metadata: event.metadata
    },
    status: getStatusFromPhase(event.phase, event.activeToolIds.length),
    statusMessage: event.statusMessage,
    messages: updatedMessages
  };

  return appendRuntimeEvent(nextState, {
    id: `phase-${event.turnId}-${event.phase}-${event.checkpointId ?? "none"}`,
    kind: "phase",
    title: `阶段：${formatPhaseText(event.phase)}`,
    detail: event.metadata.summary || event.statusMessage,
    status: event.phase === "error" ? "error" : event.phase === "complete" ? "completed" : "running",
    sourceId: `phase-${event.turnId}-${event.phase}-${event.checkpointId ?? "none"}`,
    createdAt
  });
}

function applyToolInputDelta(
  state: AiPanelState,
  event: Extract<AiBridgeEvent, { kind: "tool-input-delta" }>,
  createdAt: string
): AiPanelState {
  const nextInput: AiToolInputState = {
    index: event.index,
    toolUseId: event.toolUseId,
    toolName: event.toolName,
    accumulatedJson: event.accumulatedJson,
    updatedAt: createdAt
  };
  const existingInput = state.toolInputs.find((input) => input.index === event.index);
  const nextInputs =
    existingInput === undefined
      ? [...state.toolInputs, nextInput]
      : state.toolInputs.map((input) => (input.index === event.index ? nextInput : input));

  return upsertExecutionEvent(
    {
      ...state,
      statusMessage: `正在生成 ${event.toolName ?? "工具"} 参数...`,
      toolInputs: trimToolInputs(nextInputs)
    },
    {
      id: `tool-input-${event.turnId}-${event.index}`,
      kind: "tool",
      title: `工具参数流：${event.toolName ?? "Tool"}`,
      detail: event.accumulatedJson,
      status: "running",
      sourceId: `tool-input-${event.turnId}-${event.index}`,
      createdAt
    }
  );
}

function applyTodoUpdated(
  state: AiPanelState,
  key: string,
  items: AiTodoItem[],
  toolUseId: string,
  createdAt: string
): AiPanelState {
  const nextGroup: AiTodoGroup = {
    key,
    items,
    updatedAt: createdAt
  };
  const existingGroup = state.todoGroups.find((group) => group.key === key);
  const nextGroups =
    existingGroup === undefined
      ? [...state.todoGroups, nextGroup]
      : state.todoGroups.map((group) => (group.key === key ? nextGroup : group));

  return appendRuntimeEvent(
    {
      ...state,
      todoGroups: nextGroups,
      statusMessage: `Todo 已更新：${key}`
    },
    {
      id: `todo-${key}-${toolUseId}-${Date.now()}`,
      kind: "todo",
      title: `Todo 更新：${key}`,
      detail: `${items.length} 项任务`,
      status: "completed",
      sourceId: `todo-${key}-${toolUseId}`,
      createdAt
    }
  );
}

function applyAgentStarted(
  state: AiPanelState,
  agent: AiAgentSummary,
  createdAt: string
): AiPanelState {
  return appendRuntimeEvent(
    upsertAgent(state, agent),
    {
      id: `agent-started-${agent.agentId}`,
      kind: "agent",
      title: `Agent 启动：${agent.label}`,
      detail: buildAgentDetail(agent),
      status: "running",
      sourceId: `agent-${agent.agentId}`,
      createdAt
    }
  );
}

function applyAgentDelta(
  state: AiPanelState,
  agentId: string,
  taskId: string | null,
  contentDelta: string,
  thinkingDelta: string,
  createdAt: string
): AiPanelState {
  const action = contentDelta || thinkingDelta || "Agent 正在运行";
  return upsertExecutionEvent(
    updateAgentAction(state, agentId, action),
    {
      id: `agent-delta-${agentId}-${taskId ?? "foreground"}`,
      kind: "agent",
      title: `Agent 输出：${agentId}`,
      detail: action,
      status: "running",
      sourceId: `agent-${agentId}`,
      createdAt
    }
  );
}

function applyAgentCompleted(
  state: AiPanelState,
  agentId: string,
  taskId: string | null,
  result: string,
  totalTokens: number,
  createdAt: string
): AiPanelState {
  return upsertExecutionEvent(
    updateAgent(state, agentId, {
      status: "completed",
      taskId,
      resultPreview: result,
      currentAction: "已完成",
      totalTokens
    }),
    {
      id: `agent-completed-${agentId}-${taskId ?? "foreground"}`,
      kind: "agent",
      title: `Agent 完成：${agentId}`,
      detail: result,
      status: "completed",
      sourceId: `agent-${agentId}`,
      createdAt
    }
  );
}

function applyAgentError(
  state: AiPanelState,
  agentId: string,
  taskId: string | null,
  error: string,
  createdAt: string
): AiPanelState {
  return upsertExecutionEvent(
    updateAgent(state, agentId, {
      status: "error",
      taskId,
      resultPreview: error,
      currentAction: "执行失败"
    }),
    {
      id: `agent-error-${agentId}-${taskId ?? "foreground"}`,
      kind: "agent",
      title: `Agent 错误：${agentId}`,
      detail: error,
      status: "error",
      sourceId: `agent-${agentId}`,
      createdAt
    }
  );
}

function createUserMessage(
  id: string,
  content: string,
  createdAt: string,
  images: AiImageAttachment[] = []
): AiConversationMessage {
  return {
    id,
    role: "user",
    content,
    images,
    createdAt,
    status: "complete"
  };
}

function createAssistantMessage(
  id: string,
  content: string,
  status: AiMessageStatus,
  createdAt: string
): AiConversationMessage {
  return {
    id,
    role: "assistant",
    content,
    images: [],
    createdAt,
    status
  };
}

function createSystemMessage(
  id: string,
  content: string,
  status: AiMessageStatus,
  createdAt: string
): AiConversationMessage {
  return {
    id,
    role: "system",
    content,
    images: [],
    createdAt,
    status
  };
}

function buildHistoryMessages(
  messages: AiSessionMessage[],
  fallbackCreatedAt: string
): AiConversationMessage[] {
  /**
   * 历史消息恢复工作流：
   * 1. Python helper 已经过滤掉工具结果和空文本。
   * 2. 前端只负责把持久化消息转成对话气泡。
   * 3. 没有可展示消息时保留一条提示，避免右侧空白。
   */
  if (messages.length === 0) {
    return [
      createAssistantMessage(
        "assistant-empty-history",
        "该历史会话没有可展示的对话正文。下一条消息仍会续接它的上下文。",
        "complete",
        fallbackCreatedAt
      )
    ];
  }

  return trimMessages(
    messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      images: message.images,
      createdAt: formatHistoryMessageTime(message.createdAt, fallbackCreatedAt),
      status: "complete"
    }))
  );
}

function createExecutionEvent(
  id: string,
  kind: AiExecutionEventKind,
  title: string,
  detail: string,
  status: AiExecutionEventStatus,
  createdAt: string,
  sourceId: string
): AiExecutionEvent {
  return {
    id,
    kind,
    title,
    detail,
    status,
    createdAt,
    sourceId
  };
}

function createActivityCard(
  id: string,
  turnId: string,
  kind: AiActivityCardKind,
  title: string,
  summary: string,
  detail: string,
  status: AiActivityCardStatus,
  createdAt: string,
  sourceId: string,
  receipt: AiToolReceipt | null
): AiActivityCard {
  return {
    id,
    turnId,
    anchorMessageId: null,
    kind,
    title,
    summary,
    detail,
    status,
    createdAt,
    sourceId,
    receipt
  };
}

function createToolActivityCard(
  turnId: string,
  tool: AiToolSummary,
  createdAt: string
): AiActivityCard {
  return createActivityCard(
    `tool-${turnId}-${tool.toolUseId}`,
    turnId,
    "tool",
    formatToolWorkTitle(tool.name),
    tool.summary || `${tool.name} 已完成`,
    tool.detail,
    tool.status === "cancelled" ? "error" : tool.status,
    createdAt,
    `tool-${turnId}-${tool.toolUseId}`,
    tool.receipt
  );
}

function appendAssistantDeltaToState(
  state: AiPanelState,
  turnId: string,
  delta: string,
  createdAt: string
): AiPanelState {
  const assistantMessageId =
    state.activeAssistantMessageId ?? `assistant-${turnId}-${state.assistantMessageSequence + 1}`;
  const existingMessage = state.messages.find((message) => message.id === assistantMessageId);

  if (existingMessage === undefined) {
    return {
      ...state,
      activeAssistantMessageId: assistantMessageId,
      assistantMessageSequence: state.assistantMessageSequence + 1,
      messages: trimMessages([
        ...state.messages,
        createAssistantMessage(assistantMessageId, delta, "streaming", createdAt)
      ])
    };
  }

  return {
    ...state,
    activeAssistantMessageId: assistantMessageId,
    messages: state.messages.map((message) =>
      message.id === assistantMessageId
        ? {
            ...message,
            content: `${message.content}${delta}`,
            status: "streaming"
          }
        : message
    )
  };
}

function markAssistantMessageComplete(
  messages: AiConversationMessage[],
  turnId: string
): AiConversationMessage[] {
  const assistantMessagePrefix = `assistant-${turnId}-`;

  return messages.map((message) =>
    message.id.startsWith(assistantMessagePrefix)
      ? {
          ...message,
          status: "complete"
        }
      : message
  );
}

function markStreamingMessagesError(
  messages: AiConversationMessage[]
): AiConversationMessage[] {
  return messages.map((message) =>
    message.status === "streaming"
      ? {
          ...message,
          status: "error"
        }
      : message
  );
}

function appendSystemError(
  state: AiPanelState,
  message: string,
  createdAt: string
): AiPanelState {
  const systemErrorId = `system-error-${Date.now()}`;

  return appendRuntimeEvent(
    {
      ...state,
      activeTurnId: null,
      activeAssistantMessageId: null,
      status: "error",
      statusMessage: message,
      runtime: {
        ...state.runtime,
        phase: "error",
        activeToolIds: [],
        activeAgentId: null,
        pendingInteraction: null
      },
      messages: trimMessages([
        ...markStreamingMessagesError(state.messages),
        createSystemMessage(systemErrorId, message, "error", createdAt)
      ])
    },
    createExecutionEvent(
      systemErrorId,
      "error",
      "本地错误",
      message,
      "error",
      createdAt,
      systemErrorId
    )
  );
}

function appendRuntimeEvent(state: AiPanelState, event: AiExecutionEvent): AiPanelState {
  return {
    ...state,
    executionEvents: appendExecutionEvent(state.executionEvents, event)
  };
}

function appendExecutionEvent(
  events: AiExecutionEvent[],
  event: AiExecutionEvent
): AiExecutionEvent[] {
  return trimExecutionEvents([...events, event]);
}

function upsertExecutionEvent(state: AiPanelState, event: AiExecutionEvent): AiPanelState {
  const existingEvent = state.executionEvents.find((item) => item.sourceId === event.sourceId);
  const nextEvents =
    existingEvent === undefined
      ? [...state.executionEvents, event]
      : state.executionEvents.map((item) => (item.sourceId === event.sourceId ? event : item));

  return {
    ...state,
    executionEvents: trimExecutionEvents(nextEvents)
  };
}

function upsertActivityCard(state: AiPanelState, card: AiActivityCard): AiPanelState {
  const existingCard = state.activityCards.find((item) => item.sourceId === card.sourceId);
  const nextCard =
    existingCard === undefined
      ? attachActivityCardAnchor(state, card)
      : {
          ...card,
          anchorMessageId: existingCard.anchorMessageId
        };
  const nextCards =
    existingCard === undefined
      ? [...state.activityCards, nextCard]
      : state.activityCards.map((item) => (item.sourceId === card.sourceId ? nextCard : item));

  return {
    ...state,
    activeAssistantMessageId: null,
    activityCards: trimActivityCards(nextCards)
  };
}

/**
 * 给工具/Todo/Agent 卡片绑定插入位置。
 *
 * 工作流：
 * 1. AI 正文流入当前 assistant message。
 * 2. 工具事件到达时，卡片锚定到这条 assistant message 后面。
 * 3. 随后把 activeAssistantMessageId 置空，让下一段正文自动变成新的 assistant message。
 */
function attachActivityCardAnchor(state: AiPanelState, card: AiActivityCard): AiActivityCard {
  const expectedPrefix = `assistant-${card.turnId}-`;
  const activeAnchor =
    state.activeAssistantMessageId !== null && state.activeAssistantMessageId.startsWith(expectedPrefix)
      ? state.activeAssistantMessageId
      : null;
  const fallbackSequence = Math.max(state.assistantMessageSequence, 1);
  const fallbackAnchor = `assistant-${card.turnId}-${fallbackSequence}`;
  const anchorMessageId = activeAnchor ?? fallbackAnchor;
  const messageExists = state.messages.some((message) => message.id === anchorMessageId);

  return {
    ...card,
    anchorMessageId: messageExists ? anchorMessageId : null
  };
}

function upsertToolSummary(state: AiPanelState, tool: AiToolSummary): AiPanelState {
  const existingIndex = state.toolSummaries.findIndex(
    (summary) => summary.toolUseId === tool.toolUseId
  );
  const nextTools =
    existingIndex === -1
      ? [...state.toolSummaries, tool]
      : state.toolSummaries.map((summary) =>
          summary.toolUseId === tool.toolUseId ? tool : summary
        );

  return {
    ...state,
    status: tool.status === "running" ? "running-tools" : state.status,
    statusMessage: tool.summary,
    toolSummaries: trimTools(nextTools)
  };
}

function mergeToolProgress(
  state: AiPanelState,
  toolUseId: string,
  toolName: string,
  progress: string
): AiPanelState {
  const existingTool = state.toolSummaries.find((tool) => tool.toolUseId === toolUseId);
  const nextTool: AiToolSummary = {
    toolUseId,
    name: toolName,
    status: "running",
    summary: progress || `正在执行 ${toolName}`,
    detail: progress,
    receipt: existingTool?.receipt ?? null
  };

  return upsertToolSummary(state, nextTool);
}

function upsertAgent(state: AiPanelState, agent: AiAgentSummary): AiPanelState {
  const existingAgent = state.agents.find((item) => item.agentId === agent.agentId);
  const nextAgents =
    existingAgent === undefined
      ? [...state.agents, agent]
      : state.agents.map((item) => (item.agentId === agent.agentId ? agent : item));

  return {
    ...state,
    agents: nextAgents
  };
}

function updateAgentAction(
  state: AiPanelState,
  agentId: string,
  currentAction: string
): AiPanelState {
  return updateAgent(state, agentId, { currentAction });
}

function updateAgent(
  state: AiPanelState,
  agentId: string,
  patch: Partial<AiAgentSummary>
): AiPanelState {
  const nextAgents = state.agents.map((agent) =>
    agent.agentId === agentId
      ? {
          ...agent,
          ...patch
        }
      : agent
  );

  return {
    ...state,
    agents: nextAgents
  };
}

function getStatusFromPhase(phase: AiRuntimePhase, activeToolCount: number): AiConversationStatus {
  if (
    phase === "execute_tools" ||
    phase === "wait_interaction" ||
    phase === "apply_interaction_result" ||
    phase === "collect_tool_results" ||
    activeToolCount > 0
  ) {
    return "running-tools";
  }

  if (phase === "complete") {
    return "completed";
  }

  if (phase === "error") {
    return "error";
  }

  return "streaming";
}

function getCompletedMessage(reason: string): string {
  if (reason === "completed") {
    return "AI 轮次已完成。";
  }

  if (reason === "max_turns") {
    return "AI 已达到最大轮次限制。";
  }

  if (reason === "stop_hook_prevented") {
    return "停止钩子阻止了最终完成。";
  }

  return `AI 轮次结束：${reason}`;
}

function buildTurnCompletedDetail(reason: string, messageCount: number | null): string {
  const parts = [`reason=${reason}`];
  if (messageCount !== null) {
    parts.push(`messages=${messageCount}`);
  }
  return parts.join(" · ");
}

function formatPermissionModeMessage(mode: AiPermissionMode): string {
  if (mode === "manual") {
    return "权限模式：手动确认。工具授权会等待用户处理。";
  }

  return "权限模式：自动处理。普通工具授权会自动放行。";
}

function formatContentBlockTitle(blockType: string): string {
  if (blockType === "tool_use") {
    return "模型开始生成工具调用";
  }

  if (blockType === "thinking") {
    return "模型 thinking block";
  }

  if (blockType === "text") {
    return "模型正文 block";
  }

  return "模型 content block";
}

function formatContentBlockDetail(toolName: string | null, toolUseId: string | null): string {
  if (toolName === null) {
    return "正文或 thinking 内容开始流式返回。";
  }

  return `${toolName} · ${toolUseId ?? "无 tool_use_id"}`;
}

function formatPhaseText(phase: AiRuntimePhase): string {
  const mapping: Record<AiRuntimePhase, string> = {
    idle: "待机",
    submitted: "已提交",
    ready: "就绪",
    prepare_turn: "准备轮次",
    stream_assistant: "模型流式输出",
    dispatch_tools: "派发工具",
    execute_tools: "执行工具",
    wait_interaction: "等待交互",
    apply_interaction_result: "应用交互结果",
    collect_tool_results: "收集工具结果",
    compact: "压缩上下文",
    stop_hooks: "停止钩子",
    complete: "完成",
    error: "错误",
    interrupted: "中断"
  };
  return mapping[phase];
}

function buildAgentDetail(agent: AiAgentSummary): string {
  const parts = [agent.agentType, agent.mode, agent.background ? "background" : "foreground"].filter(
    (part) => part.trim().length > 0
  );
  if (agent.taskId !== null) {
    parts.push(agent.taskId);
  }
  return parts.join(" · ");
}

function trimMessages(messages: AiConversationMessage[]): AiConversationMessage[] {
  return messages.slice(-MAX_VISIBLE_MESSAGES);
}

function trimTools(tools: AiToolSummary[]): AiToolSummary[] {
  return tools.slice(-MAX_VISIBLE_TOOL_SUMMARIES);
}

function trimExecutionEvents(events: AiExecutionEvent[]): AiExecutionEvent[] {
  return events.slice(-MAX_VISIBLE_EXECUTION_EVENTS);
}

function trimToolInputs(inputs: AiToolInputState[]): AiToolInputState[] {
  return inputs.slice(-MAX_VISIBLE_TOOL_INPUTS);
}

function trimActivityCards(cards: AiActivityCard[]): AiActivityCard[] {
  return cards.slice(-MAX_VISIBLE_ACTIVITY_CARDS);
}

function formatToolWorkTitle(toolName: string): string {
  // 直接用工具原名（Read / Glob / Bash ...），避免和后端 summary 形成中英混用。
  return toolName;
}

function formatSelectedSessionTitle(sessions: AiSessionInfo[], sessionId: string): string {
  const selectedSession = sessions.find((session) => session.sessionId === sessionId);
  if (selectedSession !== undefined) {
    return selectedSession.title;
  }

  return `会话 ${sessionId.slice(0, 8)}`;
}

function updateSessionTitle(
  sessions: AiSessionInfo[],
  sessionId: string,
  title: string
): AiSessionInfo[] {
  const existingSession = sessions.find((session) => session.sessionId === sessionId);

  if (existingSession === undefined) {
    return sessions;
  }

  return sessions.map((session) =>
    session.sessionId === sessionId
      ? {
          ...session,
          title
        }
      : session
  );
}

function buildSessionReadyMessage(session: AiSessionInfo | null): string {
  if (session === null) {
    return "当前工作区没有历史会话。下一条消息会创建一个新会话。";
  }

  return `已默认恢复最近会话「${session.title}」。下一条消息会续接该会话上下文。`;
}

function formatHistoryMessageTime(value: string | null, fallback: string): string {
  if (value === null) {
    return fallback;
  }

  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return fallback;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(timestamp));
}

function formatTodoStatusText(status: AiTodoItem["status"]): string {
  if (status === "completed") {
    return "完成";
  }
  if (status === "in_progress") {
    return "进行中";
  }
  return "待处理";
}

function formatCurrentTime(): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date());
}
