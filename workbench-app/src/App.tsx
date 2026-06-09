import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent
} from "react";
import type {
  AiImageAttachment,
  AiPermissionMode,
  AiResolveInteractionRequest,
  AiSubmitMessageRequest
} from "../shared/aiProtocol";
import { ActivityBar } from "./components/ActivityBar";
import { AiChatPane } from "./components/AiChatPane";
import { EditorPane } from "./components/EditorPane";
import { ExplorerPane } from "./components/ExplorerPane";
import { StatusBar } from "./components/StatusBar";
import { createAiClient } from "./services/aiClient";
import { createWorkspaceClient } from "./services/workspaceClient";
import {
  aiPanelReducer,
  createInitialAiPanelState,
  isAiTurnActive
} from "./state/aiState";
import {
  createInitialWorkbenchState,
  getActiveOpenFile,
  isOpenFileDirty,
  workbenchReducer
} from "./state/workbenchState";
import type { EditorCursorPosition, EditorStatusInfo, ExplorerNode, OpenFile } from "./types";

const CHAT_PANE_WIDTH_STORAGE_KEY = "codo.workbench.chatPaneWidth";
const DEFAULT_CHAT_PANE_WIDTH = 480;
const MIN_CHAT_PANE_WIDTH = 320;
const MAX_CHAT_PANE_WIDTH = 680;

/**
 * Codo Workbench 根组件。
 *
 * 工作流：
 * 1. workbenchState 管 Explorer、编辑器、文件保存等本地工作台状态。
 * 2. aiState 管右侧 AI 对话、工具摘要和 Todo 面板。
 * 3. App 只负责串联子组件和服务客户端，不在这里实现具体 UI。
 */
export default function App() {
  const [workbenchState, workbenchDispatch] = useReducer(
    workbenchReducer,
    undefined,
    createInitialWorkbenchState
  );
  const [aiState, aiDispatch] = useReducer(
    aiPanelReducer,
    undefined,
    createInitialAiPanelState
  );
  const [cursorPosition, setCursorPosition] = useState<EditorCursorPosition | null>(null);
  const [chatPaneWidth, setChatPaneWidth] = useState(readInitialChatPaneWidth);

  const workspaceClient = useMemo(() => {
    if (!hasWorkbenchApi()) {
      return null;
    }

    return createWorkspaceClient();
  }, []);

  const aiClient = useMemo(() => {
    if (!hasWorkbenchApi()) {
      return null;
    }

    return createAiClient();
  }, []);

  const loadAiSessionMessages = useCallback(
    async (workspacePath: string, sessionId: string, isCanceled: () => boolean = () => false) => {
      if (aiClient === null) {
        return;
      }

      try {
        const messages = await aiClient.loadSessionMessages({ workspacePath, sessionId });
        if (isCanceled()) {
          return;
        }

        aiDispatch({
          type: "session/messages-loaded",
          sessionId,
          messages,
          createdAt: formatCurrentTime()
        });
      } catch (error) {
        if (isCanceled()) {
          return;
        }

        aiDispatch({
          type: "session/messages-load-failed",
          sessionId,
          message: getErrorMessage(error),
          createdAt: formatCurrentTime()
        });
      }
    },
    [aiClient]
  );

  const activeOpenFile = getActiveOpenFile(workbenchState);
  const activeOpenFileIsDirty = isOpenFileDirty(activeOpenFile);
  const isSaving = workbenchState.status === "saving";
  const editorStatus: EditorStatusInfo = {
    fileName: activeOpenFile?.name ?? null,
    filePath: activeOpenFile?.path ?? null,
    language: activeOpenFile?.language ?? null,
    cursorPosition: activeOpenFile === null ? null : cursorPosition,
    encoding: "UTF-8",
    indentation: "Spaces: 2",
    dirty: activeOpenFileIsDirty
  };

  useEffect(() => {
    return aiClient?.onEvent((event) => {
      aiDispatch({
        type: "turn/event-received",
        event,
        createdAt: formatCurrentTime()
      });
    });
  }, [aiClient]);

  useEffect(() => {
    const workspace = workbenchState.workspace;

    aiDispatch({
      type: "workspace/changed",
      workspaceName: workspace?.name ?? null,
      workspacePath: workspace?.path ?? null,
      createdAt: formatCurrentTime()
    });

    if (workspace === null) {
      return;
    }

    if (aiClient === null) {
      aiDispatch({
        type: "session/load-failed",
        message: "当前页面没有 Electron AI API，无法读取历史会话。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    let canceled = false;
    aiDispatch({ type: "session/load-started", createdAt: formatCurrentTime() });

    void aiClient
      .listSessions({ workspacePath: workspace.path })
      .then((sessions) => {
        if (canceled) {
          return;
        }

        aiDispatch({
          type: "session/list-loaded",
          sessions,
          createdAt: formatCurrentTime()
        });

        const latestSession = sessions[0];
        if (latestSession !== undefined) {
          void loadAiSessionMessages(
            workspace.path,
            latestSession.sessionId,
            () => canceled
          );
        }
      })
      .catch((error: unknown) => {
        if (canceled) {
          return;
        }

        aiDispatch({
          type: "session/load-failed",
          message: getErrorMessage(error),
          createdAt: formatCurrentTime()
        });
      });

    return () => {
      canceled = true;
    };
  }, [workbenchState.workspace, aiClient, loadAiSessionMessages]);

  const handleSelectWorkspace = async () => {
    if (!confirmDiscardUnsavedChanges(workbenchState.openFiles)) {
      return;
    }

    if (workspaceClient === null) {
      workbenchDispatch({
        type: "operation/failed",
        message: "当前页面没有 Electron preload API，请通过桌面应用启动。"
      });
      return;
    }

    workbenchDispatch({ type: "workspace/select-started" });

    try {
      const workspace = await workspaceClient.selectWorkspace();

      if (workspace === null) {
        workbenchDispatch({ type: "workspace/select-canceled" });
        return;
      }

      const entries = await workspaceClient.listDirectory("");
      workbenchDispatch({ type: "workspace/selected", workspace, entries });
    } catch (error) {
      workbenchDispatch({ type: "operation/failed", message: getErrorMessage(error) });
    }
  };

  const handleOpenFolder = async (path: string) => {
    const folderNode = findExplorerNode(workbenchState.explorerNodes, path);

    if (folderNode?.kind === "folder" && folderNode.loading) {
      return;
    }

    if (folderNode?.kind === "folder" && folderNode.expanded) {
      workbenchDispatch({ type: "directory/collapsed", path });
      return;
    }

    const cachedEntries = workbenchState.directoryEntryCache[path];

    if (cachedEntries !== undefined) {
      workbenchDispatch({ type: "directory/loaded", parentPath: path, entries: cachedEntries });
      return;
    }

    if (workspaceClient === null) {
      workbenchDispatch({
        type: "operation/failed",
        message: "当前页面没有 Electron preload API，请通过桌面应用启动。"
      });
      return;
    }

    workbenchDispatch({ type: "directory/load-started", path });

    try {
      const entries = await workspaceClient.listDirectory(path);
      workbenchDispatch({ type: "directory/loaded", parentPath: path, entries });
    } catch (error) {
      workbenchDispatch({ type: "operation/failed", message: getErrorMessage(error) });
    }
  };

  const handleRefreshWorkspace = async () => {
    if (workspaceClient === null) {
      workbenchDispatch({
        type: "operation/failed",
        message: "当前页面没有 Electron preload API，请通过桌面应用启动。"
      });
      return;
    }

    if (workbenchState.workspace === null) {
      workbenchDispatch({ type: "operation/failed", message: "请先选择工作区。" });
      return;
    }

    workbenchDispatch({ type: "workspace/root-refresh-started" });

    try {
      const entries = await workspaceClient.listDirectory("");
      workbenchDispatch({ type: "workspace/root-refreshed", entries });
    } catch (error) {
      workbenchDispatch({ type: "operation/failed", message: getErrorMessage(error) });
    }
  };

  const handleOpenFile = async (path: string) => {
    if (workspaceClient === null) {
      workbenchDispatch({
        type: "operation/failed",
        message: "当前页面没有 Electron preload API，请通过桌面应用启动。"
      });
      return;
    }

    workbenchDispatch({ type: "file/open-started", path });

    try {
      const file = await workspaceClient.readFile(path);
      workbenchDispatch({ type: "file/opened", file });
    } catch (error) {
      workbenchDispatch({ type: "operation/failed", message: getErrorMessage(error) });
    }
  };

  const handleChangeFileContent = (path: string, content: string) => {
    workbenchDispatch({ type: "file/content-changed", path, content });
  };

  const handleCursorPositionChange = (position: EditorCursorPosition) => {
    setCursorPosition(position);
  };

  const handleSelectFileTab = (path: string) => {
    workbenchDispatch({ type: "editor/tab-selected", path });
  };

  const handleCloseFileTab = (path: string) => {
    const openFile = findOpenFile(workbenchState.openFiles, path);

    if (!confirmDiscardUnsavedChange(openFile)) {
      return;
    }

    workbenchDispatch({ type: "editor/tab-closed", path });
  };

  const handleSaveFile = useCallback(async () => {
    if (workspaceClient === null) {
      workbenchDispatch({
        type: "operation/failed",
        message: "当前页面没有 Electron preload API，请通过桌面应用启动。"
      });
      return;
    }

    if (activeOpenFile === null || !isOpenFileDirty(activeOpenFile)) {
      return;
    }

    const fileToSave = activeOpenFile;
    workbenchDispatch({ type: "file/save-started", path: fileToSave.path });

    try {
      await workspaceClient.writeFile(fileToSave.path, fileToSave.content);
      workbenchDispatch({
        type: "file/saved",
        path: fileToSave.path,
        content: fileToSave.content
      });
    } catch (error) {
      workbenchDispatch({ type: "operation/failed", message: getErrorMessage(error) });
    }
  }, [activeOpenFile, workspaceClient]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const isSaveShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s";

      if (!isSaveShortcut) {
        return;
      }

      event.preventDefault();
      void handleSaveFile();
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [handleSaveFile]);

  const handleSendMessage = async (content: string, images?: AiImageAttachment[]) => {
    if (aiClient === null) {
      aiDispatch({
        type: "turn/local-error",
        message: "当前页面没有 Electron AI API，请通过桌面应用启动。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    if (workbenchState.workspace === null) {
      aiDispatch({
        type: "turn/local-error",
        message: "请先选择工作区，再开始 AI 对话。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    if (isAiTurnActive(aiState)) {
      aiDispatch({
        type: "turn/local-error",
        message: "当前 AI 轮次仍在进行中，请先中断或等待完成。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    const turnId = createTurnId();
    const request: AiSubmitMessageRequest = {
      turnId,
      prompt: content,
      sessionId: aiState.selectedSessionId,
      workspaceName: workbenchState.workspace.name,
      workspacePath: workbenchState.workspace.path,
      activeFilePath: workbenchState.activeFilePath,
      selectedPath: workbenchState.selectedPath,
      openFilePaths: workbenchState.openFiles.map((file) => file.path),
      permissionMode: aiState.permissionMode,
      images
    };

    aiDispatch({
      type: "turn/submitted",
      turnId,
      prompt: content,
      images: images ?? [],
      createdAt: formatCurrentTime()
    });

    try {
      await aiClient.submitMessage(request);
    } catch (error) {
      aiDispatch({
        type: "turn/local-error",
        message: getErrorMessage(error),
        createdAt: formatCurrentTime()
      });
    }
  };

  const handleCancelAiTurn = async () => {
    if (aiClient === null || aiState.activeTurnId === null) {
      return;
    }

    const turnId = aiState.activeTurnId;
    aiDispatch({ type: "turn/cancel-started", createdAt: formatCurrentTime() });

    try {
      await aiClient.cancelTurn({ turnId });
    } catch (error) {
      aiDispatch({
        type: "turn/local-error",
        message: getErrorMessage(error),
        createdAt: formatCurrentTime()
      });
    }
  };

  const handleResolveInteraction = async (request: AiResolveInteractionRequest) => {
    if (aiClient === null) {
      aiDispatch({
        type: "turn/local-error",
        message: "当前页面没有 Electron AI API，请通过桌面应用启动。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    try {
      await aiClient.resolveInteraction(request);
    } catch (error) {
      aiDispatch({
        type: "turn/local-error",
        message: getErrorMessage(error),
        createdAt: formatCurrentTime()
      });
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    if (!workbenchState.workspace) {
      return;
    }

    try {
      await window.codoWorkbench.ai.deleteSession({
        workspacePath: workbenchState.workspace.path,
        sessionId
      });

      // 如果删除的是当前选中的会话，切换到其他会话
      if (aiState.selectedSessionId === sessionId) {
        const remainingSessions = aiState.sessions.filter((s) => s.sessionId !== sessionId);
        if (remainingSessions.length > 0) {
          handleSelectAiSession(remainingSessions[0].sessionId);
        } else {
          // 如果没有其他会话了，开始新会话
          handleStartNewAiSession();
        }
      } else {
        // 只刷新会话列表
        const updatedSessions = await window.codoWorkbench.ai.listSessions({
          workspacePath: workbenchState.workspace.path
        });
        aiDispatch({
          type: "session/list-loaded",
          sessions: updatedSessions,
          createdAt: formatCurrentTime()
        });
      }
    } catch (error) {
      aiDispatch({
        type: "turn/local-error",
        message: `删除会话失败: ${getErrorMessage(error)}`,
        createdAt: formatCurrentTime()
      });
    }
  };

  const handleSelectAiSession = (sessionId: string) => {
    if (isAiTurnActive(aiState)) {
      aiDispatch({
        type: "turn/local-error",
        message: "当前 AI 轮次仍在进行中，结束后再切换历史会话。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    if (aiClient === null || workbenchState.workspace === null) {
      aiDispatch({
        type: "turn/local-error",
        message: "当前页面没有可用的历史会话读取能力。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    aiDispatch({
      type: "session/selected",
      sessionId,
      createdAt: formatCurrentTime()
    });

    void loadAiSessionMessages(workbenchState.workspace.path, sessionId);
  };

  /**
   * 开启一个新的 AI 会话。
   *
   * 工作流：
   * 1. 运行中的轮次不能切换上下文，否则 Python runtime 和 UI 会话会不一致。
   * 2. 新会话先只在前端清空上下文。
   * 3. 下一次发送消息时 `sessionId` 为 null，由后端创建真实会话。
   */
  const handleStartNewAiSession = () => {
    if (isAiTurnActive(aiState)) {
      aiDispatch({
        type: "turn/local-error",
        message: "当前 AI 轮次仍在进行中，结束后再开启新会话。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    aiDispatch({
      type: "session/new-started",
      createdAt: formatCurrentTime()
    });
  };

  const handleChangePermissionMode = (mode: AiPermissionMode) => {
    if (isAiTurnActive(aiState)) {
      aiDispatch({
        type: "turn/local-error",
        message: "当前 AI 轮次仍在进行中，权限模式会影响下一轮，请结束后再切换。",
        createdAt: formatCurrentTime()
      });
      return;
    }

    aiDispatch({
      type: "permission-mode/changed",
      mode,
      createdAt: formatCurrentTime()
    });
  };

  const handleOpenAgentTeam = () => {
    workbenchDispatch({ type: "editor/agents-selected" });
  };
  const workbenchGridStyle = {
    "--chat-width": `${chatPaneWidth}px`
  } as CSSProperties;

  /**
   * 拖拽调整右侧 AI 面板宽度。
   *
   * 工作流：
   * 1. 记录鼠标按下时的 X 坐标和当前宽度。
   * 2. 鼠标移动时只更新 `--chat-width`，让 CSS Grid 负责布局。
   * 3. 鼠标释放后保存宽度，下次启动恢复用户习惯。
   */
  const handleChatResizerPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();

    const startX = event.clientX;
    const startWidth = chatPaneWidth;
    let latestWidth = startWidth;

    // 添加拖拽状态类名
    document.body.classList.add("is-resizing-chat");

    const handlePointerMove = (pointerEvent: PointerEvent) => {
      const deltaX = pointerEvent.clientX - startX;
      latestWidth = clampChatPaneWidth(startWidth - deltaX);
      setChatPaneWidth(latestWidth);
    };

    const handlePointerUp = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);

      // 移除拖拽状态类名
      document.body.classList.remove("is-resizing-chat");

      saveChatPaneWidth(latestWidth);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  };

  const handleChatResizerKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }

    event.preventDefault();

    const nextWidth =
      event.key === "ArrowLeft"
        ? clampChatPaneWidth(chatPaneWidth + 20)
        : clampChatPaneWidth(chatPaneWidth - 20);
    setChatPaneWidth(nextWidth);
    saveChatPaneWidth(nextWidth);
  };

  return (
    <div className="workbench-shell">
      <div className="workbench-grid" style={workbenchGridStyle}>
        <ActivityBar activeItem="explorer" />
        <ExplorerPane
          workspaceName={workbenchState.workspace?.name ?? null}
          nodes={workbenchState.explorerNodes}
          selectedPath={workbenchState.selectedPath}
          status={workbenchState.status}
          onSelectWorkspace={handleSelectWorkspace}
          onRefreshWorkspace={handleRefreshWorkspace}
          onOpenFolder={handleOpenFolder}
          onOpenFile={handleOpenFile}
        />
        <EditorPane
          editorView={workbenchState.editorView}
          openFiles={workbenchState.openFiles}
          activeFile={activeOpenFile}
          activeFilePath={workbenchState.activeFilePath}
          agents={aiState.agents}
          activeAgentId={aiState.runtime.activeAgentId}
          isActiveFileDirty={activeOpenFileIsDirty}
          isSaving={isSaving}
          onSelectFileTab={handleSelectFileTab}
          onCloseFileTab={handleCloseFileTab}
          onChangeFileContent={handleChangeFileContent}
          onCursorPositionChange={handleCursorPositionChange}
          onSaveFile={() => {
            void handleSaveFile();
          }}
        />
        <div
          aria-label="调整 AI 面板宽度"
          aria-orientation="vertical"
          className="workbench-resizer workbench-resizer--chat"
          role="separator"
          tabIndex={0}
          onKeyDown={handleChatResizerKeyDown}
          onPointerDown={handleChatResizerPointerDown}
        />
        <AiChatPane
          state={aiState}
          onSendMessage={(content, images) => {
            void handleSendMessage(content, images);
          }}
          onCancelTurn={() => {
            void handleCancelAiTurn();
          }}
          onResolveInteraction={(request) => {
            void handleResolveInteraction(request);
          }}
          onOpenAgentTeam={handleOpenAgentTeam}
          onStartNewSession={handleStartNewAiSession}
          onSelectSession={handleSelectAiSession}
          onChangePermissionMode={handleChangePermissionMode}
          onDeleteSession={handleDeleteSession}
        />
      </div>
      <StatusBar
        workspaceName={workbenchState.workspace?.name ?? null}
        status={workbenchState.status}
        statusMessage={workbenchState.statusMessage}
        editorStatus={editorStatus}
      />
    </div>
  );
}

/**
 * 判断当前运行环境是否已经注入 Electron preload API。
 */
function hasWorkbenchApi(): boolean {
  return "codoWorkbench" in window;
}

/**
 * 把未知错误转换成用户可读消息。
 */
function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return "操作失败。";
}

/**
 * 在会丢弃当前编辑内容的操作前确认用户意图。
 *
 * 工作流：
 * 1. 没有打开文件或文件已保存时直接放行。
 * 2. 文件有未保存改动时，让用户明确确认是否继续。
 * 3. 当前阶段是多 tab，关闭和切换工作区前都需要保护未保存内容。
 */
function confirmDiscardUnsavedChange(openFile: OpenFile | null): boolean {
  if (openFile === null || !isOpenFileDirty(openFile)) {
    return true;
  }

  return window.confirm(`文件 ${openFile.name} 有未保存改动，继续操作会丢弃这些改动。`);
}

/**
 * 在会关闭当前工作区的操作前确认未保存 tab。
 *
 * 工作流：
 * 1. 切换工作区会清空全部打开 tab。
 * 2. 先统计未保存文件数量。
 * 3. 没有未保存文件时直接放行，否则让用户确认。
 */
function confirmDiscardUnsavedChanges(openFiles: OpenFile[]): boolean {
  const dirtyFiles = openFiles.filter((openFile) => isOpenFileDirty(openFile));

  if (dirtyFiles.length === 0) {
    return true;
  }

  return window.confirm(`当前有 ${dirtyFiles.length} 个文件未保存，继续操作会丢弃这些改动。`);
}

/**
 * 根据路径查找已打开 tab 文件。
 */
function findOpenFile(openFiles: OpenFile[], path: string): OpenFile | null {
  return openFiles.find((openFile) => openFile.path === path) ?? null;
}

/**
 * 根据相对路径查找 Explorer 节点。
 *
 * 工作流：
 * 1. App 层收到文件夹点击事件。
 * 2. 通过节点状态判断它是展开还是收起。
 * 3. 已展开时走收起 action，未展开时才调用文件系统读取目录。
 */
function findExplorerNode(nodes: ExplorerNode[], path: string): ExplorerNode | undefined {
  return nodes.find((node) => node.path === path);
}

/**
 * 生成 AI 轮次 ID。
 */
function createTurnId(): string {
  return `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * 读取用户上次调整过的 AI 面板宽度。
 */
function readInitialChatPaneWidth(): number {
  try {
    const storedWidth = window.localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY);
    if (storedWidth === null) {
      return DEFAULT_CHAT_PANE_WIDTH;
    }

    return clampChatPaneWidth(Number.parseInt(storedWidth, 10));
  } catch {
    return DEFAULT_CHAT_PANE_WIDTH;
  }
}

/**
 * 保存 AI 面板宽度。
 */
function saveChatPaneWidth(width: number): void {
  try {
    window.localStorage.setItem(CHAT_PANE_WIDTH_STORAGE_KEY, `${clampChatPaneWidth(width)}`);
  } catch {
    // localStorage 不可用时不影响主工作流。
  }
}

/**
 * 限制右侧宽度，避免拖拽导致编辑区或 AI 面板不可用。
 */
function clampChatPaneWidth(width: number): number {
  if (Number.isNaN(width)) {
    return DEFAULT_CHAT_PANE_WIDTH;
  }

  return Math.min(Math.max(width, MIN_CHAT_PANE_WIDTH), MAX_CHAT_PANE_WIDTH);
}

/**
 * 生成聊天消息显示时间。
 */
function formatCurrentTime(): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date());
}
