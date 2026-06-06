import { useState } from "react";

interface DiffViewerProps {
  diffText: string;
  filePath: string;
  changeId?: string | null;
}

interface DiffLine {
  type: "add" | "remove" | "context" | "header";
  oldLineNumber: number | null;
  newLineNumber: number | null;
  content: string;
}

/**
 * Diff 展示组件，支持统一视图和并排对比视图。
 *
 * 功能：
 * - 解析 unified diff 格式
 * - 统一视图：传统的 +/- 标记展示
 * - 并排视图：左右两栏对比展示
 * - 语法高亮差异部分
 */
export function DiffViewer({ diffText, filePath, changeId }: DiffViewerProps) {
  const [viewMode, setViewMode] = useState<"unified" | "split">("unified");
  const lines = parseDiff(diffText);

  return (
    <div className="codo-diff-viewer">
      <div className="codo-diff-viewer__header">
        <div className="codo-diff-viewer__info">
          <span className="codo-diff-viewer__label">文件:</span>
          <code className="codo-diff-viewer__path">{filePath}</code>
          {changeId && (
            <>
              <span className="codo-diff-viewer__label">变更:</span>
              <code className="codo-diff-viewer__change-id">{changeId}</code>
            </>
          )}
        </div>
        <div className="codo-diff-viewer__controls">
          <button
            type="button"
            className={viewMode === "unified" ? "codo-diff-viewer__btn codo-diff-viewer__btn--active" : "codo-diff-viewer__btn"}
            onClick={() => setViewMode("unified")}
            title="统一视图"
          >
            统一
          </button>
          <button
            type="button"
            className={viewMode === "split" ? "codo-diff-viewer__btn codo-diff-viewer__btn--active" : "codo-diff-viewer__btn"}
            onClick={() => setViewMode("split")}
            title="并排对比"
          >
            并排
          </button>
        </div>
      </div>
      <div className="codo-diff-viewer__content">
        {viewMode === "unified" ? (
          <UnifiedDiffView lines={lines} />
        ) : (
          <SplitDiffView lines={lines} />
        )}
      </div>
    </div>
  );
}

interface DiffViewProps {
  lines: DiffLine[];
}

/**
 * 统一视图：传统的 +/- 单栏展示
 */
function UnifiedDiffView({ lines }: DiffViewProps) {
  return (
    <div className="codo-diff-unified">
      {lines.map((line, index) => (
        <div
          key={index}
          className={`codo-diff-line codo-diff-line--${line.type}`}
        >
          <span className="codo-diff-line__number codo-diff-line__number--old">
            {line.oldLineNumber ?? ""}
          </span>
          <span className="codo-diff-line__number codo-diff-line__number--new">
            {line.newLineNumber ?? ""}
          </span>
          <span className="codo-diff-line__marker">
            {line.type === "add" ? "+" : line.type === "remove" ? "-" : " "}
          </span>
          <code className="codo-diff-line__content">{line.content}</code>
        </div>
      ))}
    </div>
  );
}

/**
 * 并排视图：左右两栏对比展示
 */
function SplitDiffView({ lines }: DiffViewProps) {
  const chunks = buildSplitChunks(lines);

  return (
    <div className="codo-diff-split">
      <div className="codo-diff-split__pane codo-diff-split__pane--old">
        <div className="codo-diff-split__header">原始内容</div>
        {chunks.map((chunk, index) => (
          <div
            key={`old-${index}`}
            className={`codo-diff-line codo-diff-line--${chunk.old?.type || "empty"}`}
          >
            <span className="codo-diff-line__number">
              {chunk.old?.oldLineNumber ?? ""}
            </span>
            <code className="codo-diff-line__content">
              {chunk.old?.content ?? ""}
            </code>
          </div>
        ))}
      </div>
      <div className="codo-diff-split__pane codo-diff-split__pane--new">
        <div className="codo-diff-split__header">修改后内容</div>
        {chunks.map((chunk, index) => (
          <div
            key={`new-${index}`}
            className={`codo-diff-line codo-diff-line--${chunk.new?.type || "empty"}`}
          >
            <span className="codo-diff-line__number">
              {chunk.new?.newLineNumber ?? ""}
            </span>
            <code className="codo-diff-line__content">
              {chunk.new?.content ?? ""}
            </code>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 解析 unified diff 格式
 */
function parseDiff(diffText: string): DiffLine[] {
  const lines: DiffLine[] = [];
  const rawLines = diffText.split("\n");
  let oldLineNum = 0;
  let newLineNum = 0;

  for (const rawLine of rawLines) {
    if (rawLine.startsWith("@@")) {
      // 解析 hunk header: @@ -oldStart,oldCount +newStart,newCount @@
      const match = rawLine.match(/@@ -(\d+),?\d* \+(\d+),?\d* @@/);
      if (match) {
        oldLineNum = parseInt(match[1], 10);
        newLineNum = parseInt(match[2], 10);
      }
      lines.push({
        type: "header",
        oldLineNumber: null,
        newLineNumber: null,
        content: rawLine
      });
    } else if (rawLine.startsWith("+")) {
      lines.push({
        type: "add",
        oldLineNumber: null,
        newLineNumber: newLineNum++,
        content: rawLine.slice(1)
      });
    } else if (rawLine.startsWith("-")) {
      lines.push({
        type: "remove",
        oldLineNumber: oldLineNum++,
        newLineNumber: null,
        content: rawLine.slice(1)
      });
    } else if (rawLine.startsWith(" ") || rawLine === "") {
      lines.push({
        type: "context",
        oldLineNumber: oldLineNum++,
        newLineNumber: newLineNum++,
        content: rawLine.slice(1)
      });
    } else {
      // 处理 diff 头部信息
      lines.push({
        type: "header",
        oldLineNumber: null,
        newLineNumber: null,
        content: rawLine
      });
    }
  }

  return lines;
}

interface SplitChunk {
  old: DiffLine | null;
  new: DiffLine | null;
}

/**
 * 将 diff 行转换为并排视图的块
 */
function buildSplitChunks(lines: DiffLine[]): SplitChunk[] {
  const chunks: SplitChunk[] = [];

  for (const line of lines) {
    if (line.type === "header") {
      // 头部信息在两边都显示
      chunks.push({
        old: line,
        new: line
      });
    } else if (line.type === "context") {
      // 上下文行在两边都显示
      chunks.push({
        old: line,
        new: line
      });
    } else if (line.type === "remove") {
      // 删除行只在左边显示
      chunks.push({
        old: line,
        new: null
      });
    } else if (line.type === "add") {
      // 添加行只在右边显示
      chunks.push({
        old: null,
        new: line
      });
    }
  }

  return chunks;
}
