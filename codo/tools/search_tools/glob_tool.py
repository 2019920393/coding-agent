"""
Glob tool - Search for files using glob patterns
"""

import glob
import os
from typing import Any, Dict

from codo.tools.base import Tool, ToolUseContext
from codo.tools.types import ToolResult

class GlobTool(Tool):
    """Search for files using glob patterns"""

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "Search for files using glob patterns (e.g., *.py, **/*.txt)"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files (e.g., '*.py', '**/*.txt', 'src/**/*.js')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current directory)",
                },
            },
            "required": ["pattern"],
        }

    def is_concurrency_safe(self, input_data: Dict[str, Any]) -> bool:
        """
        Glob 搜索是并发安全的

        [Workflow]
        Glob 搜索操作：
        1. 只读取文件系统元数据
        2. 不修改文件系统
        3. 可以安全地并发执行

        Returns:
            True - Glob 搜索总是并发安全
        """
        return True

    def is_read_only(self, input_data: Dict[str, Any]) -> bool:
        """
        Glob 搜索是只读操作

        Returns:
            True - Glob 搜索不修改任何状态
        """
        return True

    async def execute(self, input_data: Dict[str, Any], **kwargs) -> str:
        """Search for files using glob pattern"""

        # 获取输入参数
        pattern = input_data.get("pattern", "")
        search_path = input_data.get("path", "")
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

            # 切换到搜索目录
            original_cwd = os.getcwd()
            os.chdir(search_path)

            try:
                # 使用 glob 查找文件
                matches = glob.glob(pattern, recursive=True)

                # 排序结果
                matches.sort()

                # 检查是否有匹配结果
                if not matches:
                    return f"未找到匹配模式的文件: {pattern}"

                # 构建结果消息
                result = f"找到 {len(matches)} 个匹配 '{pattern}' 的文件:\n\n"

                # 显示结果（限制为 100 个）
                display_limit = 100
                for i, match in enumerate(matches[:display_limit]):
                    # 获取文件信息
                    full_path = os.path.join(search_path, match)
                    if os.path.isfile(full_path):
                        size = os.path.getsize(full_path)
                        size_str = self._format_size(size)
                        result += f"{i+1:4d}. {match} ({size_str})\n"
                    else:
                        result += f"{i+1:4d}. {match} (目录)\n"

                # 如果结果超过限制，显示省略信息
                if len(matches) > display_limit:
                    result += f"\n... 还有 {len(matches) - display_limit} 个文件"

                return result

            finally:
                # 恢复原始工作目录
                os.chdir(original_cwd)

        except Exception as e:
            return f"错误: 搜索文件失败: {str(e)}"

    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
