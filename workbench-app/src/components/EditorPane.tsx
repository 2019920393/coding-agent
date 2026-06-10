import Editor from "@monaco-editor/react";
import type { EditorProps, OnMount } from "@monaco-editor/react";
import type { AiAgentSummary } from "../../shared/aiProtocol";
import type { EditorCursorPosition, EditorView, OpenFile } from "../types";

interface EditorPaneProps {
  editorView: EditorView;
  openFiles: OpenFile[];
  activeFile: OpenFile | null;
  activeFilePath: string | null;
  agents: AiAgentSummary[];
  activeAgentId: string | null;
  isActiveFileDirty: boolean;
  isSaving: boolean;
  onSelectFileTab: (path: string) => void;
  onCloseFileTab: (path: string) => void;
  onChangeFileContent: (path: string, content: string) => void;
  onCursorPositionChange: (position: EditorCursorPosition) => void;
  onSaveFile: () => void;
}

const MONACO_EDITOR_OPTIONS: NonNullable<EditorProps["options"]> = {
  automaticLayout: true,
  fontSize: 14,
  minimap: { enabled: false },
  readOnly: false,
  renderWhitespace: "selection",
  scrollBeyondLastLine: false,
  tabSize: 2,
  wordWrap: "off"
};

/**
 * 中间编辑器面板。
 *
 * 工作流：
 * 1. 没有打开文件时显示欢迎空态。
 * 2. 有打开文件时渲染全部 tab，并显示当前激活文件。
 * 3. Monaco 内容变化后交给上层状态管理，保存和关闭动作由上层处理。
 */
export function EditorPane({
  editorView,
  openFiles,
  activeFile,
  activeFilePath,
  agents,
  activeAgentId,
  isActiveFileDirty,
  isSaving,
  onSelectFileTab,
  onCloseFileTab,
  onChangeFileContent,
  onCursorPositionChange,
  onSaveFile
}: EditorPaneProps) {
  const handleEditorMount: OnMount = (editor) => {
    const reportCursorPosition = (): void => {
      const position = editor.getPosition();

      if (position === null) {
        return;
      }

      onCursorPositionChange({
        lineNumber: position.lineNumber,
        column: position.column
      });
    };

    reportCursorPosition();
    editor.onDidChangeCursorPosition(reportCursorPosition);
    editor.onDidChangeModel(reportCursorPosition);
  };

  if (editorView === "agents") {
    return <AgentTeamWorkspace agents={agents} activeAgentId={activeAgentId} />;
  }

  if (editorView === "welcome" || activeFile === null) {
    return (
      <main className="editor-pane editor-pane--welcome" aria-label="编辑区">
        <section className="editor-empty-state">
          <div className="editor-empty-state__mark" aria-hidden="true">
            C
          </div>
          <h2>Codo Workbench</h2>
          <p>在左侧 Explorer 中选择文件，或在右侧 Codo 中描述任务</p>
        </section>
      </main>
    );
  }

  return (
    <main className="editor-pane" aria-label="编辑区">
      <header className="editor-tabs" role="tablist" aria-label="打开的文件">
        {openFiles.map((openFile) => (
          <EditorTab
            key={openFile.path}
            openFile={openFile}
            active={openFile.path === activeFilePath}
            dirty={isOpenFileDirty(openFile)}
            saving={isSaving && openFile.path === activeFilePath}
            activeFileIsDirty={isActiveFileDirty}
            onSelectFileTab={onSelectFileTab}
            onCloseFileTab={onCloseFileTab}
            onSaveFile={onSaveFile}
          />
        ))}
      </header>

      <section className="editor-surface" aria-label={activeFile.name}>
        <Editor
          height="100%"
          path={activeFile.path}
          language={activeFile.language}
          value={activeFile.content}
          theme="vs-dark"
          onChange={(value) => onChangeFileContent(activeFile.path, value ?? "")}
          onMount={handleEditorMount}
          options={MONACO_EDITOR_OPTIONS}
        />
      </section>
    </main>
  );
}

interface AgentTeamWorkspaceProps {
  agents: AiAgentSummary[];
  activeAgentId: string | null;
}

/**
 * 中间工作区的 Agent Team 可视化页面。
 *
 * 工作流：
 * 1. 右侧 Agent Team 面板触发 `editor/agents-selected`。
 * 2. App 把 aiState.agents 传入编辑区。
 * 3. 这里只做只读展示，不直接启动或停止 Agent。
 */
function AgentTeamWorkspace({ agents, activeAgentId }: AgentTeamWorkspaceProps) {
  const runningCount = agents.filter((agent) => agent.status === "running").length;
  const completedCount = agents.filter((agent) => agent.status === "completed").length;
  const errorCount = agents.filter((agent) => agent.status === "error").length;

  return (
    <main className="editor-pane editor-pane--agents" aria-label="Agent Team">
      <section className="agent-workspace">
        <header className="agent-workspace__header">
          <div>
            <span>Agent Team</span>
            <h2>协作代理运行视图</h2>
          </div>
          <dl className="agent-workspace__stats" aria-label="Agent 状态统计">
            <div>
              <dt>运行</dt>
              <dd>{runningCount}</dd>
            </div>
            <div>
              <dt>完成</dt>
              <dd>{completedCount}</dd>
            </div>
            <div>
              <dt>异常</dt>
              <dd>{errorCount}</dd>
            </div>
          </dl>
        </header>

        {agents.length === 0 ? (
          <div className="agent-workspace__empty">
            <strong>当前没有协作 Agent</strong>
            <p>当主 AI 调用 Agent 工具后，这里会显示子代理、任务状态、当前动作和结果摘要。</p>
          </div>
        ) : (
          <div className="agent-workspace__grid">
            <article className="agent-workspace__lead">
              <span className="agent-workspace__lead-mark" aria-hidden="true">
                C
              </span>
              <div>
                <strong>Codo 主流程</strong>
                <p>负责理解用户请求、分派工具和汇总子代理结果。</p>
              </div>
            </article>
            {agents.map((agent) => (
              <AgentWorkspaceCard
                key={agent.agentId}
                agent={agent}
                active={agent.agentId === activeAgentId}
              />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

interface AgentWorkspaceCardProps {
  agent: AiAgentSummary;
  active: boolean;
}

function AgentWorkspaceCard({ agent, active }: AgentWorkspaceCardProps) {
  return (
    <article
      className={[
        "agent-workspace-card",
        `agent-workspace-card--${agent.status}`,
        active ? "agent-workspace-card--active" : ""
      ]
        .filter((className) => className.length > 0)
        .join(" ")}
    >
      <header className="agent-workspace-card__header">
        <div>
          <span>{formatAgentType(agent)}</span>
          <strong>{formatAgentLabel(agent)}</strong>
        </div>
        <em>{formatAgentStatusText(agent.status)}</em>
      </header>
      <dl className="agent-workspace-card__meta">
        <div>
          <dt>模式</dt>
          <dd>{formatAgentMode(agent)}</dd>
        </div>
        <div>
          <dt>任务</dt>
          <dd>{agent.taskId ?? "前台任务"}</dd>
        </div>
        <div>
          <dt>Tokens</dt>
          <dd>{agent.totalTokens}</dd>
        </div>
      </dl>
      <section className="agent-workspace-card__section">
        <span>当前动作</span>
        <p>{formatAgentAction(agent)}</p>
      </section>
      {agent.resultPreview.trim().length > 0 ? (
        <section className="agent-workspace-card__section">
          <span>结果摘要</span>
          <pre>{agent.resultPreview}</pre>
        </section>
      ) : null}
    </article>
  );
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

interface EditorTabProps {
  openFile: OpenFile;
  active: boolean;
  dirty: boolean;
  saving: boolean;
  activeFileIsDirty: boolean;
  onSelectFileTab: (path: string) => void;
  onCloseFileTab: (path: string) => void;
  onSaveFile: () => void;
}

/**
 * 单个编辑器 tab。
 *
 * 工作流：
 * 1. 点击 tab 主体时切换 activeFilePath。
 * 2. 只有当前激活 tab 展示保存按钮。
 * 3. 关闭按钮只关闭当前 tab，不触发 tab 切换。
 */
function EditorTab({
  openFile,
  active,
  dirty,
  saving,
  activeFileIsDirty,
  onSelectFileTab,
  onCloseFileTab,
  onSaveFile
}: EditorTabProps) {
  return (
    <div
      className={`editor-tab ${active ? "editor-tab--active" : ""} ${
        dirty ? "editor-tab--dirty" : ""
      }`}
      role="tab"
      aria-selected={active}
      title={openFile.path}
    >
      <button
        className="editor-tab__select"
        type="button"
        onClick={() => onSelectFileTab(openFile.path)}
      >
        <span
          className="editor-tab__dirty-dot"
          aria-label={dirty ? "文件有未保存改动" : "文件已保存"}
        >
          {dirty ? "●" : ""}
        </span>
        <span className="editor-tab__name">{openFile.name}</span>
      </button>
      {active ? (
        <button
          className="editor-tab__save"
          type="button"
          disabled={!activeFileIsDirty || saving}
          onClick={onSaveFile}
        >
          {saving ? "保存中" : "保存"}
        </button>
      ) : null}
      <button
        className="editor-tab__close"
        type="button"
        aria-label={`关闭 ${openFile.name}`}
        onClick={() => onCloseFileTab(openFile.path)}
      >
        ×
      </button>
    </div>
  );
}

/**
 * 判断单个 tab 是否有未保存改动。
 */
function isOpenFileDirty(openFile: OpenFile): boolean {
  return openFile.content !== openFile.savedContent;
}
