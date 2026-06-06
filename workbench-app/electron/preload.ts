import { contextBridge, ipcRenderer } from "electron";
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
} from "../shared/aiProtocol.js";
import type {
  CodoWorkbenchApi,
  WorkspaceDirectoryEntry,
  WorkspaceInfo,
  WorkspaceReadFileResult,
  WorkspaceWriteFileRequest,
  WorkspaceWriteFileResult
} from "../shared/ipcTypes.js";

const API_KEY = "codoWorkbench";

/**
 * Renderer 安全桥。
 *
 * 工作流：
 * 1. Renderer 不能直接访问 Node fs，也不能直接拿 ipcRenderer。
 * 2. preload 只暴露当前 UI 需要的最小能力。
 * 3. 真正文件系统操作全部交给 main process。
 */
class CodoWorkbenchPreload {
  public expose(): void {
    contextBridge.exposeInMainWorld(API_KEY, this.createApi());
  }

  private createApi(): CodoWorkbenchApi {
    return {
      workspace: {
        selectWorkspace: () => this.selectWorkspace(),
        listDirectory: (relativePath: string) => this.listDirectory(relativePath),
        readFile: (relativePath: string) => this.readFile(relativePath),
        writeFile: (request: WorkspaceWriteFileRequest) => this.writeFile(request)
      },
      ai: {
        listSessions: (request: AiListSessionsRequest) => this.listSessions(request),
        loadSessionMessages: (request: AiLoadSessionMessagesRequest) =>
          this.loadSessionMessages(request),
        deleteSession: (request: AiDeleteSessionRequest) => this.deleteSession(request),
        submitMessage: (request: AiSubmitMessageRequest) => this.submitMessage(request),
        cancelTurn: (request: AiCancelTurnRequest) => this.cancelTurn(request),
        resolveInteraction: (request: AiResolveInteractionRequest) =>
          this.resolveInteraction(request),
        onEvent: (listener: (event: AiBridgeEvent) => void) => this.onEvent(listener)
      }
    };
  }

  /**
   * 打开系统目录选择器，让用户显式授权一个 workspace。
   */
  private async selectWorkspace(): Promise<WorkspaceInfo | null> {
    return ipcRenderer.invoke("workspace:select") as Promise<WorkspaceInfo | null>;
  }

  /**
   * 读取 workspace 内某个目录的第一层内容。
   *
   * 工作流：
   * 1. 前端传入相对 workspace 的路径。
   * 2. main process 校验路径边界。
   * 3. 返回这一层的文件和文件夹，不递归读取。
   */
  private async listDirectory(relativePath: string): Promise<WorkspaceDirectoryEntry[]> {
    return ipcRenderer.invoke("fs:list-directory", relativePath) as Promise<
      WorkspaceDirectoryEntry[]
    >;
  }

  /**
   * 读取 workspace 内单个文件内容。
   *
   * 工作流：
   * 1. 用户点击文件后才调用。
   * 2. main process 校验路径边界。
   * 3. 返回文件名、相对路径和文本内容。
   */
  private async readFile(relativePath: string): Promise<WorkspaceReadFileResult> {
    return ipcRenderer.invoke("fs:read-file", relativePath) as Promise<WorkspaceReadFileResult>;
  }

  /**
   * 写入 workspace 内单个文件内容。
   *
   * 工作流：
   * 1. Renderer 传入相对路径和编辑器当前内容。
   * 2. preload 不暴露 Node fs，只把请求转给 main process。
   * 3. main process 完成路径边界校验和真实写入。
   */
  private async writeFile(
    request: WorkspaceWriteFileRequest
  ): Promise<WorkspaceWriteFileResult> {
    return ipcRenderer.invoke("fs:write-file", request) as Promise<WorkspaceWriteFileResult>;
  }

  /**
   * 列出指定工作区的历史 AI 会话。
   */
  private async listSessions(request: AiListSessionsRequest): Promise<AiSessionInfo[]> {
    return ipcRenderer.invoke("ai:list-sessions", request) as Promise<AiSessionInfo[]>;
  }

  /**
   * 读取历史会话消息。
   */
  private async loadSessionMessages(
    request: AiLoadSessionMessagesRequest
  ): Promise<AiSessionMessage[]> {
    return ipcRenderer.invoke("ai:load-session-messages", request) as Promise<AiSessionMessage[]>;
  }

  /**
   * 删除历史会话。
   */
  private async deleteSession(request: AiDeleteSessionRequest): Promise<void> {
    await ipcRenderer.invoke("ai:delete-session", request);
  }

  /**
   * 提交一轮 AI 对话给主进程。
   */
  private async submitMessage(request: AiSubmitMessageRequest): Promise<void> {
    await ipcRenderer.invoke("ai:submit-message", request);
  }

  /**
   * 中断当前 AI 轮次。
   */
  private async cancelTurn(request: AiCancelTurnRequest): Promise<void> {
    await ipcRenderer.invoke("ai:cancel-turn", request);
  }

  /**
   * 回答 AI runtime 的交互请求。
   */
  private async resolveInteraction(request: AiResolveInteractionRequest): Promise<void> {
    await ipcRenderer.invoke("ai:resolve-interaction", request);
  }

  /**
   * 订阅 AI 事件流。
   */
  private onEvent(listener: (event: AiBridgeEvent) => void): () => void {
    const channel = "ai:event";
    const handleEvent = (_event: Electron.IpcRendererEvent, payload: unknown) => {
      listener(payload as AiBridgeEvent);
    };

    ipcRenderer.on(channel, handleEvent);

    return () => {
      ipcRenderer.off(channel, handleEvent);
    };
  }
}

new CodoWorkbenchPreload().expose();
