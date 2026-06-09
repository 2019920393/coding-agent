import { useEffect, useMemo, useRef, useState } from "react";
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
}

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
  onOpenFile
}: ExplorerPaneProps) {
  const isBusy = status === "loading" || status === "saving";
  const workspaceIsSelected = workspaceName !== null;
  const treeRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  const visibleRange = useMemo(
    () => getVisibleNodeRange(nodes.length, scrollTop, viewportHeight),
    [nodes.length, scrollTop, viewportHeight]
  );
  const visibleNodes = nodes.slice(visibleRange.startIndex, visibleRange.endIndex);
  const topSpacerHeight = visibleRange.startIndex * EXPLORER_ROW_HEIGHT;
  const bottomSpacerHeight = (nodes.length - visibleRange.endIndex) * EXPLORER_ROW_HEIGHT;

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
      >
        {nodes.length === 0 ? (
          <p className="explorer-tree__empty">选择工作区后显示文件。</p>
        ) : (
          <>
            {topSpacerHeight > 0 ? (
              <div
                aria-hidden="true"
                className="explorer-tree__virtual-spacer"
                style={{ height: `${topSpacerHeight}px` }}
              />
            ) : null}
            {visibleNodes.map((node) => (
              <ExplorerTreeRow
                key={node.id}
                node={node}
                selected={selectedPath === node.path}
                onOpenFolder={onOpenFolder}
                onOpenFile={onOpenFile}
              />
            ))}
            {bottomSpacerHeight > 0 ? (
              <div
                aria-hidden="true"
                className="explorer-tree__virtual-spacer"
                style={{ height: `${bottomSpacerHeight}px` }}
              />
            ) : null}
          </>
        )}
      </div>
    </aside>
  );
}

interface ExplorerTreeRowProps {
  node: ExplorerNode;
  selected: boolean;
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
function ExplorerTreeRow({ node, selected, onOpenFolder, onOpenFile }: ExplorerTreeRowProps) {
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

  return (
    <button
      className={rowClassName}
      type="button"
      role="treeitem"
      aria-busy={isLoadingFolder ? true : undefined}
      aria-expanded={node.kind === "folder" ? node.expanded : undefined}
      style={rowStyle}
      onClick={handleClick}
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
