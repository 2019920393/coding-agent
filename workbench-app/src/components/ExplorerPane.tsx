import type { ExplorerNode } from "../types";
import type { WorkbenchStatus } from "../state/workbenchState";

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

      <div className="explorer-tree" role="tree" aria-label="工作区文件树">
        {nodes.length === 0 ? (
          <p className="explorer-tree__empty">选择工作区后显示文件。</p>
        ) : (
          nodes.map((node) => (
            <ExplorerTreeRow
              key={node.id}
              node={node}
              selected={selectedPath === node.path}
              onOpenFolder={onOpenFolder}
              onOpenFile={onOpenFile}
            />
          ))
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
    paddingLeft: `${12 + node.depth * 18}px`
  };

  const handleClick = () => {
    if (node.kind === "folder") {
      onOpenFolder(node.path);
      return;
    }

    onOpenFile(node.path);
  };

  return (
    <button
      className={`explorer-tree__row ${selected ? "explorer-tree__row--selected" : ""}`}
      type="button"
      role="treeitem"
      style={rowStyle}
      onClick={handleClick}
    >
      <span className="explorer-tree__icon" aria-hidden="true">
        {getExplorerNodeIcon(node)}
      </span>
      <span className={`explorer-tree__name explorer-tree__name--${node.kind}`}>{node.name}</span>
    </button>
  );
}

/**
 * 返回 Explorer 行首图标。
 *
 * 工作流：
 * 1. 文件夹用展开/收起符号表达状态。
 * 2. 文件用轻量文档符号，避免和层级缩进混在一起看不清。
 */
function getExplorerNodeIcon(node: ExplorerNode): string {
  if (node.kind === "folder") {
    return node.expanded ? "▾" : "▸";
  }

  return "□";
}
