"""
Grep tool - Search file contents using patterns
"""

import os
import re
from typing import Any, Dict

from codo.tools.base import Tool, ToolUseContext
from codo.tools.types import ToolResult

class GrepTool(Tool):
    """Search file contents using regex patterns"""

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "Search for patterns in file contents (supports regex)"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Pattern to search for (regex supported)",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default: current directory)",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File pattern to filter (e.g., '*.py', '*.txt')",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case sensitive search (default: false)",
                    "default": False,
                },
            },
            "required": ["pattern"],
        }

    def is_concurrency_safe(self, input_data: Dict[str, Any]) -> bool:
        """
        Grep 搜索是并发安全的

        [Workflow]
        Grep 搜索操作：
        1. 只读取文件内容
        2. 不修改文件系统
        3. 可以安全地并发执行

        Returns:
            True - Grep 搜索总是并发安全
        """
        return True

    def is_read_only(self, input_data: Dict[str, Any]) -> bool:
        """
        Grep 搜索是只读操作

        Returns:
            True - Grep 搜索不修改任何状态
        """
        return True

    async def execute(self, input_data: Dict[str, Any], **kwargs) -> str:
        """Search for pattern in files"""

        # 获取输入参数
        pattern = input_data.get("pattern", "")
        search_path = input_data.get("path", "")
        file_pattern = input_data.get("file_pattern", "*")
        case_sensitive = input_data.get("case_sensitive", False)
        cwd = kwargs.get("cwd", os.getcwd())

        # 验证模式是否提供
        if not pattern:
            return "错误: 未提供搜索模式"

        try:
            # 解析搜索路径
            if search_path:
                if not os.path.isabs(search_path):
                    search_path = os.path.join(cwd, search_path)
            else:
                search_path = cwd

            # 编译正则表达式
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return f"错误: 无效的正则表达式: {e}"

            # 搜索文件
            matches = []
            total_files = 0

            if os.path.isfile(search_path):
                # 搜索单个文件
                total_files = 1
                file_matches = self._search_file(search_path, regex)
                if file_matches:
                    matches.append((search_path, file_matches))
            else:
                # 搜索目录
                import glob as glob_module

                # 查找匹配文件模式的文件
                pattern_path = os.path.join(search_path, "**", file_pattern)
                files = glob_module.glob(pattern_path, recursive=True)

                for file_path in files:
                    if os.path.isfile(file_path):
                        total_files += 1
                        file_matches = self._search_file(file_path, regex)
                        if file_matches:
                            matches.append((file_path, file_matches))

            # 构建结果消息
            if not matches:
                return f"在 {total_files} 个文件中未找到匹配模式 '{pattern}' 的内容"

            result = f"在 {len(matches)} 个文件中找到 {sum(len(m) for _, m in matches)} 处匹配:\n\n"

            # 显示结果（限制为 50 个文件）
            display_limit = 50
            for file_path, file_matches in matches[:display_limit]:
                # 转换为相对路径
                try:
                    rel_path = os.path.relpath(file_path, cwd)
                except:
                    rel_path = file_path

                result += f"{rel_path}:\n"

                # 显示匹配行（每个文件限制为 10 行）
                for line_num, line_content in file_matches[:10]:
                    result += f"  {line_num:4d}: {line_content}\n"

                if len(file_matches) > 10:
                    result += f"  ... 还有 {len(file_matches) - 10} 处匹配\n"

                result += "\n"

            if len(matches) > display_limit:
                result += f"... 还有 {len(matches) - display_limit} 个文件包含匹配"

            return result

        except Exception as e:
            return f"错误: 搜索文件失败: {str(e)}"

    def _search_file(self, file_path: str, regex: re.Pattern) -> list:
        """Search a single file for pattern matches"""
        matches = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    if regex.search(line):
                        # Remove trailing newline
                        line_content = line.rstrip("\n\r")
                        matches.append((line_num, line_content))

                        # Limit matches per file
                        if len(matches) >= 100:
                            break

        except Exception:
            # Skip files that can't be read
            pass

        return matches
