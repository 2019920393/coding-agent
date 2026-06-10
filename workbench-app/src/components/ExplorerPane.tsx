import { useEffect, useMemo, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  RefObject
} from "react";
import type {
  WorkspaceCreateEntryRequest,
  WorkspaceDeleteEntryRequest,
  WorkspaceDirectoryEntryKind,
  WorkspaceRenameEntryRequest
} from "../../shared/ipcTypes";
import type { ExplorerNode } from "../types";
import type { WorkbenchStatus } from "../state/workbenchState";

const EXPLORER_ROW_HEIGHT = 26;
const EXPLORER_OVERSCAN_ROWS = 8;

interface ExplorerPaneProps {
  workspaceName: string | null;
  nodes: ExplorerNode[];
  selectedPath: string | null;
  status: WorkbenchStatus;
  onSelectWorkspace: () => void;
  onRefreshWorkspace: () => void;
  onOpenFolder: (path: string) => void;
  onOpenFile: (path: string) => void;
  onCreateEntry: (request: WorkspaceCreateEntryRequest) => Promise<boolean>;
  onRenameEntry: (request: WorkspaceRenameEntryRequest) => Promise<boolean>;
  onDeleteEntry: (request: WorkspaceDeleteEntryRequest) => Promise<boolean>;
}

interface ExplorerContextMenuState {
  x: number;
  y: number;
  target: ExplorerNode | null;
}

type ExplorerInlineEditState =
  | {
      mode: "create";
      parentPath: string;
      kind: WorkspaceDirectoryEntryKind;
      value: string;
    }
  | {
      mode: "rename";
      path: string;
      kind: WorkspaceDirectoryEntryKind;
      originalName: string;
      value: string;
    };

type ExplorerRenderItem =
  | { kind: "node"; node: ExplorerNode }
  | {
      kind: "inline-create";
      parentPath: string;
      entryKind: WorkspaceDirectoryEntryKind;
      depth: number;
    };

/**
 * 左侧资源管理器面板。
 *
 * 工作流：
 * 1. 没有 workspace 时展示选择入口。
 * 2. 有 workspace 后展示扁平化文件树。
 * 3. 点击文件夹/文件只抛事件给上层，不在组件里读文件系统。
 */
export function ExplorerPane({
  workspaceName,
  nodes,
  selectedPath,
  status,
  onSelectWorkspace,
  onRefreshWorkspace,
  onOpenFolder,
  onOpenFile,
  onCreateEntry,
  onRenameEntry,
  onDeleteEntry
}: ExplorerPaneProps) {
  const isBusy = status === "loading" || status === "saving";
  const workspaceIsSelected = workspaceName !== null;
  const treeRef = useRef<HTMLDivElement | null>(null);
  const contextMenuRef = useRef<HTMLDivElement | null>(null);
  const inlineInputRef = useRef<HTMLInputElement | null>(null);
  const committingInlineEditRef = useRef(false);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  const [contextMenu, setContextMenu] = useState<ExplorerContextMenuState | null>(null);
  const [inlineEdit, setInlineEdit] = useState<ExplorerInlineEditState | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState<ExplorerNode | null>(null);
  const [deleteIsPending, setDeleteIsPending] = useState(false);
  const renderItems = useMemo(
    () => buildExplorerRenderItems(nodes, inlineEdit),
    [nodes, inlineEdit]
  );
  const visibleRange = useMemo(
    () => getVisibleNodeRange(renderItems.length, scrollTop, viewportHeight),
    [renderItems.length, scrollTop, viewportHeight]
  );
  const visibleItems = renderItems.slice(visibleRange.startIndex, visibleRange.endIndex);
  const topSpacerHeight = visibleRange.startIndex * EXPLORER_ROW_HEIGHT;
  const bottomSpacerHeight = (renderItems.length - visibleRange.endIndex) * EXPLORER_ROW_HEIGHT;
  const inlineEditFocusKey = getInlineEditFocusKey(inlineEdit);

  useEffect(() => {
    const treeElement = treeRef.current;
    if (treeElement === null) {
      return;
    }

    const updateViewportHeight = () => {
      setViewportHeight(treeElement.clientHeight);
    };

    updateViewportHeight();

    const resizeObserver = new ResizeObserver(updateViewportHeight);
    resizeObserver.observe(treeElement);

    return () => {
      resizeObserver.disconnect();
    };
  }, []);

  useEffect(() => {
    if (contextMenu === null) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const eventTarget = event.target;
      if (
        eventTarget instanceof Node &&
        contextMenuRef.current !== null &&
        contextMenuRef.current.contains(eventTarget)
      ) {
        return;
      }

      setContextMenu(null);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setContextMenu(null);
      }
    };

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    contextMenuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();

    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (inlineEdit === null) {
      return;
    }

    inlineInputRef.current?.focus();
    inlineInputRef.current?.select();
  }, [inlineEditFocusKey]);

  const openContextMenu = (
    event: ReactMouseEvent<HTMLElement>,
    target: ExplorerNode | null
  ) => {
    if (!workspaceIsSelected || isBusy) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      target
    });
  };

  const startCreateEntry = (
    parentPath: string,
    kind: WorkspaceDirectoryEntryKind
  ) => {
    committingInlineEditRef.current = false;
    setContextMenu(null);
    setInlineEdit({
      mode: "create",
      parentPath,
      kind,
      value: ""
    });
  };

  const startRenameEntry = (node: ExplorerNode) => {
    committingInlineEditRef.current = false;
    setContextMenu(null);
    setInlineEdit({
      mode: "rename",
      path: node.path,
      kind: node.kind,
      originalName: node.name,
      value: node.name
    });
  };

  const cancelInlineEdit = () => {
    committingInlineEditRef.current = true;
    setInlineEdit(null);
  };

  const commitInlineEdit = async () => {
    if (committingInlineEditRef.current || inlineEdit === null) {
      return;
    }

    const edit = inlineEdit;
    const nextName = edit.value.trim();

    if (nextName.length === 0) {
      cancelInlineEdit();
      return;
    }

    if (edit.mode === "rename" && nextName === edit.originalName) {
      cancelInlineEdit();
      return;
    }

    committingInlineEditRef.current = true;
    setInlineEdit(null);

    try {
      if (edit.mode === "create") {
        await onCreateEntry({
          parentPath: edit.parentPath,
          name: nextName,
          kind: edit.kind
        });
      } else {
        await onRenameEntry({
          path: edit.path,
          newName: nextName
        });
      }
    } finally {
      committingInlineEditRef.current = false;
    }
  };

  const handleInlineInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void commitInlineEdit();
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      cancelInlineEdit();
    }
  };

  const updateInlineValue = (value: string) => {
    setInlineEdit((current) => (current === null ? null : { ...current, value }));
  };

  const requestDeleteEntry = (node: ExplorerNode) => {
    setContextMenu(null);
    setDeleteConfirmation(node);
  };

  const cancelDeleteEntry = () => {
    if (deleteIsPending) {
      return;
    }

    setDeleteConfirmation(null);
  };

  const confirmDeleteEntry = async () => {
    if (deleteConfirmation === null || deleteIsPending) {
      return;
    }

    const node = deleteConfirmation;
    setDeleteIsPending(true);

    try {
      await onDeleteEntry({ path: node.path });
      setDeleteConfirmation(null);
    } finally {
      setDeleteIsPending(false);
    }
  };

  return (
    <aside className="explorer-pane" aria-label="资源管理器">
      <header className="explorer-pane__header">
        <h1>EXPLORER</h1>
        <div className="explorer-pane__actions" aria-label="资源管理器操作">
          <button
            className="explorer-pane__icon-button"
            type="button"
            onClick={onRefreshWorkspace}
            disabled={isBusy || !workspaceIsSelected}
            title="刷新工作区根目录"
            aria-label="刷新工作区根目录"
          >
            ↻
          </button>
          <button
            className="explorer-pane__open-button"
            type="button"
            onClick={onSelectWorkspace}
            disabled={isBusy}
          >
            打开工作区
          </button>
        </div>
      </header>

      <section className="explorer-pane__workspace" aria-live="polite">
        <span className="explorer-pane__workspace-label">WORKSPACE</span>
        <span className="explorer-pane__workspace-name">
          {workspaceName === null ? "未选择工作区" : workspaceName}
        </span>
      </section>

      <div
        className="explorer-tree"
        role="tree"
        aria-label="工作区文件树"
        ref={treeRef}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        onContextMenu={(event) => openContextMenu(event, null)}
      >
        {renderItems.length === 0 ? (
          <p className="explorer-tree__empty">
            {workspaceIsSelected ? "当前工作区为空。右键可新建文件。" : "选择工作区后显示文件。"}
          </p>
        ) : (
          <>
            {topSpacerHeight > 0 ? (
              <div
                aria-hidden="true"
                className="explorer-tree__virtual-spacer"
                style={{ height: `${topSpacerHeight}px` }}
              />
            ) : null}
            {visibleItems.map((item) =>
              item.kind === "node" ? (
                <ExplorerTreeRow
                  key={item.node.id}
                  node={item.node}
                  selected={selectedPath === item.node.path}
                  inlineEdit={getInlineRenameEdit(inlineEdit, item.node.path)}
                  inputRef={inlineInputRef}
                  onInlineValueChange={updateInlineValue}
                  onInlineKeyDown={handleInlineInputKeyDown}
                  onInlineBlur={() => {
                    void commitInlineEdit();
                  }}
                  onContextMenu={(event) => openContextMenu(event, item.node)}
                  onOpenFolder={onOpenFolder}
                  onOpenFile={onOpenFile}
                />
              ) : (
                <ExplorerInlineInputRow
                  key={`create-${item.parentPath}-${item.entryKind}`}
                  depth={item.depth}
                  icon={getInlineCreateIcon(item.entryKind)}
                  value={inlineEdit?.mode === "create" ? inlineEdit.value : ""}
                  placeholder={getInlineCreatePlaceholder(item.entryKind)}
                  inputRef={inlineInputRef}
                  onValueChange={updateInlineValue}
                  onKeyDown={handleInlineInputKeyDown}
                  onBlur={() => {
                    void commitInlineEdit();
                  }}
                />
              )
            )}
            {bottomSpacerHeight > 0 ? (
              <div
                aria-hidden="true"
                className="explorer-tree__virtual-spacer"
                style={{ height: `${bottomSpacerHeight}px` }}
              />
            ) : null}
          </>
        )}
        {contextMenu !== null ? (
          <ExplorerContextMenu
            menuRef={contextMenuRef}
            state={contextMenu}
            onCreateFile={(parentPath) => startCreateEntry(parentPath, "file")}
            onCreateFolder={(parentPath) => startCreateEntry(parentPath, "folder")}
            onRename={startRenameEntry}
            onDelete={requestDeleteEntry}
          />
        ) : null}
        {deleteConfirmation !== null ? (
          <ExplorerDeleteConfirmation
            node={deleteConfirmation}
            deleting={deleteIsPending}
            onCancel={cancelDeleteEntry}
            onConfirm={() => {
              void confirmDeleteEntry();
            }}
          />
        ) : null}
      </div>
    </aside>
  );
}

interface ExplorerTreeRowProps {
  node: ExplorerNode;
  selected: boolean;
  inlineEdit: Extract<ExplorerInlineEditState, { mode: "rename" }> | null;
  inputRef: RefObject<HTMLInputElement | null>;
  onInlineValueChange: (value: string) => void;
  onInlineKeyDown: (event: ReactKeyboardEvent<HTMLInputElement>) => void;
  onInlineBlur: () => void;
  onContextMenu: (event: ReactMouseEvent<HTMLButtonElement | HTMLDivElement>) => void;
  onOpenFolder: (path: string) => void;
  onOpenFile: (path: string) => void;
}

/**
 * 单个 Explorer 节点行。
 *
 * 工作流：
 * 1. 文件夹点击后交给上层判断展开或收起。
 * 2. 文件点击后请求上层读取文件内容。
 * 3. 缩进由节点 depth 控制，组件不推断层级。
 */
function ExplorerTreeRow({
  node,
  selected,
  inlineEdit,
  inputRef,
  onInlineValueChange,
  onInlineKeyDown,
  onInlineBlur,
  onContextMenu,
  onOpenFolder,
  onOpenFile
}: ExplorerTreeRowProps) {
  const rowStyle = {
    paddingLeft: `${8 + node.depth * 18}px`
  };
  const icon = getExplorerNodeIcon(node);
  const isLoadingFolder = node.kind === "folder" && node.loading;
  const rowClassName = [
    "explorer-tree__row",
    selected ? "explorer-tree__row--selected" : "",
    isLoadingFolder ? "explorer-tree__row--loading" : ""
  ]
    .filter(Boolean)
    .join(" ");

  const handleClick = () => {
    if (isLoadingFolder) {
      return;
    }

    if (node.kind === "folder") {
      onOpenFolder(node.path);
      return;
    }

    onOpenFile(node.path);
  };

  if (inlineEdit !== null) {
    return (
      <ExplorerInlineInputRow
        depth={node.depth}
        icon={getExplorerNodeIcon(node)}
        value={inlineEdit.value}
        placeholder="输入新名称"
        inputRef={inputRef}
        onValueChange={onInlineValueChange}
        onKeyDown={onInlineKeyDown}
        onBlur={onInlineBlur}
        onContextMenu={onContextMenu}
      />
    );
  }

  return (
    <button
      className={rowClassName}
      type="button"
      role="treeitem"
      aria-busy={isLoadingFolder ? true : undefined}
      aria-expanded={node.kind === "folder" ? node.expanded : undefined}
      style={rowStyle}
      onClick={handleClick}
      onContextMenu={onContextMenu}
    >
      <span className="explorer-tree__twisty" aria-hidden="true">
        {getExplorerNodeTwisty(node)}
      </span>
      <span className={`explorer-tree__icon explorer-tree__icon--${icon.variant}`} aria-hidden="true">
        {icon.label}
      </span>
      <span className={`explorer-tree__name explorer-tree__name--${node.kind}`}>{node.name}</span>
    </button>
  );
}

interface ExplorerInlineInputRowProps {
  depth: number;
  icon: ExplorerNodeIcon;
  value: string;
  placeholder: string;
  inputRef: RefObject<HTMLInputElement | null>;
  onValueChange: (value: string) => void;
  onKeyDown: (event: ReactKeyboardEvent<HTMLInputElement>) => void;
  onBlur: () => void;
  onContextMenu?: (event: ReactMouseEvent<HTMLDivElement>) => void;
}

function ExplorerInlineInputRow({
  depth,
  icon,
  value,
  placeholder,
  inputRef,
  onValueChange,
  onKeyDown,
  onBlur,
  onContextMenu
}: ExplorerInlineInputRowProps) {
  return (
    <div
      className="explorer-tree__row explorer-tree__row--editing"
      role="treeitem"
      style={{ paddingLeft: `${8 + depth * 18}px` }}
      onContextMenu={onContextMenu}
    >
      <span className="explorer-tree__twisty" aria-hidden="true" />
      <span className={`explorer-tree__icon explorer-tree__icon--${icon.variant}`} aria-hidden="true">
        {icon.label}
      </span>
      <input
        ref={inputRef}
        className="explorer-tree__inline-input"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onValueChange(event.target.value)}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
      />
    </div>
  );
}

interface ExplorerContextMenuProps {
  menuRef: RefObject<HTMLDivElement | null>;
  state: ExplorerContextMenuState;
  onCreateFile: (parentPath: string) => void;
  onCreateFolder: (parentPath: string) => void;
  onRename: (node: ExplorerNode) => void;
  onDelete: (node: ExplorerNode) => void;
}

function ExplorerContextMenu({
  menuRef,
  state,
  onCreateFile,
  onCreateFolder,
  onRename,
  onDelete
}: ExplorerContextMenuProps) {
  const target = state.target;
  const canCreate = target === null || target.kind === "folder";
  const createParentPath = target?.kind === "folder" ? target.path : "";

  return (
    <div
      ref={menuRef}
      className="explorer-context-menu"
      role="menu"
      style={{ left: `${state.x}px`, top: `${state.y}px` }}
      onContextMenu={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
    >
      {canCreate ? (
        <>
          <button type="button" role="menuitem" onClick={() => onCreateFile(createParentPath)}>
            新建文件
          </button>
          <button type="button" role="menuitem" onClick={() => onCreateFolder(createParentPath)}>
            新建文件夹
          </button>
          {target !== null ? <span className="explorer-context-menu__separator" /> : null}
        </>
      ) : null}
      {target !== null ? (
        <>
          <button type="button" role="menuitem" onClick={() => onRename(target)}>
            重命名
          </button>
          <button
            type="button"
            role="menuitem"
            className="explorer-context-menu__danger"
            onClick={() => onDelete(target)}
          >
            删除
          </button>
        </>
      ) : null}
    </div>
  );
}

interface ExplorerDeleteConfirmationProps {
  node: ExplorerNode;
  deleting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

function ExplorerDeleteConfirmation({
  node,
  deleting,
  onCancel,
  onConfirm
}: ExplorerDeleteConfirmationProps) {
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const title = node.kind === "folder" ? "删除文件夹" : "删除文件";
  const description =
    node.kind === "folder"
      ? "将递归删除该文件夹及其内容，这个操作无法撤销。"
      : "这个文件会被永久删除，操作无法撤销。";

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCancel();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    cancelButtonRef.current?.focus();

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onCancel]);

  return (
    <div
      className="explorer-delete-confirmation"
      role="presentation"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) {
          onCancel();
        }
      }}
    >
      <section
        className="explorer-delete-confirmation__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="explorer-delete-confirmation-title"
      >
        <div className="explorer-delete-confirmation__mark" aria-hidden="true">
          !
        </div>
        <div className="explorer-delete-confirmation__content">
          <h2 id="explorer-delete-confirmation-title">{title}</h2>
          <p className="explorer-delete-confirmation__name">{node.name}</p>
          <p className="explorer-delete-confirmation__description">{description}</p>
          <div className="explorer-delete-confirmation__actions">
            <button
              ref={cancelButtonRef}
              type="button"
              className="explorer-delete-confirmation__button"
              disabled={deleting}
              onClick={onCancel}
            >
              取消
            </button>
            <button
              type="button"
              className="explorer-delete-confirmation__button explorer-delete-confirmation__button--danger"
              disabled={deleting}
              onClick={onConfirm}
            >
              {deleting ? "删除中" : "删除"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function buildExplorerRenderItems(
  nodes: ExplorerNode[],
  inlineEdit: ExplorerInlineEditState | null
): ExplorerRenderItem[] {
  const items: ExplorerRenderItem[] = [];

  if (inlineEdit?.mode === "create" && inlineEdit.parentPath === "") {
    items.push({
      kind: "inline-create",
      parentPath: "",
      entryKind: inlineEdit.kind,
      depth: 0
    });
  }

  for (const node of nodes) {
    items.push({ kind: "node", node });

    if (
      inlineEdit?.mode === "create" &&
      inlineEdit.parentPath === node.path &&
      node.kind === "folder"
    ) {
      items.push({
        kind: "inline-create",
        parentPath: inlineEdit.parentPath,
        entryKind: inlineEdit.kind,
        depth: node.depth + 1
      });
    }
  }

  return items;
}

function getInlineRenameEdit(
  inlineEdit: ExplorerInlineEditState | null,
  nodePath: string
): Extract<ExplorerInlineEditState, { mode: "rename" }> | null {
  if (inlineEdit?.mode !== "rename" || inlineEdit.path !== nodePath) {
    return null;
  }

  return inlineEdit;
}

function getInlineEditFocusKey(inlineEdit: ExplorerInlineEditState | null): string {
  if (inlineEdit === null) {
    return "";
  }

  if (inlineEdit.mode === "create") {
    return `create:${inlineEdit.parentPath}:${inlineEdit.kind}`;
  }

  return `rename:${inlineEdit.path}`;
}

function getInlineCreateIcon(kind: WorkspaceDirectoryEntryKind): ExplorerNodeIcon {
  if (kind === "folder") {
    return { label: "▱", variant: "folder" };
  }

  return { label: "–", variant: "file" };
}

function getInlineCreatePlaceholder(kind: WorkspaceDirectoryEntryKind): string {
  return kind === "folder" ? "新文件夹名称" : "新文件名称";
}

function getExplorerNodeTwisty(node: ExplorerNode): string {
  if (node.kind !== "folder") {
    return "";
  }

  if (node.loading) {
    return "◌";
  }

  return node.expanded ? "▾" : "▸";
}

interface VisibleNodeRange {
  startIndex: number;
  endIndex: number;
}

/**
 * 计算 Explorer 虚拟滚动窗口。
 *
 * 工作流：
 * 1. 根据滚动位置和固定行高算出可见区间。
 * 2. 前后各多渲染少量 overscan，避免快速滚动时出现空白。
 * 3. 返回半开区间，组件只对这个切片执行 map。
 */
function getVisibleNodeRange(
  nodeCount: number,
  scrollTop: number,
  viewportHeight: number
): VisibleNodeRange {
  if (nodeCount === 0) {
    return { startIndex: 0, endIndex: 0 };
  }

  const safeViewportHeight = Math.max(viewportHeight, EXPLORER_ROW_HEIGHT);
  const firstVisibleIndex = Math.floor(scrollTop / EXPLORER_ROW_HEIGHT);
  const visibleRowCount = Math.ceil(safeViewportHeight / EXPLORER_ROW_HEIGHT);
  const startIndex = Math.min(
    nodeCount,
    Math.max(0, firstVisibleIndex - EXPLORER_OVERSCAN_ROWS)
  );
  const endIndex = Math.max(
    startIndex,
    Math.min(nodeCount, firstVisibleIndex + visibleRowCount + EXPLORER_OVERSCAN_ROWS)
  );

  return { startIndex, endIndex };
}

interface ExplorerNodeIcon {
  label: string;
  variant: string;
}

/**
 * 返回 Explorer 行首图标。
 *
 * 工作流：
 * 1. 文件夹用独立 folder 图形，展开状态交给 twisty 表达。
 * 2. 常见源码、配置、文档文件用颜色和短标识区分。
 * 3. 未识别文件保留通用文档图标，不把未知类型伪装成源码。
 */
function getExplorerNodeIcon(node: ExplorerNode): ExplorerNodeIcon {
  if (node.kind === "folder") {
    return {
      label: node.expanded ? "▰" : "▱",
      variant: "folder"
    };
  }

  switch (node.language) {
    case "typescript":
      return { label: "TS", variant: "typescript" };
    case "javascript":
      return { label: "JS", variant: "javascript" };
    case "python":
      return { label: "PY", variant: "python" };
    case "json":
      return { label: "{}", variant: "json" };
    case "markdown":
      return { label: "MD", variant: "markdown" };
    case "css":
    case "scss":
    case "less":
      return { label: "#", variant: "style" };
    case "html":
    case "xml":
      return { label: "<>", variant: "markup" };
    case "yaml":
      return { label: "Y", variant: "yaml" };
    case "shell":
    case "bat":
    case "powershell":
      return { label: "$", variant: "shell" };
    case "go":
      return { label: "GO", variant: "go" };
    case "rust":
      return { label: "RS", variant: "rust" };
    case "java":
      return { label: "J", variant: "java" };
    case "cpp":
    case "csharp":
      return { label: "C", variant: "compiled" };
    case "sql":
    case "mysql":
    case "pgsql":
      return { label: "DB", variant: "database" };
    case "dockerfile":
      return { label: "D", variant: "docker" };
    default:
      return { label: "–", variant: "file" };
  }
}
