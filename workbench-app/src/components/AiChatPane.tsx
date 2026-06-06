import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type {
  AiAgentSummary,
  AiInteractionQuestion,
  AiInteractionResponseValue,
  AiPermissionMode,
  AiPendingInteraction,
  AiReceiptMetadataValue,
  AiResolveInteractionRequest,
  AiSessionInfo,
  AiTodoStatus,
  AiToolSummary
} from "../../shared/aiProtocol";
import type {
  AiActivityCard,
  AiConversationMessage,
  AiPanelState,
  AiTodoGroup
} from "../state/aiState";

interface AiChatPaneProps {
  state: AiPanelState;
  onSendMessage: (content: string) => void;
  onCancelTurn: () => void;
  onResolveInteraction: (request: AiResolveInteractionRequest) => void;
  onOpenAgentTeam: () => void;
  onStartNewSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onChangePermissionMode: (mode: AiPermissionMode) => void;
  onDeleteSession?: (sessionId: string) => void;
}

type ConversationFeedItem =
  | { item: AiConversationMessage; kind: "message" }
  | { item: AiActivityCard; kind: "activity" };

interface TodoViewItem {
  key: string;
  content: string;
  activeForm: string;
  status: AiTodoStatus;
  displayStatus: AiTodoStatus;
}

/**
 * 右侧 AI 对话面板。
 *
 * 工作流：
 * 1. 用户消息、AI 回复、工具摘要卡片按线性顺序展示。
 * 2. 工具卡片默认只显示摘要，命令/回执/详细输出用 details 展开。
 * 3. Todo、Agent、人工交互只在运行时作为对话流里的临时块出现。
 * 4. AI 运行中，输入区的发送按钮会切换成停止按钮。
 */
export function AiChatPane({
  state,
  onSendMessage,
  onCancelTurn,
  onResolveInteraction,
  onOpenAgentTeam,
  onStartNewSession,
  onSelectSession,
  onChangePermissionMode,
  onDeleteSession
}: AiChatPaneProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const feedItems = buildConversationFeed(
    state.messages,
    state.activityCards.filter((card) => card.kind === "tool")
  );
  const isCancelling = state.status === "cancelling";
  const canSend =
    state.workspacePath !== null &&
    state.activeTurnId === null &&
    state.sessionStatus !== "loading" &&
    draft.trim().length > 0 &&
    !isCancelling;
  const canCancel = state.activeTurnId !== null && !isCancelling;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (canCancel) {
      onCancelTurn();
      return;
    }

    if (!canSend) {
      return;
    }

    onSendMessage(draft.trim());
    setDraft("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  useEffect(() => {
    const scrollElement = scrollRef.current;
    if (scrollElement === null) {
      return;
    }
    scrollElement.scrollTop = scrollElement.scrollHeight;
  });

  return (
    <aside className="ai-chat-pane" aria-label="Codo AI">
      <header className="codo-assistant-header">
        <div className="codo-assistant-header__identity">
          <span className="codo-assistant-header__mark" aria-hidden="true">
            C
          </span>
          <div>
            <h2>Codo</h2>
            <p>{formatHeaderSubtitle(state)}</p>
          </div>
        </div>
        <div className="codo-assistant-header__actions">
          <button
            className="codo-header-action"
            type="button"
            disabled={state.workspacePath === null || state.activeTurnId !== null}
            onClick={onStartNewSession}
          >
            新会话
          </button>
          <SessionHistoryMenu
            sessions={state.sessions}
            selectedSessionId={state.selectedSessionId}
            sessionStatus={state.sessionStatus}
            sessionMessage={state.sessionMessage}
            disabled={state.workspacePath === null || state.activeTurnId !== null}
            onSelectSession={onSelectSession}
            onDeleteSession={onDeleteSession}
          />
          <span className={`ai-status-pill ai-status-pill--${state.status}`}>
            {formatStatusText(state.status)}
          </span>
        </div>
      </header>

      <main className="codo-linear-panel codo-linear-panel--thread-only">
        <section className="codo-thread" aria-label="对话流">
          <div className="codo-thread__scroll" aria-live="polite" ref={scrollRef}>
            {feedItems.map((feedItem) =>
              feedItem.kind === "message" ? (
                <AiMessageBubble
                  key={feedItem.item.id}
                  message={feedItem.item}
                />
              ) : (
                <ActivityCard key={feedItem.item.id} card={feedItem.item} />
              )
            )}
            <InlineTodoBlock groups={state.todoGroups} />
            <InlineAgentTeam
              agents={state.agents}
              activeAgentId={state.runtime.activeAgentId}
              onOpenAgentTeam={onOpenAgentTeam}
            />
            <PendingInteractionCard
              interaction={state.runtime.pendingInteraction}
              onResolveInteraction={onResolveInteraction}
            />
          </div>
        </section>
      </main>

      <form className="ai-chat-pane__composer" onSubmit={handleSubmit}>
        <div className="ai-chat-pane__composer-box">
          <label className="ai-chat-pane__input-label">
            <textarea
              value={draft}
              rows={3}
              placeholder={
                state.workspacePath === null
                  ? "先选择工作区，再向 Codo 发起任务"
                  : "描述你要完成的任务，Enter 发送，Shift+Enter 换行"
              }
              disabled={state.workspacePath === null || state.activeTurnId !== null}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleKeyDown}
            />
          </label>
          <div className="ai-chat-pane__composer-toolbar">
            <PermissionModeSelect
              mode={state.permissionMode}
              disabled={state.activeTurnId !== null}
              onChangePermissionMode={onChangePermissionMode}
            />
            <span>{formatComposerHint(state, canCancel)}</span>
          </div>
        </div>
        <button
          className={
            canCancel ? "ai-chat-pane__send ai-chat-pane__send--stop" : "ai-chat-pane__send"
          }
          type="submit"
          disabled={!canSend && !canCancel}
          title={canCancel ? "中断当前 AI 回复" : "发送消息"}
        >
          {canCancel ? "停止" : "发送"}
        </button>
      </form>
    </aside>
  );
}

interface PermissionModeSelectProps {
  mode: AiPermissionMode;
  disabled: boolean;
  onChangePermissionMode: (mode: AiPermissionMode) => void;
}

/**
 * 权限模式选择器。
 *
 * 工作流：
 * 1. 自动：普通工具授权由 bridge 自动放行，适合连续执行。
 * 2. 手动：工具授权停下来等用户点击交互卡片，适合谨慎修改。
 * 3. 运行中的轮次不切换，避免当前 QueryEngine 权限状态和 UI 显示不一致。
 */
function PermissionModeSelect({
  mode,
  disabled,
  onChangePermissionMode
}: PermissionModeSelectProps) {
  return (
    <label className="codo-permission-select">
      <span>权限</span>
      <select
        value={mode}
        disabled={disabled}
        onChange={(event) => onChangePermissionMode(event.target.value as AiPermissionMode)}
      >
        <option value="auto">自动处理</option>
        <option value="manual">手动确认</option>
      </select>
    </label>
  );
}

interface SessionHistoryMenuProps {
  sessions: AiSessionInfo[];
  selectedSessionId: string | null;
  sessionStatus: AiPanelState["sessionStatus"];
  sessionMessage: string;
  disabled: boolean;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession?: (sessionId: string) => void;
}

/**
 * 当前工作区历史会话选择器。
 *
 * 工作流：
 * 1. App 在工作区变更后读取 sessions，并默认选择最近一条。
 * 2. 用户从这里切换历史会话，后续 prompt 会带着该 sessionId 进入 Python bridge。
 * 3. 这里不读取消息正文，只做运行时续接目标选择。
 */
function SessionHistoryMenu({
  sessions,
  selectedSessionId,
  sessionStatus,
  sessionMessage,
  disabled,
  onSelectSession,
  onDeleteSession
}: SessionHistoryMenuProps) {
  const [open, setOpen] = useState(false);
  const [hoveredSessionId, setHoveredSessionId] = useState<string | null>(null);

  useEffect(() => {
    setOpen(false);
  }, [sessions, selectedSessionId]);

  const selectedSession = sessions.find((session) => session.sessionId === selectedSessionId);
  const buttonTitle =
    selectedSession === undefined ? sessionMessage : cleanSessionTitle(selectedSession.title);

  const handleDeleteClick = (event: React.MouseEvent, sessionId: string) => {
    event.stopPropagation();
    if (onDeleteSession && confirm('确定要删除这个会话吗？此操作无法撤销。')) {
      onDeleteSession(sessionId);
    }
  };

  return (
    <div className="codo-session-menu">
      <button
        className="codo-session-menu__trigger"
        type="button"
        disabled={disabled || sessionStatus === "loading"}
        onClick={() => setOpen(!open)}
        title={buttonTitle}
      >
        恢复
      </button>
      {open ? (
        <section className="codo-session-menu__panel" aria-label="历史会话">
          <div className="codo-session-menu__header">
            <strong>历史会话</strong>
            <span>{formatSessionStatusText(sessionStatus, sessions.length)}</span>
          </div>
          {sessions.length === 0 ? (
            <p className="codo-session-menu__empty">{sessionMessage}</p>
          ) : (
            <div className="codo-session-menu__list">
              {sessions.map((session) => (
                <div
                  className="codo-session-menu__item-wrapper"
                  key={session.sessionId}
                  onMouseEnter={() => setHoveredSessionId(session.sessionId)}
                  onMouseLeave={() => setHoveredSessionId(null)}
                >
                  <button
                    className={
                      session.sessionId === selectedSessionId
                        ? "codo-session-menu__item codo-session-menu__item--active"
                        : "codo-session-menu__item"
                    }
                    type="button"
                    onClick={() => {
                      onSelectSession(session.sessionId);
                      setOpen(false);
                    }}
                  >
                    <span>{cleanSessionTitle(session.title)}</span>
                    <em>
                      {formatSessionModifiedAt(session.modifiedAt)} · {session.messageCount} 条消息
                    </em>
                  </button>
                  {hoveredSessionId === session.sessionId && onDeleteSession && (
                    <button
                      className="codo-session-menu__delete"
                      type="button"
                      onClick={(e) => handleDeleteClick(e, session.sessionId)}
                      title="删除会话"
                      aria-label="删除会话"
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}

interface InlineAgentTeamProps {
  agents: AiAgentSummary[];
  activeAgentId: string | null;
  onOpenAgentTeam: () => void;
}

/**
 * 对话流里的 Agent Team 运行块。
 *
 * 工作流：
 * 1. Python runtime 发出 agent_started / agent_delta / agent_completed 等事件。
 * 2. reducer 将这些事件合并到 state.agents。
 * 3. 这里只在有真实运行数据时展示紧凑摘要，不做常驻 dashboard。
 */
function InlineAgentTeam({ agents, activeAgentId, onOpenAgentTeam }: InlineAgentTeamProps) {
  if (agents.length === 0) {
    return null;
  }

  const runningCount = agents.filter((agent) => agent.status === "running").length;
  const completedCount = agents.filter((agent) => agent.status === "completed").length;
  const errorCount = agents.filter((agent) => agent.status === "error").length;
  const focusedAgent =
    agents.find((agent) => agent.agentId === activeAgentId) ??
    agents.find((agent) => agent.status === "running") ??
    agents[0];

  return (
    <section className="codo-inline-agent" aria-label="Agent Team">
      <div className="codo-inline-agent__header">
        <div>
          <h3>Agent Team</h3>
          <p>{formatAgentTeamSummary(runningCount, completedCount, errorCount)}</p>
        </div>
        <button type="button" onClick={onOpenAgentTeam} title="在中间工作区打开 Agent Team">
          打开
        </button>
      </div>
      <div className="codo-inline-agent__focus">
        <AgentRuntimeItem
          agent={focusedAgent}
          active={focusedAgent.agentId === activeAgentId || focusedAgent.status === "running"}
        />
      </div>
      {agents.length > 1 ? (
        <details className="codo-inline-agent__details">
          <summary>查看全部 {agents.length} 个 Agent</summary>
          <div className="codo-inline-agent__list">
            {agents
              .filter((agent) => agent.agentId !== focusedAgent.agentId)
              .map((agent) => (
                <AgentRuntimeItem
                  active={agent.agentId === activeAgentId}
                  agent={agent}
                  key={agent.agentId}
                />
              ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}

interface AgentRuntimeItemProps {
  agent: AiAgentSummary;
  active: boolean;
}

function AgentRuntimeItem({ agent, active }: AgentRuntimeItemProps) {
  return (
    <article
      className={[
        "codo-inline-agent-item",
        `codo-inline-agent-item--${agent.status}`,
        active ? "codo-inline-agent-item--active" : ""
      ]
        .filter((className) => className.length > 0)
        .join(" ")}
    >
      <span className="codo-inline-agent-item__node" aria-hidden="true" />
      <div className="codo-inline-agent-item__body">
        <div className="codo-inline-agent-item__top">
          <strong>{formatAgentLabel(agent)}</strong>
          <em>{formatAgentStatusText(agent.status)}</em>
        </div>
        <div className="codo-inline-agent-item__meta">
          <span>{formatAgentType(agent)}</span>
          <span>{formatAgentMode(agent)}</span>
          {agent.background ? <span>后台</span> : <span>前台</span>}
        </div>
        <p>{formatAgentAction(agent)}</p>
        {agent.resultPreview.trim().length > 0 ? <code>{agent.resultPreview}</code> : null}
      </div>
    </article>
  );
}

interface PendingInteractionCardProps {
  interaction: AiPendingInteraction | null;
  onResolveInteraction: (request: AiResolveInteractionRequest) => void;
}

function PendingInteractionCard({
  interaction,
  onResolveInteraction
}: PendingInteractionCardProps) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [activeQuestionIndex, setActiveQuestionIndex] = useState(0);

  useEffect(() => {
    setAnswers({});
    setActiveQuestionIndex(0);
  }, [interaction]);

  if (interaction === null) {
    return null;
  }

  const hasQuestions = interaction.questions.length > 0;
  const questionCount = interaction.questions.length;
  const visibleQuestionIndex = hasQuestions
    ? Math.min(activeQuestionIndex, questionCount - 1)
    : 0;
  const activeQuestion = hasQuestions
    ? interaction.questions[visibleQuestionIndex] ?? interaction.questions[0]
    : null;
  const activeAnswer =
    activeQuestion === null ? "" : answers[activeQuestion.question] ?? "";
  const activeQuestionAnswered = activeAnswer.trim().length > 0;
  const activeQuestionNumber = visibleQuestionIndex + 1;
  const isFirstQuestion = visibleQuestionIndex === 0;
  const isLastQuestion = visibleQuestionIndex === questionCount - 1;
  const canSubmitQuestions =
    hasQuestions &&
    interaction.questions.every((question) => {
      const answer = answers[question.question];
      return typeof answer === "string" && answer.trim().length > 0;
    });

  const resolve = (data: AiInteractionResponseValue) => {
    onResolveInteraction({
      requestId: interaction.requestId,
      data
    });
  };

  const updateQuestionAnswer = (question: AiInteractionQuestion, optionValue: string) => {
    setAnswers({
      ...answers,
      [question.question]: updateInteractionAnswer(
        answers[question.question] ?? "",
        optionValue,
        question.multiSelect
      )
    });
  };

  const goToPreviousQuestion = () => {
    setActiveQuestionIndex(Math.max(visibleQuestionIndex - 1, 0));
  };

  const goToNextQuestion = () => {
    if (!activeQuestionAnswered) {
      return;
    }

    setActiveQuestionIndex(Math.min(visibleQuestionIndex + 1, questionCount - 1));
  };

  return (
    <section className={`codo-interaction-card codo-interaction-card--${interaction.kind}`}>
      <div className="codo-interaction-card__header">
        <div>
          <span>{formatInteractionKind(interaction.kind)}</span>
          <h3>{interaction.label || "需要你的决定"}</h3>
        </div>
        <em>等待输入</em>
      </div>

      {interaction.message.trim().length > 0 ? <p>{interaction.message}</p> : null}
      {interaction.toolInfo.trim().length > 0 ? <code>{interaction.toolInfo}</code> : null}

      {hasQuestions ? (
        <div className="codo-interaction-card__questions">
          <QuestionProgress
            answers={answers}
            activeQuestionIndex={visibleQuestionIndex}
            questions={interaction.questions}
            onSelectQuestion={setActiveQuestionIndex}
          />
          {activeQuestion !== null ? (
            <fieldset className="codo-interaction-question" key={activeQuestion.questionId}>
              <legend>{activeQuestion.header || activeQuestion.question}</legend>
              <div className="codo-interaction-question__top">
                <p>{activeQuestion.question}</p>
                <span>
                  {activeQuestionNumber} / {questionCount}
                </span>
              </div>
              <div className="codo-interaction-question__options">
                {activeQuestion.options.map((option) => {
                  const active = isInteractionOptionSelected(
                    activeAnswer,
                    option.value,
                    activeQuestion.multiSelect
                  );
                  return (
                    <button
                      className={active ? "codo-interaction-option codo-interaction-option--active" : "codo-interaction-option"}
                      key={option.value}
                      type="button"
                      onClick={() => updateQuestionAnswer(activeQuestion, option.value)}
                    >
                      <strong>{option.label}</strong>
                      {option.description.trim().length > 0 ? <span>{option.description}</span> : null}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          ) : null}
          <div className="codo-interaction-card__actions">
            <button type="button" onClick={() => resolve(null)}>
              取消
            </button>
            <button type="button" disabled={isFirstQuestion} onClick={goToPreviousQuestion}>
              上一步
            </button>
            {isLastQuestion ? (
              <button type="button" disabled={!canSubmitQuestions} onClick={() => resolve(answers)}>
                提交答案
              </button>
            ) : (
              <button type="button" disabled={!activeQuestionAnswered} onClick={goToNextQuestion}>
                下一题
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="codo-interaction-card__actions">
          {interaction.options.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => resolve(option.value)}
              title={option.description || option.label}
            >
              {formatInteractionOptionLabel(option.label)}
            </button>
          ))}
          {interaction.options.length === 0 ? (
            <>
              <button type="button" onClick={() => resolve(null)}>
                取消
              </button>
              <button type="button" onClick={() => resolve(interaction.initialValue ?? "")}>
                继续
              </button>
            </>
          ) : null}
        </div>
      )}
    </section>
  );
}

interface QuestionProgressProps {
  questions: AiInteractionQuestion[];
  answers: Record<string, string>;
  activeQuestionIndex: number;
  onSelectQuestion: (index: number) => void;
}

/**
 * 多问题交互进度条。
 *
 * 工作流：
 * 1. 只显示每道题的序号和回答状态，不展开问题正文。
 * 2. 用户可以回到前面已回答的问题修改答案。
 * 3. 真正提交时仍由 PendingInteractionCard 汇总 answers。
 */
function QuestionProgress({
  questions,
  answers,
  activeQuestionIndex,
  onSelectQuestion
}: QuestionProgressProps) {
  return (
    <div className="codo-interaction-progress" aria-label="问题进度">
      {questions.map((question, index) => {
        const answered = (answers[question.question] ?? "").trim().length > 0;
        const className = [
          "codo-interaction-progress__step",
          index === activeQuestionIndex ? "codo-interaction-progress__step--active" : "",
          answered ? "codo-interaction-progress__step--answered" : ""
        ]
          .filter((item) => item.length > 0)
          .join(" ");

        return (
          <button
            className={className}
            key={question.questionId}
            type="button"
            onClick={() => onSelectQuestion(index)}
            title={question.header || question.question}
          >
            {index + 1}
          </button>
        );
      })}
    </div>
  );
}

function isInteractionOptionSelected(
  currentAnswer: string,
  optionValue: string,
  multiSelect: boolean
): boolean {
  if (!multiSelect) {
    return currentAnswer === optionValue;
  }

  return splitMultiSelectAnswer(currentAnswer).includes(optionValue);
}

function updateInteractionAnswer(
  currentAnswer: string,
  optionValue: string,
  multiSelect: boolean
): string {
  if (!multiSelect) {
    return optionValue;
  }

  const selectedValues = splitMultiSelectAnswer(currentAnswer);
  if (selectedValues.includes(optionValue)) {
    return selectedValues.filter((value) => value !== optionValue).join(", ");
  }

  return [...selectedValues, optionValue].join(", ");
}

function splitMultiSelectAnswer(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

interface InlineTodoBlockProps {
  groups: AiTodoGroup[];
}

/**
 * 对话流里的 Todo 运行块。
 *
 * 工作流：
 * 1. 没有 Todo 数据时不渲染，避免右侧出现常驻任务板。
 * 2. 运行中和已完成状态用同一个列表表达，让完成项自然划掉。
 * 3. 真正的任务事实只来自 codo runtime 的 `todo-updated` 事件。
 */
function InlineTodoBlock({ groups }: InlineTodoBlockProps) {
  const todos = buildTodoViewItems(groups);
  const pendingCount = todos.filter((todo) => todo.displayStatus === "pending").length;
  const activeCount = todos.filter((todo) => todo.displayStatus === "in_progress").length;
  const completedCount = todos.filter((todo) => todo.displayStatus === "completed").length;
  const progressPercent = Math.round((completedCount / todos.length) * 100);
  const activeTodo = todos.find((todo) => todo.displayStatus === "in_progress") ?? null;

  if (todos.length === 0) {
    return null;
  }

  return (
    <section className="codo-inline-todo" aria-label="Todo 任务板">
      <div className="codo-inline-todo__header">
        <div>
          <h3>Todo</h3>
          <p>{formatTodoQueueSummary(activeTodo, pendingCount, activeCount)}</p>
        </div>
        <span className="codo-inline-todo__count">
          {completedCount}/{todos.length}
        </span>
      </div>
      <div className="codo-inline-todo__progress" aria-label="Todo 完成进度">
        <span style={{ width: `${progressPercent}%` }} />
      </div>
      <div className="codo-inline-todo__list">
        {todos.map((todo) => (
          <article
            className={`codo-inline-todo-item codo-inline-todo-item--${todo.displayStatus}`}
            key={todo.key}
          >
            <span className="codo-inline-todo-item__mark" aria-hidden="true" />
            <div>
              <strong>{todo.content}</strong>
              <p>{todo.activeForm || formatTodoStatusText(todo.displayStatus)}</p>
            </div>
            <em>{formatTodoStatusText(todo.displayStatus)}</em>
          </article>
        ))}
      </div>
    </section>
  );
}

function formatTodoQueueSummary(
  activeTodo: TodoViewItem | null,
  pendingCount: number,
  activeCount: number
): string {
  if (activeTodo !== null) {
    return `正在执行：${activeTodo.activeForm || activeTodo.content}`;
  }

  if (pendingCount === 0 && activeCount === 0) {
    return "任务已全部完成";
  }

  return `${activeCount} 个进行中 · ${pendingCount} 个待处理`;
}

/**
 * 将后端 Todo 状态转成运行队列视图。
 *
 * 工作流：
 * 1. 如果后端已经给出 `in_progress`，直接尊重真实状态。
 * 2. 如果后端只给出 completed / pending，则把第一个未完成项显示为当前执行项。
 * 3. 这里只改变展示状态，不改写 codo runtime 的原始 Todo 数据。
 */
function buildTodoViewItems(groups: AiTodoGroup[]): TodoViewItem[] {
  const baseItems: TodoViewItem[] = groups.flatMap((group) =>
    group.items.map((todo, index) => ({
      key: `${group.key}-${index}`,
      content: todo.content,
      activeForm: todo.activeForm,
      status: todo.status,
      displayStatus: todo.status
    }))
  );
  const hasExplicitActiveItem = baseItems.some((todo) => todo.status === "in_progress");

  if (hasExplicitActiveItem) {
    return baseItems;
  }

  const nextActiveIndex = baseItems.findIndex((todo) => todo.status !== "completed");
  if (nextActiveIndex === -1) {
    return baseItems;
  }

  return baseItems.map((todo, index) =>
    index === nextActiveIndex
      ? {
          ...todo,
          displayStatus: "in_progress"
        }
      : todo
  );
}

interface AiMessageBubbleProps {
  message: AiConversationMessage;
}

function AiMessageBubble({ message }: AiMessageBubbleProps) {
  return (
    <article
      className={`ai-message ai-message--${message.role} ai-message--${message.status}`}
    >
      <div className="ai-message__meta">
        <span>{formatRoleText(message.role)}</span>
        <time>{message.createdAt}</time>
      </div>
      <div className="ai-message__bubble">
        {message.content || placeholderForMessage(message)}
      </div>
    </article>
  );
}

interface ActivityCardProps {
  card: AiActivityCard;
}

function ActivityCard({ card }: ActivityCardProps) {
  const previewText = buildActivityPreviewText(card);
  const canShowDetails = card.detail.trim().length > 0 || card.receipt !== null;
  // 已完成的工具卡折叠成单行：summary 已说明做了什么，预览行（命令/路径）收进 details，
  // 长任务里几十张卡才不会被预览行撑高。仍在跑或出错的卡保留预览行，便于即时判断。
  const showSubline = previewText !== null && card.status !== "completed";

  // 根据工具类型添加颜色编码类
  const toolColorClass =
    card.kind === "tool" ? getToolColorClass(card.title) : "";

  const className = [
    "codo-activity-card",
    `codo-activity-card--${card.kind}`,
    `codo-activity-card--${card.status}`,
    card.kind === "tool" ? "codo-activity-card--compact" : "",
    toolColorClass
  ]
    .filter((item) => item.length > 0)
    .join(" ");

  return (
    <article className={className}>
      <span className="codo-activity-card__icon" aria-hidden="true">
        {formatActivityIcon(card.kind, card.title)}
      </span>
      <div className="codo-activity-card__content">
        <div className="codo-activity-card__top">
          <strong>{card.title}</strong>
          <span>{card.summary}</span>
          {card.status !== "completed" ? (
            <em>{formatActivityStatus(card.status)}</em>
          ) : null}
        </div>
        {showSubline ? (
          <div className="codo-activity-card__subline">
            <code>{previewText}</code>
          </div>
        ) : null}
        {canShowDetails ? (
          <details className="codo-activity-card__details">
            <summary>{formatActivityDetailsLabel(card)}</summary>
            {card.detail.trim().length > 0 ? <pre>{card.detail}</pre> : null}
            {card.receipt !== null ? <pre>{formatReceiptText(card.receipt)}</pre> : null}
          </details>
        ) : null}
      </div>
    </article>
  );
}

function buildConversationFeed(
  messages: AiConversationMessage[],
  cards: AiActivityCard[]
): ConversationFeedItem[] {
  const feedItems: ConversationFeedItem[] = [];
  const usedCardIds = new Set<string>();
  const cardsByAnchor = groupActivityCardsByAnchor(cards);

  for (const message of messages) {
    feedItems.push({ item: message, kind: "message" });

    const anchoredCards = cardsByAnchor.get(message.id) ?? [];
    for (const card of anchoredCards) {
      feedItems.push({ item: card, kind: "activity" });
      usedCardIds.add(card.id);
    }
  }

  for (const card of cards) {
    if (!usedCardIds.has(card.id)) {
      feedItems.push({ item: card, kind: "activity" });
    }
  }

  return feedItems;
}

/**
 * 按 assistant message id 分组工具卡。
 *
 * 工作流：
 * 1. reducer 在工具事件到达时写入 anchorMessageId。
 * 2. UI 渲染时只按 anchor 分组，不再靠 turnId 猜测位置。
 * 3. 没有 anchor 的历史卡片会在对话末尾兜底展示，避免丢信息。
 */
function groupActivityCardsByAnchor(cards: AiActivityCard[]): Map<string, AiActivityCard[]> {
  const groups = new Map<string, AiActivityCard[]>();

  for (const card of cards) {
    if (card.anchorMessageId === null) {
      continue;
    }

    const group = groups.get(card.anchorMessageId) ?? [];
    group.push(card);
    groups.set(card.anchorMessageId, group);
  }

  return groups;
}

function placeholderForMessage(message: AiConversationMessage): string {
  if (message.status === "streaming") {
    return "Codo 正在回复...";
  }

  if (message.status === "error") {
    return "消息生成失败。";
  }

  return "";
}

function formatRoleText(role: AiConversationMessage["role"]): string {
  if (role === "assistant") {
    return "Codo";
  }

  if (role === "user") {
    return "你";
  }

  return "系统";
}

function formatStatusText(status: AiPanelState["status"]): string {
  switch (status) {
    case "idle":
      return "待机";
    case "ready":
      return "就绪";
    case "streaming":
      return "回复中";
    case "running-tools":
      return "处理中";
    case "completed":
      return "完成";
    case "cancelling":
      return "停止中";
    case "error":
      return "异常";
  }
}

function formatHeaderSubtitle(state: AiPanelState): string {
  if (state.workspaceName === null) {
    return "选择工作区后开始";
  }

  const selectedSession = state.sessions.find(
    (session) => session.sessionId === state.selectedSessionId
  );

  if (selectedSession !== undefined) {
    return `${state.workspaceName} · ${cleanSessionTitle(selectedSession.title)}`;
  }

  return state.sessionMessage;
}

/**
 * 后端的标题生成偶尔会回吐 ```json {"title": "..."} ``` 这类未解析的 LLM 输出。
 * 这里在前端兜底：剥掉围栏、抽出 title 字段，失败就回到「未命名会话」。
 */
function cleanSessionTitle(title: string): string {
  const fallback = "未命名会话";
  const trimmed = title.trim();
  if (trimmed.length === 0) {
    return fallback;
  }

  const fenceStripped = trimmed
    .replace(/^```(?:json|md|markdown)?\s*/i, "")
    .replace(/```\s*$/i, "")
    .trim();

  if (fenceStripped.startsWith("{")) {
    try {
      const parsed = JSON.parse(fenceStripped) as unknown;
      if (parsed !== null && typeof parsed === "object" && "title" in parsed) {
        const candidate = (parsed as { title: unknown }).title;
        if (typeof candidate === "string" && candidate.trim().length > 0) {
          return candidate.trim();
        }
      }
    } catch {
      // 解析失败说明不是真的 JSON，继续用截断后的原文。
    }
  }

  return fenceStripped.length > 0 ? fenceStripped : fallback;
}

function formatComposerHint(state: AiPanelState, canCancel: boolean): string {
  if (canCancel) {
    return "AI 正在运行，可随时停止";
  }

  if (state.workspacePath === null) {
    return "请先选择工作区";
  }

  if (state.sessionStatus === "loading") {
    return "正在读取历史会话";
  }

  return "Enter 发送 · Shift+Enter 换行";
}

function formatSessionStatusText(
  status: AiPanelState["sessionStatus"],
  sessionCount: number
): string {
  if (status === "loading") {
    return "读取中";
  }

  if (status === "error") {
    return "读取失败";
  }

  return `${sessionCount} 条`;
}

function formatSessionModifiedAt(value: string | null): string {
  if (value === null) {
    return "未知时间";
  }

  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(timestamp));
}

function formatInteractionKind(kind: string): string {
  if (kind === "permission") {
    return "工具授权";
  }

  if (kind === "diff_review") {
    return "变更审阅";
  }

  if (kind === "question") {
    return "问题确认";
  }

  return "用户交互";
}

function formatInteractionOptionLabel(label: string): string {
  const mapping: Record<string, string> = {
    "Allow Once": "本次允许",
    "Allow Session": "本会话允许",
    Deny: "拒绝",
    Abort: "中止",
    Accept: "接受",
    Reject: "拒绝"
  };

  return mapping[label] ?? label;
}

function formatActivityIcon(kind: AiActivityCard["kind"], title?: string): string {
  if (kind === "tool") {
    // 根据工具名称显示不同图标
    const toolName = title?.toLowerCase() || "";

    if (toolName.includes("read") || toolName.includes("读取")) {
      return "📖";
    }
    if (toolName.includes("edit") || toolName.includes("编辑")) {
      return "✏️";
    }
    if (toolName.includes("write") || toolName.includes("写入")) {
      return "📝";
    }
    if (toolName.includes("bash") || toolName.includes("命令") || toolName.includes("shell")) {
      return "⚡";
    }
    if (toolName.includes("grep") || toolName.includes("搜索") || toolName.includes("search")) {
      return "🔍";
    }
    if (toolName.includes("glob") || toolName.includes("文件")) {
      return "📁";
    }
    if (toolName.includes("agent") || toolName.includes("代理")) {
      return "🤖";
    }
    if (toolName.includes("web") || toolName.includes("网络")) {
      return "🌐";
    }

    return "⚙️";
  }

  if (kind === "todo") {
    return "✓";
  }

  return "@";
}

function getToolColorClass(title: string): string {
  const toolName = title.toLowerCase();

  if (toolName.includes("read") || toolName.includes("读取")) {
    return "codo-tool--read";
  }
  if (toolName.includes("edit") || toolName.includes("编辑")) {
    return "codo-tool--edit";
  }
  if (toolName.includes("write") || toolName.includes("写入")) {
    return "codo-tool--write";
  }
  if (toolName.includes("bash") || toolName.includes("命令") || toolName.includes("shell")) {
    return "codo-tool--bash";
  }
  if (toolName.includes("grep") || toolName.includes("搜索") || toolName.includes("search")) {
    return "codo-tool--search";
  }
  if (toolName.includes("glob") || toolName.includes("文件")) {
    return "codo-tool--file";
  }
  if (toolName.includes("agent") || toolName.includes("代理")) {
    return "codo-tool--agent";
  }
  if (toolName.includes("web") || toolName.includes("网络")) {
    return "codo-tool--web";
  }

  return "codo-tool--default";
}

/**
 * 生成工具卡片的一行预览。
 *
 * 工作流：
 * 1. 命令工具显示实际命令，方便用户快速判断 AI 执行了什么。
 * 2. diff 工具显示目标文件路径，避免默认摘要过于抽象。
 * 3. 其他工具不额外展示预览，保持卡片紧凑。
 */
function buildActivityPreviewText(card: AiActivityCard): string | null {
  if (card.receipt?.kind === "command" && card.receipt.command.trim().length > 0) {
    return `$ ${card.receipt.command}`;
  }

  if (card.receipt?.kind === "diff" && card.receipt.path.trim().length > 0) {
    return card.receipt.path;
  }

  if (card.receipt?.kind === "generic") {
    const metadata = card.receipt.metadata;
    const filePath = getMetadataString(metadata.filePath);
    if (filePath !== null) {
      return filePath;
    }

    const pattern = getMetadataString(metadata.pattern);
    const searchPath = getMetadataString(metadata.path);
    if (pattern !== null) {
      return searchPath !== null ? `${pattern} @ ${searchPath}` : pattern;
    }

    const commandName = getMetadataString(metadata.commandName);
    if (commandName !== null) {
      return `/${commandName}`;
    }
  }

  return null;
}

function getMetadataString(value: AiReceiptMetadataValue | undefined): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function formatMetadataValue(key: string, value: AiReceiptMetadataValue): string {
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }

  if (typeof value === "number") {
    if (key === "durationMs") {
      return `${value}ms`;
    }

    if (key === "sizeBytes") {
      return formatByteSize(value);
    }

    return `${value}`;
  }

  return value ?? "";
}

function formatByteSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes}B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)}KB`;
  }

  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function formatActivityKind(kind: AiActivityCard["kind"]): string {
  if (kind === "tool") {
    return "工具调用";
  }
  if (kind === "todo") {
    return "任务更新";
  }
  return "Agent";
}

function formatAgentLabel(agent: AiAgentSummary): string {
  const label = agent.label.trim();
  if (label.length > 0) {
    return label;
  }

  const agentType = agent.agentType.trim();
  if (agentType.length > 0) {
    return agentType;
  }

  return agent.agentId;
}

function formatAgentType(agent: AiAgentSummary): string {
  return agent.agentType.trim().length > 0 ? agent.agentType : "通用";
}

function formatAgentMode(agent: AiAgentSummary): string {
  return agent.mode.trim().length > 0 ? agent.mode : "默认模式";
}

function formatAgentTeamSummary(
  runningCount: number,
  completedCount: number,
  errorCount: number
): string {
  const parts = [`运行 ${runningCount}`, `完成 ${completedCount}`];
  if (errorCount > 0) {
    parts.push(`异常 ${errorCount}`);
  }

  return parts.join(" · ");
}

function formatAgentAction(agent: AiAgentSummary): string {
  const currentAction = agent.currentAction.trim();
  if (currentAction.length > 0) {
    return currentAction;
  }

  if (agent.status === "running") {
    return "等待子任务输出...";
  }

  if (agent.status === "completed") {
    return "子任务已完成。";
  }

  return "子任务执行异常。";
}

function formatAgentStatusText(status: AiAgentSummary["status"]): string {
  if (status === "running") {
    return "运行中";
  }

  if (status === "completed") {
    return "已完成";
  }

  return "异常";
}

function formatActivityStatus(status: AiActivityCard["status"]): string {
  switch (status) {
    case "running":
      return "进行中";
    case "completed":
      return "完成";
    case "error":
      return "失败";
    case "pending":
      return "等待";
  }
}

function formatActivityDetailsLabel(card: AiActivityCard): string {
  if (card.receipt?.kind === "command") {
    return "查看命令和输出";
  }

  if (card.receipt?.kind === "diff") {
    return "查看文件变更";
  }

  if (card.kind === "todo") {
    return "查看任务详情";
  }

  return "查看详情";
}

function formatTodoStatusText(status: AiTodoStatus): string {
  if (status === "in_progress") {
    return "进行中";
  }

  if (status === "completed") {
    return "已完成";
  }

  return "待处理";
}

function formatReceiptText(receipt: NonNullable<AiToolSummary["receipt"]>): string {
  switch (receipt.kind) {
    case "command":
      return `命令: ${receipt.command}\n目录: ${receipt.cwd || "当前工作区"}\n退出码: ${receipt.exitCode}\n\nstdout:\n${receipt.stdout}\n\nstderr:\n${receipt.stderr}`;
    case "diff":
      return `文件: ${receipt.path}\n变更: ${receipt.changeId ?? "无"}\n\n${receipt.diffText}`;
    case "generic":
      return formatGenericReceiptText(receipt);
    case "agent":
      return `Agent: ${receipt.agentType} / ${receipt.mode}\n任务: ${receipt.taskId ?? "无"}\n状态: ${receipt.status}\n\n${receipt.resultPreview}`;
    case "unknown":
      return receipt.body;
  }
}

function formatGenericReceiptText(receipt: Extract<NonNullable<AiToolSummary["receipt"]>, { kind: "generic" }>): string {
  const metadataLines = Object.entries(receipt.metadata).map(
    ([key, value]) => `${key}: ${formatMetadataValue(key, value)}`
  );
  const sections = [
    receipt.summary.trim(),
    metadataLines.length > 0 ? metadataLines.join("\n") : "",
    receipt.body.trim()
  ];

  return sections.filter((section) => section.length > 0).join("\n\n");
}
