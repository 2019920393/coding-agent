export type AiTodoStatus = "pending" | "in_progress" | "completed";

export type AiToolExecutionStatus = "running" | "completed" | "error" | "cancelled";

export type AiAgentExecutionStatus = "running" | "completed" | "error";

export type AiContentBlockType = "text" | "thinking" | "tool_use" | "unknown";

export type AiPermissionMode = "auto" | "manual";

export type AiRuntimePhase =
  | "idle"
  | "submitted"
  | "ready"
  | "prepare_turn"
  | "stream_assistant"
  | "dispatch_tools"
  | "execute_tools"
  | "wait_interaction"
  | "apply_interaction_result"
  | "collect_tool_results"
  | "compact"
  | "stop_hooks"
  | "complete"
  | "error"
  | "interrupted";

export interface AiWorkspaceContext {
  workspaceName: string;
  workspacePath: string;
  activeFilePath: string | null;
  selectedPath: string | null;
  openFilePaths: string[];
}

export interface AiImageAttachment {
  base64: string;
  mimeType: string;
}

export interface AiSubmitMessageRequest extends AiWorkspaceContext {
  turnId: string;
  prompt: string;
  sessionId: string | null;
  permissionMode: AiPermissionMode;
  images?: AiImageAttachment[];
}

export interface AiSessionInfo {
  sessionId: string;
  title: string;
  createdAt: string | null;
  modifiedAt: string | null;
  messageCount: number;
  firstPrompt: string | null;
}

export interface AiListSessionsRequest {
  workspacePath: string;
}

export interface AiDeleteSessionRequest {
  workspacePath: string;
  sessionId: string;
}

export interface AiLoadSessionMessagesRequest {
  workspacePath: string;
  sessionId: string;
}

export type AiSessionMessageRole = "user" | "assistant";

export interface AiSessionMessage {
  id: string;
  role: AiSessionMessageRole;
  content: string;
  images: AiImageAttachment[];
  createdAt: string | null;
}

export interface AiCancelTurnRequest {
  turnId: string;
}

export type AiInteractionResponseValue = string | Record<string, string> | null;

export interface AiResolveInteractionRequest {
  requestId: string;
  data: AiInteractionResponseValue;
}

export interface AiSubmitCommand {
  type: "submit";
  request: AiSubmitMessageRequest;
}

export interface AiCancelCommand {
  type: "cancel";
  turnId: string;
}

export interface AiResetCommand {
  type: "reset";
}

export interface AiResolveInteractionCommand {
  type: "resolve-interaction";
  request: AiResolveInteractionRequest;
}

export type AiBridgeCommand =
  | AiSubmitCommand
  | AiCancelCommand
  | AiResetCommand
  | AiResolveInteractionCommand;

export interface AiCommandReceipt {
  kind: "command";
  summary: string;
  command: string;
  cwd: string;
  exitCode: number;
  stdout: string;
  stderr: string;
}

export interface AiDiffReceipt {
  kind: "diff";
  summary: string;
  path: string;
  diffText: string;
  changeId: string | null;
}

export type AiReceiptMetadataValue = string | number | boolean | null;

export interface AiGenericReceipt {
  kind: "generic";
  summary: string;
  body: string;
  metadata: Record<string, AiReceiptMetadataValue>;
}

export interface AiAgentReceipt {
  kind: "agent";
  summary: string;
  agentId: string;
  agentType: string;
  mode: string;
  taskId: string | null;
  background: boolean;
  status: string;
  resultPreview: string;
  totalTokens: number;
}

export interface AiUnknownReceipt {
  kind: "unknown";
  summary: string;
  body: string;
}

export type AiToolReceipt =
  | AiCommandReceipt
  | AiDiffReceipt
  | AiGenericReceipt
  | AiAgentReceipt
  | AiUnknownReceipt;

export interface AiToolSummary {
  toolUseId: string;
  name: string;
  status: AiToolExecutionStatus;
  summary: string;
  detail: string;
  receipt: AiToolReceipt | null;
}

export interface AiTodoItem {
  content: string;
  activeForm: string;
  status: AiTodoStatus;
}

export interface AiRuntimeMetadata {
  summary: string;
  reason: string | null;
  messageCount: number | null;
  toolCount: number | null;
  contentBlockCount: number | null;
}

export interface AiPendingInteraction {
  requestId: string;
  kind: string;
  label: string;
  toolName: string;
  toolInfo: string;
  message: string;
  questions: AiInteractionQuestion[];
  options: AiInteractionOption[];
  initialValue: string | null;
  payload: Record<string, string>;
}

export interface AiInteractionOption {
  value: string;
  label: string;
  description: string;
  preview: string;
}

export interface AiInteractionQuestion {
  questionId: string;
  header: string;
  question: string;
  options: AiInteractionOption[];
  multiSelect: boolean;
}

export interface AiAgentSummary {
  agentId: string;
  label: string;
  agentType: string;
  mode: string;
  background: boolean;
  status: AiAgentExecutionStatus;
  taskId: string | null;
  currentAction: string;
  resultPreview: string;
  totalTokens: number;
}

export interface AiBridgeReadyEvent {
  kind: "bridge-ready";
  workspacePath: string | null;
  sessionId: string | null;
  controlPort: number | null;
}

export interface AiSessionTitleUpdatedEvent {
  kind: "session-title-updated";
  workspacePath: string;
  sessionId: string;
  title: string;
}

export interface AiBridgeErrorEvent {
  kind: "bridge-error";
  message: string;
}

export interface AiTurnStartedEvent {
  kind: "turn-started";
  turnId: string;
  turnCount: number;
  messagesCount: number;
}

export interface AiRuntimeTurnCompletedEvent {
  kind: "turn-completed";
  turnId: string;
  reason: string;
  turnCount: number;
  messageCount: number | null;
}

export interface AiStatusChangedEvent {
  kind: "status-changed";
  turnId: string;
  phase: AiRuntimePhase;
  statusMessage: string;
  turnCount: number;
  checkpointId: string | null;
  activeToolIds: string[];
  activeAgentId: string | null;
  pendingInteraction: AiPendingInteraction | null;
  interruptReason: string | null;
  resumeTarget: string | null;
  metadata: AiRuntimeMetadata;
}

export interface AiStreamStartedEvent {
  kind: "stream-started";
  turnId: string;
}

export interface AiContentBlockStartedEvent {
  kind: "content-block-started";
  turnId: string;
  index: number;
  blockType: AiContentBlockType;
  toolUseId: string | null;
  toolName: string | null;
}

export interface AiContentBlockStoppedEvent {
  kind: "content-block-stopped";
  turnId: string;
  index: number;
}

export interface AiTextDeltaEvent {
  kind: "text-delta";
  turnId: string;
  index: number | null;
  delta: string;
}

export interface AiThinkingDeltaEvent {
  kind: "thinking-delta";
  turnId: string;
  index: number | null;
  delta: string;
}

export interface AiToolInputDeltaEvent {
  kind: "tool-input-delta";
  turnId: string;
  index: number;
  toolUseId: string | null;
  toolName: string | null;
  partialJson: string;
  accumulatedJson: string;
}

export interface AiToolStartedEvent {
  kind: "tool-started";
  turnId: string;
  toolUseId: string;
  toolName: string;
  inputPreview: string;
  status: "running";
}

export interface AiToolProgressEvent {
  kind: "tool-progress";
  turnId: string;
  toolUseId: string;
  toolName: string;
  progress: string;
}

export interface AiToolCompletedEvent {
  kind: "tool-completed";
  turnId: string;
  tool: AiToolSummary;
}

export interface AiToolResultEvent {
  kind: "tool-result";
  turnId: string;
  tool: AiToolSummary;
  isError: boolean;
}

export interface AiTodoUpdatedEvent {
  kind: "todo-updated";
  turnId: string;
  key: string;
  items: AiTodoItem[];
  toolUseId: string;
}

export interface AiCompactEvent {
  kind: "compact";
  turnId: string;
  preTokens: number;
  postTokens: number;
}

export interface AiAgentStartedEvent {
  kind: "agent-started";
  turnId: string;
  agent: AiAgentSummary;
}

export interface AiAgentDeltaEvent {
  kind: "agent-delta";
  turnId: string;
  agentId: string;
  taskId: string | null;
  contentDelta: string;
  thinkingDelta: string;
}

export interface AiAgentToolStartedEvent {
  kind: "agent-tool-started";
  turnId: string;
  agentId: string;
  taskId: string | null;
  toolUseId: string;
  toolName: string;
  inputPreview: string;
}

export interface AiAgentToolCompletedEvent {
  kind: "agent-tool-completed";
  turnId: string;
  agentId: string;
  taskId: string | null;
  tool: AiToolSummary;
}

export interface AiAgentCompletedEvent {
  kind: "agent-completed";
  turnId: string;
  agentId: string;
  taskId: string | null;
  result: string;
  status: "completed";
  totalTokens: number;
}

export interface AiAgentErrorEvent {
  kind: "agent-error";
  turnId: string;
  agentId: string;
  taskId: string | null;
  error: string;
  status: "error";
}

export interface AiMessageStopEvent {
  kind: "message-stop";
  turnId: string;
}

export interface AiTurnCompletedEvent {
  kind: "completed";
  turnId: string;
  reason: string;
  turnCount: number;
}

export interface AiInterruptAckEvent {
  kind: "interrupt-ack";
  turnId: string;
  reason: string;
  turnCount: number;
}

export interface AiErrorEvent {
  kind: "error";
  turnId: string;
  message: string;
  errorType: string | null;
  category: string | null;
  recoverable: boolean;
  retryAttempt: number | null;
  maxRetries: number | null;
}

export type AiBridgeEvent =
  | AiBridgeReadyEvent
  | AiSessionTitleUpdatedEvent
  | AiBridgeErrorEvent
  | AiTurnStartedEvent
  | AiRuntimeTurnCompletedEvent
  | AiStatusChangedEvent
  | AiStreamStartedEvent
  | AiContentBlockStartedEvent
  | AiContentBlockStoppedEvent
  | AiTextDeltaEvent
  | AiThinkingDeltaEvent
  | AiToolInputDeltaEvent
  | AiToolStartedEvent
  | AiToolProgressEvent
  | AiToolCompletedEvent
  | AiToolResultEvent
  | AiTodoUpdatedEvent
  | AiCompactEvent
  | AiAgentStartedEvent
  | AiAgentDeltaEvent
  | AiAgentToolStartedEvent
  | AiAgentToolCompletedEvent
  | AiAgentCompletedEvent
  | AiAgentErrorEvent
  | AiMessageStopEvent
  | AiTurnCompletedEvent
  | AiInterruptAckEvent
  | AiErrorEvent;
