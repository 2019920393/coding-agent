/**
 * 中间编辑区当前显示的视图。
 *
 * 工作流：
 * 1. 初始进入 `welcome` 空态。
 * 2. 点击 Explorer 文件后进入 `file`。
 * 3. 点击右侧 Agent Team 入口后进入 `agents`。
 */
//限定只能取这两个值

export type EditorView = "welcome" | "file" | "agents";

/**
 * 相对当前 workspace 的路径。
 *
 * 工作流：
 * 1. Electron main process 返回相对路径，而不是绝对路径。
 * 2. Renderer 用这个路径定位 Explorer 节点、打开文件、保存文件。
 * 3. 真正的越界校验仍然由 main process 做，这里只让类型语义更清楚。
 */
export type WorkspaceRelativePath = string;

/**
 * Monaco 编辑器使用的语言标识。
 *
 * 这不是“只能打开这些文件”的白名单。
 * 工作流：
 * 1. Explorer 拿到文件名。
 * 2. 后续用扩展名映射到这里的 language id。
 * 3. 未识别的文件统一用 `plaintext`，仍然可以打开，只是没有专门语法高亮。
 *
 * 注意：
 * - `.tsx` 后续映射到 `typescript`，再给 Monaco 配 JSX 选项。
 * - `.jsx` 后续映射到 `javascript`，再给 Monaco 配 JSX 选项。
 */
export type EditorLanguageId =
  | "abap"
  | "apex"
  | "azcli"
  | "bat"
  | "bicep"
  | "cameligo"
  | "clojure"
  | "coffee"
  | "cpp"
  | "csharp"
  | "csp"
  | "css"
  | "cypher"
  | "dart"
  | "dockerfile"
  | "ecl"
  | "elixir"
  | "flow9"
  | "freemarker2"
  | "fsharp"
  | "go"
  | "graphql"
  | "handlebars"
  | "hcl"
  | "html"
  | "ini"
  | "java"
  | "javascript"
  | "json"
  | "julia"
  | "kotlin"
  | "less"
  | "lexon"
  | "liquid"
  | "lua"
  | "m3"
  | "markdown"
  | "mips"
  | "msdax"
  | "mysql"
  | "objective-c"
  | "pascal"
  | "pascaligo"
  | "perl"
  | "pgsql"
  | "php"
  | "pla"
  | "postiats"
  | "powerquery"
  | "powershell"
  | "pug"
  | "python"
  | "qsharp"
  | "r"
  | "razor"
  | "redis"
  | "redshift"
  | "restructuredtext"
  | "ruby"
  | "rust"
  | "sb"
  | "scala"
  | "scheme"
  | "scss"
  | "shell"
  | "solidity"
  | "sophia"
  | "sparql"
  | "sql"
  | "st"
  | "swift"
  | "systemverilog"
  | "tcl"
  | "twig"
  | "typescript"
  | "vb"
  | "xml"
  | "yaml"
  | "plaintext";

/**
 * Explorer 中的可点击节点类型。
 * 当前最小 UI 只包含文件夹和文件，不提前加入其他工作台入口。
 */
export type ExplorerNodeKind = "folder" | "file";

/**
 * Explorer 节点的公共字段。
 * `depth` 由静态数据确定，用于渲染缩进，不在组件里临时猜。
 * `path` 是相对 workspace 的路径，文件夹和文件都需要用它来定位点击目标。
 */
export interface ExplorerNodeBase {
  id: string;
  name: string;
  path: WorkspaceRelativePath;
  depth: number;
  kind: ExplorerNodeKind;
}

/**
 * Explorer 文件夹节点。
 *
 * 工作流：
 * 1. 未展开时只表示这个目录本身。
 * 2. 展开后，目录第一层子节点会被插入到扁平 Explorer 列表里。
 * 3. 收起后，子孙节点会从 Explorer 列表里移除。
 */
export interface ExplorerFolderNode extends ExplorerNodeBase {
  kind: "folder";
  expanded: boolean;
}

/**
 * Explorer 文件节点。
 *
 * 工作流：
 * 1. Explorer 只展示文件元信息，不携带文件内容。
 * 2. 用户点击文件后，再由文件读取流程生成 `OpenFile`。
 * 3. 这样后续接真实项目时，不会因为加载文件树而读取全部文件内容。
 */
export interface ExplorerFileNode extends ExplorerNodeBase {
  kind: "file";
  language: EditorLanguageId;
}

export type ExplorerNode = ExplorerFolderNode | ExplorerFileNode;

/**
 * 单个已打开编辑器 tab 的文件状态。
 *
 * 工作流：
 * 1. 用户从 Explorer 点击一个文件节点。
 * 2. 读取该文件内容。
 * 3. 生成 `OpenFile` 并放入打开文件列表。
 * 4. 多 tab 模式下，当前显示哪个文件由 `activeFilePath` 这类状态字段决定。
 *
 * 这里只保留单个 tab 需要的数据，不提前加入光标位置、滚动位置、diff 等状态。
 */
export interface OpenFile {
  /** tab 唯一标识，当前直接使用 workspace 相对路径。 */
  id: WorkspaceRelativePath;

  /** tab 上展示的文件名。 */
  name: string;

  /** 文件相对 workspace 的路径，保存和切换 tab 都依赖它。 */
  path: WorkspaceRelativePath;

  /** Monaco 使用的语言标识。 */
  language: EditorLanguageId;

  /**
   * 最近一次从磁盘读取或成功保存后的内容。
   * UI 用它和 content 对比，判断当前文件是否有未保存改动。
   */
  savedContent: string;

  /** 编辑器当前内容，用户输入会实时更新这个字段。 */
  content: string;
}

/**
 * Monaco 编辑器当前光标位置。
 *
 * 工作流：
 * 1. EditorPane 监听 Monaco 的光标变化事件。
 * 2. 把 lineNumber/column 上报给 App。
 * 3. App 再把它组装进状态栏展示信息。
 */
export interface EditorCursorPosition {
  lineNumber: number;
  column: number;
}

export type EditorTextEncoding = "UTF-8";

export type EditorIndentation = "Spaces: 2";

/**
 * 底部状态栏右侧展示的编辑器信息。
 *
 * 工作流：
 * 1. App 从当前 active tab 读取文件名、路径和语言。
 * 2. App 从 EditorPane 上报的光标事件读取行列号。
 * 3. StatusBar 只负责展示这个对象，不自己推导业务状态。
 */
export interface EditorStatusInfo {
  fileName: string | null;
  filePath: WorkspaceRelativePath | null;
  language: EditorLanguageId | null;
  cursorPosition: EditorCursorPosition | null;
  encoding: EditorTextEncoding;
  indentation: EditorIndentation;
  dirty: boolean;
}

export type ChatRole = "assistant" | "user";

/**
 * 右侧 AI 助手消息。
 * `createdAt` 用显示文本，避免原型阶段引入日期格式化工具。
 */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: string;
}
