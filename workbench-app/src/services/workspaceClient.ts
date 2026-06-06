import type {
  CodoWorkspaceApi,
  WorkspaceDirectoryEntry,
  WorkspaceInfo,
  WorkspaceReadFileResult,
  WorkspaceWriteFileResult
} from "../../shared/ipcTypes";

/**
 * Renderer 侧工作区客户端。
 *
 * 工作流：
 * 1. React 组件只依赖这个客户端，不直接触碰 window.codoWorkbench。
 * 2. 客户端把请求转发给 preload 暴露的受控 API。
 * 3. Electron main process 再执行真实文件系统操作。
 */
export class WorkspaceClient {
  public constructor(private readonly api: CodoWorkspaceApi) {}

  /**
   * 打开系统目录选择器，让用户选择当前工作区。
   */
  public async selectWorkspace(): Promise<WorkspaceInfo | null> {
    return this.api.selectWorkspace();
  }

  /**
   * 读取工作区内某个目录的第一层内容。
   *
   * 工作流：
   * 1. 入参是相对 workspace 的路径。
   * 2. 空字符串表示 workspace 根目录。
   * 3. main process 会做最终路径边界校验。
   */
  public async listDirectory(relativePath: string): Promise<WorkspaceDirectoryEntry[]> {
    this.assertRelativePath(relativePath);
    return this.api.listDirectory(relativePath);
  }

  /**
   * 读取工作区内单个文件内容。
   *
   * 工作流：
   * 1. Explorer 点击文件后调用。
   * 2. 客户端只传相对路径。
   * 3. 文件内容由 main process 读取后返回。
   */
  public async readFile(relativePath: string): Promise<WorkspaceReadFileResult> {
    this.assertRelativePath(relativePath);
    return this.api.readFile(relativePath);
  }

  /**
   * 写入工作区内单个文件内容。
   *
   * 工作流：
   * 1. 编辑器保存时传入相对路径和当前文本内容。
   * 2. 客户端做最基础的字符串校验。
   * 3. main process 负责最终路径边界校验和真实写入。
   */
  public async writeFile(
    relativePath: string,
    content: string
  ): Promise<WorkspaceWriteFileResult> {
    this.assertRelativePath(relativePath);
    this.assertFileContent(content);
    return this.api.writeFile({ path: relativePath, content });
  }

  private assertRelativePath(relativePath: string): void {
    if (typeof relativePath !== "string") {
      throw new Error("工作区路径必须是字符串。");
    }
  }

  private assertFileContent(content: string): void {
    if (typeof content !== "string") {
      throw new Error("文件内容必须是字符串。");
    }
  }
}

/**
 * 从 preload 暴露的 window API 创建工作区客户端。
 *
 * 工作流：
 * 1. React 应用启动后调用这个工厂。
 * 2. 工厂读取 preload 注入的 `window.codoWorkbench.workspace`。
 * 3. 返回可注入组件或状态层的 WorkspaceClient 实例。
 */
export function createWorkspaceClient(): WorkspaceClient {
  return new WorkspaceClient(window.codoWorkbench.workspace);
}
