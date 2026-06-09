import type {
  WorkspaceDirectoryEntry,
  WorkspaceInfo,
  WorkspaceReadFileResult
} from "../../shared/ipcTypes";
import type { EditorView, ExplorerNode, OpenFile, WorkspaceRelativePath } from "../types";
import { getEditorLanguageId } from "../utils/language";

/**
 * 工作台当前操作状态。
 *
 * 含义：
 * - `idle`：应用刚启动或用户取消操作，还没有进入可工作的项目状态。
 * - `loading`：正在选择工作区、读取目录或打开文件。
 * - `saving`：正在把当前编辑器内容写回磁盘。
 * - `ready`：最近一次操作成功完成，界面可继续操作。
 * - `error`：最近一次操作失败，错误原因放在 `statusMessage`。
 */
export type WorkbenchStatus = "idle" | "loading" | "saving" | "ready" | "error";

export interface WorkbenchState {
  /** 用户通过系统目录选择器打开的工作区。 */
  workspace: WorkspaceInfo | null;

  /**
   * Explorer 当前展示的扁平节点列表。
   *
   * 工作流：
   * 1. 根目录刷新时替换为根目录第一层节点。
   * 2. 展开文件夹时把子节点插入到父节点后面。
   * 3. 收起文件夹时删除它下面已经展开的子孙节点。
   */
  explorerNodes: ExplorerNode[];

  /** 已读取过的目录第一层条目，用于收起后再次展开时避免重复磁盘 I/O。 */
  directoryEntryCache: Record<string, WorkspaceDirectoryEntry[]>;

  /**
   * 当前已打开的编辑器 tab 文件列表。
   *
   * 工作流：
   * 1. 用户从 Explorer 打开文件时，把文件加入这个数组。
   * 2. 如果文件已经在数组里，则不重复加入，只切换 activeFilePath。
   * 3. Explorer 节点仍然不保存文件内容，文件内容只存在这里。
   */
  openFiles: OpenFile[];

  /**
   * 当前正在显示/编辑的 tab 路径。
   * 为 null 时，中间编辑区显示 welcome 空态。
   */
  activeFilePath: WorkspaceRelativePath | null;

  /** 当前编辑区显示 welcome 空态还是文件编辑器。 */
  editorView: EditorView;

  /** Explorer 当前选中的文件或文件夹相对路径。 */
  selectedPath: WorkspaceRelativePath | null;

  /** 当前异步操作状态，用于禁用按钮、切换状态栏样式。 */
  status: WorkbenchStatus;

  /** 展示给用户看的最近一次操作说明或错误消息。 */
  statusMessage: string;
}

export type WorkbenchAction =
  /** 用户开始选择工作区。 */
  | { type: "workspace/select-started" }

  /** 用户选中工作区后，根目录第一层条目读取完成。 */
  | { type: "workspace/selected"; workspace: WorkspaceInfo; entries: WorkspaceDirectoryEntry[] }

  /** 用户关闭系统目录选择器，没有选择工作区。 */
  | { type: "workspace/select-canceled" }

  /** 用户点击刷新 Explorer 根目录。 */
  | { type: "workspace/root-refresh-started" }

  /** Explorer 根目录第一层条目刷新完成。 */
  | { type: "workspace/root-refreshed"; entries: WorkspaceDirectoryEntry[] }

  /** 用户点击未展开的文件夹，开始读取该文件夹第一层。 */
  | { type: "directory/load-started"; path: string }

  /** 文件夹第一层读取完成，把子节点插入 Explorer。 */
  | { type: "directory/loaded"; parentPath: string; entries: WorkspaceDirectoryEntry[] }

  /** 用户点击已展开的文件夹，收起它并移除子孙节点。 */
  | { type: "directory/collapsed"; path: string }

  /** 用户点击文件，开始读取文件内容。 */
  | { type: "file/open-started"; path: string }

  /** 文件内容读取完成，加入 tab 列表并切换到该文件。 */
  | { type: "file/opened"; file: WorkspaceReadFileResult }

  /** Monaco 编辑器内容变化，用于更新 dirty 状态。 */
  | { type: "file/content-changed"; path: string; content: string }

  /** 用户点击保存或按 Ctrl+S，开始写回磁盘。 */
  | { type: "file/save-started"; path: string }

  /** 文件写回磁盘成功，更新 savedContent。 */
  | { type: "file/saved"; path: string; content: string }

  /** 用户点击某个编辑器 tab，把它设为当前显示文件。 */
  | { type: "editor/tab-selected"; path: WorkspaceRelativePath }

  /** 用户从右侧 Agent Team 面板打开中间可视化团队页。 */
  | { type: "editor/agents-selected" }

  /** 用户关闭指定编辑器 tab。 */
  | { type: "editor/tab-closed"; path: WorkspaceRelativePath }

  /** 用户关闭当前编辑器 tab。 */
  | { type: "editor/closed" }

  /** 任意异步操作失败，错误信息进入状态栏。 */
  | { type: "operation/failed"; message: string };

/**
 * 创建工作台初始状态。
 *
 * 工作流：
 * 1. 应用启动时还没有 workspace。
 * 2. Explorer 没有节点。
 * 3. 中间编辑区显示 welcome 空态。
 */
export function createInitialWorkbenchState(): WorkbenchState {
  return {
    workspace: null,
    explorerNodes: [],
    directoryEntryCache: {},
    openFiles: [],
    activeFilePath: null,
    editorView: "welcome",
    selectedPath: null,
    status: "idle",
    statusMessage: "请选择一个工作区。"
  };
}

/**
 * 工作台状态 reducer。
 *
 * 工作流：
 * 1. UI 层发起异步操作，例如选择工作区、读取目录、打开文件。
 * 2. 异步操作完成后派发 action。
 * 3. reducer 只根据 action 生成新状态，不直接访问文件系统。
 */
export function workbenchReducer(
  state: WorkbenchState,
  action: WorkbenchAction
): WorkbenchState {
  switch (action.type) {
    case "workspace/select-started":
      return {
        ...state,
        status: "loading",
        statusMessage: "正在选择工作区..."
      };

    case "workspace/selected":
      return {
        ...state,
        workspace: action.workspace,
        explorerNodes: createExplorerNodes(action.entries, 0),
        directoryEntryCache: { "": action.entries },
        openFiles: [],
        activeFilePath: null,
        editorView: "welcome",
        selectedPath: null,
        status: "ready",
        statusMessage: `已打开工作区：${action.workspace.name}`
      };

    case "workspace/select-canceled":
      return {
        ...state,
        status: "idle",
        statusMessage: "已取消选择工作区。"
      };

    case "workspace/root-refresh-started":
      return {
        ...state,
        status: "loading",
        statusMessage: "正在刷新资源管理器..."
      };

    case "workspace/root-refreshed":
      return {
        ...state,
        explorerNodes: createExplorerNodes(action.entries, 0),
        directoryEntryCache: { "": action.entries },
        selectedPath: state.activeFilePath,
        status: "ready",
        statusMessage: "资源管理器根目录已刷新。"
      };

    case "directory/load-started":
      return {
        ...state,
        explorerNodes: setDirectoryLoading(state.explorerNodes, action.path, true),
        selectedPath: action.path,
        status: "loading",
        statusMessage: "正在读取目录..."
      };

    case "directory/loaded":
      return {
        ...state,
        explorerNodes: replaceDirectoryChildren(
          state.explorerNodes,
          action.parentPath,
          action.entries
        ),
        directoryEntryCache: {
          ...state.directoryEntryCache,
          [action.parentPath]: action.entries
        },
        selectedPath: action.parentPath,
        status: "ready",
        statusMessage: "目录已更新。"
      };

    case "directory/collapsed":
      return collapseDirectory(state, action.path);

    case "file/open-started":
      return {
        ...state,
        selectedPath: action.path,
        status: "loading",
        statusMessage: "正在打开文件..."
      };

    case "file/opened":
      return openFileInEditorTabs(state, action.file);

    case "file/content-changed":
      return updateOpenFileContent(state, action.path, action.content);

    case "file/save-started":
      return {
        ...state,
        selectedPath: action.path,
        status: "saving",
        statusMessage: "正在保存文件..."
      };

    case "file/saved":
      return updateSavedOpenFile(state, action.path, action.content);

    case "editor/tab-selected":
      return selectEditorTab(state, action.path);

    case "editor/agents-selected":
      return {
        ...state,
        editorView: "agents",
        status: "ready",
        statusMessage: "已打开 Agent Team 视图。"
      };

    case "editor/tab-closed":
      return closeEditorTab(state, action.path);

    case "editor/closed":
      return closeActiveEditorTab(state);

    case "operation/failed":
      return {
        ...state,
        explorerNodes: clearDirectoryLoading(state.explorerNodes),
        status: "error",
        statusMessage: action.message
      };
  }
}

/**
 * 把 main process 返回的目录条目转换成 Explorer 节点。
 *
 * 工作流：
 * 1. 文件夹节点只保存元信息和展开状态。
 * 2. 文件节点只保存元信息和 Monaco 语言标识，不保存 content。
 * 3. 节点深度由调用方传入，避免组件渲染时临时推断层级。
 */
function createExplorerNodes(
  entries: WorkspaceDirectoryEntry[],
  depth: number
): ExplorerNode[] {
  return entries.map((entry): ExplorerNode => {
    if (entry.kind === "folder") {
      return {
        id: entry.id,
        name: entry.name,
        path: entry.path,
        depth,
        kind: "folder",
        expanded: false,
        loading: false
      };
    }

    return {
      id: entry.id,
      name: entry.name,
      path: entry.path,
      depth,
      kind: "file",
      language: getEditorLanguageId(entry.name)
    };
  });
}

/**
 * 替换某个目录节点的子节点。
 *
 * 工作流：
 * 1. 找到被展开的父目录。
 * 2. 移除它旧的直接/间接子节点。
 * 3. 把新读取到的一层子节点插入父目录后面，并把父目录标记为 expanded。
 */
function replaceDirectoryChildren(
  nodes: ExplorerNode[],
  parentPath: string,
  entries: WorkspaceDirectoryEntry[]
): ExplorerNode[] {
  const parentIndex = nodes.findIndex((node) => node.path === parentPath);

  if (parentIndex === -1) {
    return nodes;
  }

  const parentNode = nodes[parentIndex];
  const childDepth = parentNode.depth + 1;
  const nextSiblingIndex = findNextSiblingIndex(nodes, parentIndex);
  const childNodes = createExplorerNodes(entries, childDepth);
  const updatedParentNode: ExplorerNode =
    parentNode.kind === "folder"
      ? { ...parentNode, expanded: true, loading: false }
      : parentNode;

  return [
    ...nodes.slice(0, parentIndex),
    updatedParentNode,
    ...childNodes,
    ...nodes.slice(nextSiblingIndex)
  ];
}

/**
 * 收起某个 Explorer 文件夹。
 *
 * 工作流：
 * 1. 找到目标文件夹节点。
 * 2. 删除它后方连续的所有子孙节点。
 * 3. 把目标文件夹标记为未展开；如果当前选中项在被收起目录内，则选中目录本身。
 */
function collapseDirectory(state: WorkbenchState, directoryPath: string): WorkbenchState {
  return {
    ...state,
    explorerNodes: collapseDirectoryChildren(state.explorerNodes, directoryPath),
    selectedPath: getSelectedPathAfterCollapse(state.selectedPath, directoryPath),
    status: "ready",
    statusMessage: "目录已收起。"
  };
}

/**
 * 从扁平 Explorer 节点列表中移除某个目录的子孙节点。
 */
function collapseDirectoryChildren(
  nodes: ExplorerNode[],
  directoryPath: string
): ExplorerNode[] {
  const parentIndex = nodes.findIndex((node) => node.path === directoryPath);

  if (parentIndex === -1) {
    return nodes;
  }

  const parentNode = nodes[parentIndex];

  if (parentNode.kind !== "folder") {
    return nodes;
  }

  const nextSiblingIndex = findNextSiblingIndex(nodes, parentIndex);

  return [
    ...nodes.slice(0, parentIndex),
    { ...parentNode, expanded: false, loading: false },
    ...nodes.slice(nextSiblingIndex)
  ];
}

/**
 * 标记某个目录正在读取，让用户点击后立即看到反馈。
 */
function setDirectoryLoading(
  nodes: ExplorerNode[],
  directoryPath: string,
  loading: boolean
): ExplorerNode[] {
  let changed = false;

  const nextNodes = nodes.map((node): ExplorerNode => {
    if (node.kind !== "folder" || node.path !== directoryPath) {
      return node;
    }

    changed = true;
    return { ...node, loading };
  });

  return changed ? nextNodes : nodes;
}

/**
 * 异常结束时清掉残留 loading 状态。
 */
function clearDirectoryLoading(nodes: ExplorerNode[]): ExplorerNode[] {
  let changed = false;

  const nextNodes = nodes.map((node): ExplorerNode => {
    if (node.kind !== "folder" || !node.loading) {
      return node;
    }

    changed = true;
    return { ...node, loading: false };
  });

  return changed ? nextNodes : nodes;
}

/**
 * 收起目录后修正当前选中路径。
 */
function getSelectedPathAfterCollapse(
  selectedPath: string | null,
  directoryPath: string
): string | null {
  if (selectedPath === null) {
    return directoryPath;
  }

  if (isSameOrDescendantPath(selectedPath, directoryPath)) {
    return directoryPath;
  }

  return selectedPath;
}

/**
 * 判断 candidatePath 是否等于 parentPath 或位于 parentPath 下方。
 */
function isSameOrDescendantPath(candidatePath: string, parentPath: string): boolean {
  const normalizedCandidatePath = normalizeExplorerPath(candidatePath);
  const normalizedParentPath = normalizeExplorerPath(parentPath);

  return (
    normalizedCandidatePath === normalizedParentPath ||
    normalizedCandidatePath.startsWith(`${normalizedParentPath}/`)
  );
}

/**
 * 统一 Explorer 路径分隔符，避免 Windows 反斜杠影响前端路径比较。
 */
function normalizeExplorerPath(value: string): string {
  return value.replaceAll("\\", "/");
}

/**
 * 查找当前节点之后第一个非子孙节点的位置。
 */
function findNextSiblingIndex(nodes: ExplorerNode[], parentIndex: number): number {
  const parentDepth = nodes[parentIndex].depth;

  for (let index = parentIndex + 1; index < nodes.length; index += 1) {
    if (nodes[index].depth <= parentDepth) {
      return index;
    }
  }

  return nodes.length;
}

/**
 * 把文件读取结果转换成编辑器打开文件状态。
 */
function createOpenFile(file: WorkspaceReadFileResult): OpenFile {
  return {
    id: file.path,
    name: file.name,
    path: file.path,
    language: getEditorLanguageId(file.name),
    savedContent: file.content,
    content: file.content
  };
}

/**
 * 获取当前激活的编辑器 tab 文件。
 *
 * 工作流：
 * 1. activeFilePath 为 null 时，没有激活文件。
 * 2. activeFilePath 有值时，从 openFiles 中查找对应文件。
 * 3. 如果状态异常导致找不到文件，返回 null，让 UI 回到安全空态。
 */
export function getActiveOpenFile(state: WorkbenchState): OpenFile | null {
  if (state.activeFilePath === null) {
    return null;
  }

  return state.openFiles.find((file) => file.path === state.activeFilePath) ?? null;
}

/**
 * 判断打开文件是否存在未保存改动。
 *
 * 工作流：
 * 1. 没有打开文件时返回 false。
 * 2. 有打开文件时，对比编辑器当前内容和最近一次磁盘内容。
 */
export function isOpenFileDirty(openFile: OpenFile | null): boolean {
  return openFile !== null && openFile.content !== openFile.savedContent;
}

/**
 * 判断某个路径对应的 tab 是否存在未保存改动。
 *
 * 工作流：
 * 1. tab 操作通常只知道 path。
 * 2. 先根据 path 找到对应 OpenFile。
 * 3. 再复用 isOpenFileDirty 判断 dirty。
 */
export function isOpenFilePathDirty(
  openFiles: OpenFile[],
  path: WorkspaceRelativePath
): boolean {
  return isOpenFileDirty(openFiles.find((file) => file.path === path) ?? null);
}

/**
 * 把读取到的文件放入编辑器 tab 列表。
 *
 * 工作流：
 * 1. 如果文件已经打开，不覆盖现有 tab 内容，避免丢失未保存编辑。
 * 2. 如果文件没打开，把它追加到 openFiles。
 * 3. 无论是否新建 tab，都把 activeFilePath 切到这个文件。
 */
function openFileInEditorTabs(
  state: WorkbenchState,
  file: WorkspaceReadFileResult
): WorkbenchState {
  const nextOpenFile = createOpenFile(file);
  const openedFile = state.openFiles.find((openFile) => openFile.path === nextOpenFile.path);

  if (openedFile !== undefined) {
    return {
      ...state,
      activeFilePath: openedFile.path,
      editorView: "file",
      selectedPath: openedFile.path,
      status: "ready",
      statusMessage: `已切换到文件：${openedFile.name}`
    };
  }

  return {
    ...state,
    openFiles: [...state.openFiles, nextOpenFile],
    activeFilePath: nextOpenFile.path,
    editorView: "file",
    selectedPath: nextOpenFile.path,
    status: "ready",
    statusMessage: `已打开文件：${nextOpenFile.name}`
  };
}

/**
 * 切换当前编辑器 tab。
 *
 * 工作流：
 * 1. UI 传入用户点击的 tab 路径。
 * 2. reducer 确认这个路径已经存在于 openFiles。
 * 3. 切换 activeFilePath，Monaco 后续显示对应文件内容。
 */
function selectEditorTab(
  state: WorkbenchState,
  path: WorkspaceRelativePath
): WorkbenchState {
  const targetFile = state.openFiles.find((file) => file.path === path);

  if (targetFile === undefined) {
    return state;
  }

  return {
    ...state,
    activeFilePath: targetFile.path,
    editorView: "file",
    selectedPath: targetFile.path,
    status: "ready",
    statusMessage: `已切换到文件：${targetFile.name}`
  };
}

/**
 * 关闭当前激活的编辑器 tab。
 */
function closeActiveEditorTab(state: WorkbenchState): WorkbenchState {
  if (state.activeFilePath === null) {
    return state;
  }

  return closeEditorTab(state, state.activeFilePath);
}

/**
 * 关闭指定编辑器 tab。
 *
 * 工作流：
 * 1. 从 openFiles 中移除目标路径。
 * 2. 如果关闭的是当前激活 tab，则自动选择右侧 tab；没有右侧时选择左侧。
 * 3. 如果没有任何 tab，编辑区回到 welcome 空态。
 */
function closeEditorTab(
  state: WorkbenchState,
  path: WorkspaceRelativePath
): WorkbenchState {
  const closedIndex = state.openFiles.findIndex((file) => file.path === path);

  if (closedIndex === -1) {
    return state;
  }

  const closedFile = state.openFiles[closedIndex];
  const nextOpenFiles = state.openFiles.filter((file) => file.path !== path);
  const nextActiveFilePath = getNextActiveFilePathAfterClose(
    state.activeFilePath,
    path,
    nextOpenFiles,
    closedIndex
  );

  return {
    ...state,
    openFiles: nextOpenFiles,
    activeFilePath: nextActiveFilePath,
    editorView: nextActiveFilePath === null ? "welcome" : "file",
    selectedPath: nextActiveFilePath ?? state.selectedPath,
    status: "ready",
    statusMessage: `已关闭文件：${closedFile.name}`
  };
}

/**
 * 关闭 tab 后计算下一个激活 tab。
 */
function getNextActiveFilePathAfterClose(
  activeFilePath: WorkspaceRelativePath | null,
  closedPath: WorkspaceRelativePath,
  nextOpenFiles: OpenFile[],
  closedIndex: number
): WorkspaceRelativePath | null {
  if (nextOpenFiles.length === 0) {
    return null;
  }

  if (activeFilePath !== closedPath) {
    return activeFilePath;
  }

  const nextIndex = Math.min(closedIndex, nextOpenFiles.length - 1);
  return nextOpenFiles[nextIndex].path;
}

/**
 * 更新某个已打开 tab 的编辑器内容。
 *
 * 工作流：
 * 1. Monaco 内容变化后派发 action。
 * 2. 根据 path 精确更新对应 tab，避免误改其他文件。
 * 3. 保存过程中继续编辑时保留 saving 状态，等待保存结果回来。
 */
function updateOpenFileContent(
  state: WorkbenchState,
  path: WorkspaceRelativePath,
  content: string
): WorkbenchState {
  const targetFile = state.openFiles.find((file) => file.path === path);

  if (targetFile === undefined) {
    return state;
  }

  const nextOpenFile: OpenFile = {
    ...targetFile,
    content
  };
  const isDirty = nextOpenFile.content !== nextOpenFile.savedContent;
  const shouldKeepSavingStatus = state.status === "saving";
  const nextOpenFiles = replaceOpenFile(state.openFiles, nextOpenFile);

  return {
    ...state,
    openFiles: nextOpenFiles,
    status: shouldKeepSavingStatus ? state.status : "ready",
    statusMessage: shouldKeepSavingStatus
      ? state.statusMessage
      : getContentChangedStatusMessage(nextOpenFile.name, isDirty)
  };
}

/**
 * 标记当前打开文件保存完成。
 *
 * 工作流：
 * 1. 保存成功后，把本次写入内容记录为 savedContent。
 * 2. 如果保存过程中用户又继续编辑，content 会保留新内容。
 * 3. UI 继续通过 content/savedContent 对比判断是否仍然 dirty。
 */
function updateSavedOpenFile(
  state: WorkbenchState,
  path: WorkspaceRelativePath,
  content: string
): WorkbenchState {
  const targetFile = state.openFiles.find((file) => file.path === path);

  if (targetFile === undefined) {
    return {
      ...state,
      status: "ready",
      statusMessage: "文件已保存。"
    };
  }

  const nextOpenFile: OpenFile = {
    ...targetFile,
    savedContent: content
  };
  const isDirty = nextOpenFile.content !== nextOpenFile.savedContent;
  const nextOpenFiles = replaceOpenFile(state.openFiles, nextOpenFile);

  return {
    ...state,
    openFiles: nextOpenFiles,
    status: "ready",
    statusMessage: isDirty
      ? "文件已保存，当前编辑器还有新的未保存改动。"
      : `已保存文件：${nextOpenFile.name}`
  };
}

/**
 * 生成编辑内容变化后的状态提示。
 */
function getContentChangedStatusMessage(fileName: string, isDirty: boolean): string {
  return isDirty ? `未保存改动：${fileName}` : `已恢复到磁盘内容：${fileName}`;
}

/**
 * 用新的 OpenFile 替换 openFiles 中路径相同的文件。
 */
function replaceOpenFile(openFiles: OpenFile[], nextOpenFile: OpenFile): OpenFile[] {
  return openFiles.map((openFile) =>
    openFile.path === nextOpenFile.path ? nextOpenFile : openFile
  );
}
