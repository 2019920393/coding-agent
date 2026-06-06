import type { EditorLanguageId } from "../types";

const LANGUAGE_BY_EXTENSION = new Map<string, EditorLanguageId>([
  [".bat", "bat"],
  [".c", "cpp"],
  [".cc", "cpp"],
  [".clj", "clojure"],
  [".coffee", "coffee"],
  [".cpp", "cpp"],
  [".cs", "csharp"],
  [".css", "css"],
  [".dart", "dart"],
  [".dockerfile", "dockerfile"],
  [".ex", "elixir"],
  [".exs", "elixir"],
  [".fs", "fsharp"],
  [".go", "go"],
  [".graphql", "graphql"],
  [".h", "cpp"],
  [".hbs", "handlebars"],
  [".hpp", "cpp"],
  [".html", "html"],
  [".ini", "ini"],
  [".java", "java"],
  [".js", "javascript"],
  [".json", "json"],
  [".jsx", "javascript"],
  [".kt", "kotlin"],
  [".less", "less"],
  [".lua", "lua"],
  [".md", "markdown"],
  [".mdx", "markdown"],
  [".mjs", "javascript"],
  [".php", "php"],
  [".ps1", "powershell"],
  [".py", "python"],
  [".r", "r"],
  [".rb", "ruby"],
  [".rs", "rust"],
  [".sass", "scss"],
  [".scala", "scala"],
  [".scss", "scss"],
  [".sh", "shell"],
  [".sql", "sql"],
  [".swift", "swift"],
  [".ts", "typescript"],
  [".tsx", "typescript"],
  [".vb", "vb"],
  [".xml", "xml"],
  [".yaml", "yaml"],
  [".yml", "yaml"]
]);

const LANGUAGE_BY_FILENAME = new Map<string, EditorLanguageId>([
  ["dockerfile", "dockerfile"],
  ["makefile", "plaintext"]
]);

/**
 * 根据文件名获取 Monaco 语言标识。
 *
 * 工作流：
 * 1. 先匹配完整文件名，例如 Dockerfile。
 * 2. 再匹配文件扩展名，例如 `.py`。
 * 3. 未识别的文件返回 `plaintext`，文件仍然可以打开，只是不启用专门语法高亮。
 */
export function getEditorLanguageId(fileName: string): EditorLanguageId {
  const normalizedFileName = fileName.trim().toLowerCase();

  if (normalizedFileName === "") {
    return "plaintext";
  }

  const languageByName = LANGUAGE_BY_FILENAME.get(normalizedFileName);

  if (languageByName !== undefined) {
    return languageByName;
  }

  const extensionStartIndex = normalizedFileName.lastIndexOf(".");

  if (extensionStartIndex <= 0) {
    return "plaintext";
  }

  const extension = normalizedFileName.slice(extensionStartIndex);
  return LANGUAGE_BY_EXTENSION.get(extension) ?? "plaintext";
}
