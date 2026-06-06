import type { WorkbenchStatus } from "../state/workbenchState";
import type { EditorStatusInfo } from "../types";

interface StatusBarProps {
  workspaceName: string | null;
  status: WorkbenchStatus;
  statusMessage: string;
  editorStatus: EditorStatusInfo;
}

/**
 * 底部状态栏。
 *
 * 工作流：
 * 1. 上层把 workspace、状态消息和当前文件名传入。
 * 2. 状态栏只负责展示，不推导业务状态。
 * 3. 后续要显示 Git 分支、编码、行列号时，再增加明确字段。
 */
export function StatusBar({
  workspaceName,
  status,
  statusMessage,
  editorStatus
}: StatusBarProps) {
  const hasOpenFile = editorStatus.filePath !== null;
  return (
    <footer className={`status-bar status-bar--${status}`}>
      <div className="status-bar__left">
        <span>{workspaceName ?? "No Workspace"}</span>
        <span>{statusMessage}</span>
      </div>
      <div className="status-bar__right">
        <span className="status-bar__path" title={editorStatus.filePath ?? undefined}>
          {formatFilePathText(editorStatus.filePath, editorStatus.dirty)}
        </span>
        {hasOpenFile ? (
          <>
            <span>{formatCursorPosition(editorStatus.cursorPosition)}</span>
            <span>{editorStatus.language ?? "plaintext"}</span>
            <span>{editorStatus.encoding}</span>
            <span>{editorStatus.indentation}</span>
          </>
        ) : null}
      </div>
    </footer>
  );
}

/**
 * 生成状态栏当前文件路径文本。
 *
 * 工作流：
 * 1. 没有打开文件时显示 No File。
 * 2. 文件有未保存改动时在路径前加圆点。
 * 3. 这里不推导 dirty，只展示上层传入的事实。
 */
function formatFilePathText(filePath: string | null, isDirty: boolean): string {
  if (filePath === null) {
    return "No File";
  }

  return isDirty ? `● ${filePath}` : filePath;
}

/**
 * 生成光标行列号文本。
 */
function formatCursorPosition(position: EditorStatusInfo["cursorPosition"]): string {
  if (position === null) {
    return "Ln -, Col -";
  }

  return `Ln ${position.lineNumber}, Col ${position.column}`;
}
