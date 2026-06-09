import { app, BrowserWindow, dialog, ipcMain, Menu } from "electron";
import type { IpcMainInvokeEvent, OpenDialogReturnValue } from "electron";
import type { Dirent } from "node:fs";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { WorkbenchAiBridge } from "./aiBridge.js";
import type {
  WorkspaceDirectoryEntry,
  WorkspaceInfo,
  WorkspaceReadFileResult,
  WorkspaceWriteFileRequest,
  WorkspaceWriteFileResult
} from "../shared/ipcTypes.js";

const DEV_SERVER_URL = "http://127.0.0.1:5173";
const DEFAULT_WINDOW_WIDTH = 1440;
const DEFAULT_WINDOW_HEIGHT = 900;
const MIN_WINDOW_WIDTH = 960;
const MIN_WINDOW_HEIGHT = 640;
const WORKBENCH_BACKGROUND = "#1e1e1e";
const BYTES_PER_KIBIBYTE = 1024;
const BYTES_PER_MEBIBYTE = BYTES_PER_KIBIBYTE * 1024;
const MAX_READ_FILE_BYTES = 5 * BYTES_PER_MEBIBYTE;

interface SortableWorkspaceDirectoryEntry extends WorkspaceDirectoryEntry {
  sortName: string;
}

/**
 * 工作区路径安全边界。
 *
 * 工作流：
 * 1. 用户通过系统目录选择器确定 workspaceRoot。
 * 2. 前端后续只能传相对 workspaceRoot 的路径。
 * 3. 本类把相对路径解析成绝对路径，并拒绝越界路径。
 *
 * 注意：
 * 这个类只服务于工作区内 Explorer、读文件、未来保存文件等操作。
 * 如果后续要导入外部文件、另存为、选择 Python 解释器，需要单独走系统 dialog 授权。
 */
class WorkspacePathGuard {
  private workspaceRoot: string | null = null;

  public setWorkspaceRoot(selectedPath: string): void {
    this.workspaceRoot = path.resolve(selectedPath);
  }

  public getWorkspaceRoot(): string | null {
    return this.workspaceRoot;
  }

  public resolveInsideWorkspace(relativePath: string): string {
    if (this.workspaceRoot === null) {
      throw new Error("请先选择工作区。");
    }

    const normalizedRelativePath = relativePath.trim() === "" ? "." : relativePath;
    const absolutePath = path.resolve(this.workspaceRoot, normalizedRelativePath);
    const rootWithSeparator = this.workspaceRoot.endsWith(path.sep)
      ? this.workspaceRoot
      : `${this.workspaceRoot}${path.sep}`;

    if (absolutePath !== this.workspaceRoot && !absolutePath.startsWith(rootWithSeparator)) {
      throw new Error("路径超出当前工作区。");
    }

    return absolutePath;
  }
}

/**
 * 工作区文件服务。
 *
 * 工作流：
 * 1. `selectWorkspace` 让用户选择任意项目目录，不绑定当前开发仓库。
 * 2. `listDirectory` 只读取当前目录第一层，不递归扫描。
 * 3. `readWorkspaceFile` 只在用户点击文件后读取内容。
 */
class WorkspaceFileService {
  public constructor(private readonly pathGuard: WorkspacePathGuard) {}

  public async selectWorkspace(ownerWindow: BrowserWindow): Promise<WorkspaceInfo | null> {
    const result: OpenDialogReturnValue = await dialog.showOpenDialog(ownerWindow, {
      title: "选择工作区",
      properties: ["openDirectory"]
    });

    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }

    const selectedWorkspacePath = result.filePaths[0];
    this.pathGuard.setWorkspaceRoot(selectedWorkspacePath);

    return {
      name: path.basename(selectedWorkspacePath),
      path: path.resolve(selectedWorkspacePath)
    };
  }

  public async listDirectory(relativePath: string): Promise<WorkspaceDirectoryEntry[]> {
    const directoryPath = this.pathGuard.resolveInsideWorkspace(relativePath);
    const workspaceRoot = this.pathGuard.getWorkspaceRoot();

    if (workspaceRoot === null) {
      throw new Error("请先选择工作区。");
    }

    let entries: Dirent[];

    try {
      entries = await readdir(directoryPath, { withFileTypes: true });
    } catch (error) {
      throw createDirectoryReadError(error, relativePath);
    }

    return entries
      .map((entry): SortableWorkspaceDirectoryEntry => {
        const childAbsolutePath = path.join(directoryPath, entry.name);
        const childRelativePath = path.relative(workspaceRoot, childAbsolutePath);

        return {
          id: childRelativePath,
          name: entry.name,
          path: childRelativePath,
          kind: entry.isDirectory() ? "folder" : "file",
          sortName: entry.name.toLowerCase()
        };
      })
      .sort(compareWorkspaceDirectoryEntries)
      .map(removeSortKey);
  }

  public async readWorkspaceFile(relativePath: string): Promise<WorkspaceReadFileResult> {
    const filePath = this.pathGuard.resolveInsideWorkspace(relativePath);
    await this.assertReadableWorkspaceFile(filePath);

    const content = await readFile(filePath, "utf8");

    return {
      name: path.basename(filePath),
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
  private async assertReadableWorkspaceFile(filePath: string): Promise<void> {
    const fileStats = await stat(filePath);

    if (!fileStats.isFile()) {
      throw new Error("当前路径不是可打开的文件。");
    }

    if (fileStats.size > MAX_READ_FILE_BYTES) {
      throw new Error(
        `文件过大：${formatFileSize(fileStats.size)}，当前限制 ${formatFileSize(
          MAX_READ_FILE_BYTES
        )}。`
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
  public async writeWorkspaceFile(
    request: WorkspaceWriteFileRequest
  ): Promise<WorkspaceWriteFileResult> {
    const filePath = this.pathGuard.resolveInsideWorkspace(request.path);
    await writeFile(filePath, request.content, "utf8");

    return {
      name: path.basename(filePath),
      path: request.path
    };
  }
}

/**
 * Codo 桌面窗口。
 *
 * 工作流：
 * 1. 创建 BrowserWindow。
 * 2. 等待页面 ready-to-show 后展示，避免白屏闪烁。
 * 3. 开发环境加载 Vite，生产环境加载构建后的 HTML。
 */
class WorkbenchWindow {
  private window: BrowserWindow | null = null;

  public create(): BrowserWindow {
    this.window = new BrowserWindow({
      width: DEFAULT_WINDOW_WIDTH,
      height: DEFAULT_WINDOW_HEIGHT,
      minWidth: MIN_WINDOW_WIDTH,
      minHeight: MIN_WINDOW_HEIGHT,
      backgroundColor: WORKBENCH_BACKGROUND,
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true
      }
    });

    this.window.once("ready-to-show", () => {
      this.window?.show();
    });

    if (app.isPackaged) {
      void this.window.loadFile(path.join(__dirname, "..", "dist", "index.html"));
    } else {
      void this.window.loadURL(DEV_SERVER_URL);
    }

    return this.window;
  }

  public getRequiredWindow(): BrowserWindow {
    if (this.window === null) {
      throw new Error("主窗口尚未创建。");
    }

    return this.window;
  }

  public getWindow(): BrowserWindow | null {
    return this.window;
  }
}

/**
 * IPC 控制器。
 *
 * 工作流：
 * 1. 注册 renderer 可调用的最小文件系统能力。
 * 2. 对 IPC 入参做明确校验，因为 renderer 边界传入的数据不可信。
 * 3. 把真实业务委托给 WorkspaceFileService。
 */
class WorkbenchIpcController {
  public constructor(
    private readonly workbenchWindow: WorkbenchWindow,
    private readonly fileService: WorkspaceFileService
  ) {}

  public register(): void {
    ipcMain.handle("workspace:select", () => this.selectWorkspace());
    ipcMain.handle("fs:list-directory", (_event: IpcMainInvokeEvent, relativePath: unknown) =>
      this.listDirectory(relativePath)
    );
    ipcMain.handle("fs:read-file", (_event: IpcMainInvokeEvent, relativePath: unknown) =>
      this.readFile(relativePath)
    );
    ipcMain.handle("fs:write-file", (_event: IpcMainInvokeEvent, request: unknown) =>
      this.writeFile(request)
    );
  }

  private async selectWorkspace(): Promise<WorkspaceInfo | null> {
    return this.fileService.selectWorkspace(this.workbenchWindow.getRequiredWindow());
  }

  private async listDirectory(relativePath: unknown): Promise<WorkspaceDirectoryEntry[]> {
    return this.fileService.listDirectory(this.parseRelativePath(relativePath));
  }

  private async readFile(relativePath: unknown): Promise<WorkspaceReadFileResult> {
    return this.fileService.readWorkspaceFile(this.parseRelativePath(relativePath));
  }

  private async writeFile(request: unknown): Promise<WorkspaceWriteFileResult> {
    return this.fileService.writeWorkspaceFile(this.parseWriteFileRequest(request));
  }

  private parseRelativePath(value: unknown): string {
    if (typeof value !== "string") {
      throw new Error("路径参数必须是字符串。");
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
  private parseWriteFileRequest(value: unknown): WorkspaceWriteFileRequest {
    if (!isRecord(value)) {
      throw new Error("写文件参数必须是对象。");
    }

    const pathValue = value.path;
    const contentValue = value.content;

    if (typeof pathValue !== "string") {
      throw new Error("写文件路径必须是字符串。");
    }

    if (typeof contentValue !== "string") {
      throw new Error("写文件内容必须是字符串。");
    }

    return {
      path: pathValue,
      content: contentValue
    };
  }
}

function compareWorkspaceDirectoryEntries(
  left: SortableWorkspaceDirectoryEntry,
  right: SortableWorkspaceDirectoryEntry
): number {
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

function removeSortKey(entry: SortableWorkspaceDirectoryEntry): WorkspaceDirectoryEntry {
  return {
    id: entry.id,
    name: entry.name,
    path: entry.path,
    kind: entry.kind
  };
}

/**
 * 把 Node 文件系统异常转换成面向用户的目录读取错误。
 *
 * 工作流：
 * 1. 保留 main process 的真实权限校验和路径边界校验。
 * 2. 对 Windows 常见的 EPERM/EACCES 给出可读原因。
 * 3. 其他异常继续携带原始 message，便于定位磁盘或路径问题。
 */
function createDirectoryReadError(error: unknown, relativePath: string): Error {
  const displayPath = relativePath.trim() === "" ? "." : relativePath;
  const errorCode = getFileSystemErrorCode(error);

  if (errorCode === "EPERM" || errorCode === "EACCES") {
    return new Error(`没有权限读取目录：${displayPath}。请检查 Windows 文件夹权限，或跳过该目录。`);
  }

  if (errorCode === "ENOENT") {
    return new Error(`目录不存在或已被删除：${displayPath}。请刷新资源管理器。`);
  }

  if (error instanceof Error) {
    return new Error(`读取目录失败：${displayPath}。${error.message}`);
  }

  return new Error(`读取目录失败：${displayPath}。`);
}

/**
 * 从 Node 文件系统异常中读取稳定错误码。
 */
function getFileSystemErrorCode(error: unknown): string | null {
  if (!isRecord(error)) {
    return null;
  }

  return typeof error.code === "string" ? error.code : null;
}

/**
 * 判断未知值是否是可读取字段的普通对象。
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * 把字节数格式化成用户可读文本。
 */
function formatFileSize(bytes: number): string {
  if (bytes < BYTES_PER_KIBIBYTE) {
    return `${bytes} B`;
  }

  if (bytes < BYTES_PER_MEBIBYTE) {
    return `${(bytes / BYTES_PER_KIBIBYTE).toFixed(1)} KiB`;
  }

  return `${(bytes / BYTES_PER_MEBIBYTE).toFixed(1)} MiB`;
}

/**
 * Electron 主应用入口。
 *
 * 工作流：
 * 1. 初始化路径守卫、文件服务、窗口管理和 IPC 控制器。
 * 2. Electron ready 后注册 IPC 并创建窗口。
 * 3. 处理 macOS 激活和所有窗口关闭事件。
 */
class WorkbenchMainApp {
  private readonly workbenchWindow = new WorkbenchWindow();
  private readonly pathGuard = new WorkspacePathGuard();
  private readonly fileService = new WorkspaceFileService(this.pathGuard);
  private readonly ipcController = new WorkbenchIpcController(
    this.workbenchWindow,
    this.fileService
  );
  // aiBridge 依赖 app.getAppPath()，必须在 app ready 之后初始化
  private aiBridge: WorkbenchAiBridge | null = null;

  public run(): void {
    void app.whenReady().then(() => {
      this.aiBridge = new WorkbenchAiBridge(
        () => this.workbenchWindow.getWindow(),
        getPythonBridgeScriptPath(),
        getCodoRepoRoot()
      );
      this.configureApplicationMenu();
      this.ipcController.register();
      this.aiBridge.registerIpc();
      this.workbenchWindow.create();

      app.on("activate", () => {
        if (BrowserWindow.getAllWindows().length === 0) {
          this.workbenchWindow.create();
        }
      });
    });

    app.on("window-all-closed", () => {
      if (process.platform !== "darwin") {
        app.quit();
      }
    });

    app.on("before-quit", () => {
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
  private configureApplicationMenu(): void {
    Menu.setApplicationMenu(null);
  }
}

function getPythonBridgeScriptPath(): string {
  return path.join(app.getAppPath(), "python", "ai_bridge.py");
}

function getCodoRepoRoot(): string {
  return path.resolve(app.getAppPath(), "..");
}

new WorkbenchMainApp().run();
