"""
LSP 类型定义

基于 lsprotocol 的类型定义和自定义类型
"""

from dataclasses import dataclass, field
from typing import Any

from lsprotocol.types import (
    CallHierarchyIncomingCall as LSPProtocolCallHierarchyIncomingCall,
)
from lsprotocol.types import (
    CallHierarchyItem as LSPProtocolCallHierarchyItem,
)
from lsprotocol.types import (
    CallHierarchyOutgoingCall as LSPProtocolCallHierarchyOutgoingCall,
)
from lsprotocol.types import (
    DocumentSymbol as LSPProtocolDocumentSymbol,
)
from lsprotocol.types import (
    Hover as LSPProtocolHover,
)
from lsprotocol.types import (
    Location as LSPProtocolLocation,
)
from lsprotocol.types import (
    LocationLink as LSPProtocolLocationLink,
)
from lsprotocol.types import (
    Position as LSPProtocolPosition,
)
from lsprotocol.types import (
    Range as LSPProtocolRange,
)
from lsprotocol.types import (
    SymbolInformation as LSPProtocolSymbolInformation,
)
from pydantic import BaseModel

# 重新导出 lsprotocol 类型
LSPPosition = LSPProtocolPosition
LSPLocation = LSPProtocolLocation
LSPLocationLink = LSPProtocolLocationLink
LSPSymbolInformation = LSPProtocolSymbolInformation
LSPDocumentSymbol = LSPProtocolDocumentSymbol
LSPHover = LSPProtocolHover
LSPCallHierarchyItem = LSPProtocolCallHierarchyItem
LSPCallHierarchyIncomingCall = LSPProtocolCallHierarchyIncomingCall
LSPCallHierarchyOutgoingCall = LSPProtocolCallHierarchyOutgoingCall
LSPRange = LSPProtocolRange

@dataclass
class LSPServerConfig:
    """LSP 服务器配置"""

    name: str
    """服务器名称"""

    command: str
    """启动命令"""

    args: list[str]
    """命令参数"""

    file_extensions: list[str]
    """支持的文件扩展名（如 ['.py', '.pyi']）"""

    language_ids: list[str]
    """支持的语言 ID（如 ['python']）"""

    env: dict[str, str] | None = None
    """环境变量"""

    initialization_options: dict[str, Any] | None = None
    """初始化选项"""

    workspace_folders: list[str] | None = None
    """工作区文件夹"""

    disabled: bool = False
    """是否禁用"""

@dataclass
class LSPServerInfo:
    """LSP 服务器信息"""

    config: LSPServerConfig
    """服务器配置"""

    process: Any | None = None
    """服务器进程"""

    initialized: bool = False
    """是否已初始化"""

    capabilities: dict[str, Any] | None = None
    """服务器能力"""

    opened_files: dict[str, int] = field(default_factory=dict)
    """已打开的文件（文件路径 -> 版本号）"""

class LSPOperationType(str):
    """LSP 操作类型"""

    GO_TO_DEFINITION = "goToDefinition"
    FIND_REFERENCES = "findReferences"
    HOVER = "hover"
    DOCUMENT_SYMBOL = "documentSymbol"
    WORKSPACE_SYMBOL = "workspaceSymbol"
    GO_TO_IMPLEMENTATION = "goToImplementation"
    PREPARE_CALL_HIERARCHY = "prepareCallHierarchy"
    INCOMING_CALLS = "incomingCalls"
    OUTGOING_CALLS = "outgoingCalls"

# LSP 方法名称映射
LSP_METHOD_MAP = {
    LSPOperationType.GO_TO_DEFINITION: "textDocument/definition",
    LSPOperationType.FIND_REFERENCES: "textDocument/references",
    LSPOperationType.HOVER: "textDocument/hover",
    LSPOperationType.DOCUMENT_SYMBOL: "textDocument/documentSymbol",
    LSPOperationType.WORKSPACE_SYMBOL: "workspace/symbol",
    LSPOperationType.GO_TO_IMPLEMENTATION: "textDocument/implementation",
    LSPOperationType.PREPARE_CALL_HIERARCHY: "textDocument/prepareCallHierarchy",
    LSPOperationType.INCOMING_CALLS: "callHierarchy/incomingCalls",
    LSPOperationType.OUTGOING_CALLS: "callHierarchy/outgoingCalls",
}

class LSPRequest(BaseModel):
    """LSP 请求"""

    method: str
    """LSP 方法名"""

    params: dict[str, Any]
    """请求参数"""

class LSPResponse(BaseModel):
    """LSP 响应"""

    result: Any | None = None
    """响应结果"""

    error: dict[str, Any] | None = None
    """错误信息"""

# LSP 结果类型
LSPDefinitionResult = LSPLocation | list[LSPLocation] | list[LSPLocationLink] | None

LSPReferencesResult = list[LSPLocation] | None

LSPHoverResult = LSPHover | None

LSPDocumentSymbolResult = list[LSPDocumentSymbol] | list[LSPSymbolInformation] | None

LSPWorkspaceSymbolResult = list[LSPSymbolInformation] | None

LSPImplementationResult = LSPLocation | list[LSPLocation] | list[LSPLocationLink] | None

LSPCallHierarchyPrepareResult = list[LSPCallHierarchyItem] | None

LSPCallHierarchyIncomingCallsResult = list[LSPCallHierarchyIncomingCall] | None

LSPCallHierarchyOutgoingCallsResult = list[LSPCallHierarchyOutgoingCall] | None

@dataclass
class FormattedLocation:
    """格式化的位置信息"""

    file_path: str
    """文件路径（相对路径）"""

    line: int
    """行号（1-based）"""

    character: int
    """字符位置（1-based）"""

    end_line: int | None = None
    """结束行号（1-based）"""

    end_character: int | None = None
    """结束字符位置（1-based）"""

    context: str | None = None
    """上下文信息"""

@dataclass
class FormattedSymbol:
    """格式化的符号信息"""

    name: str
    """符号名称"""

    kind: str
    """符号类型"""

    location: FormattedLocation
    """位置信息"""

    container_name: str | None = None
    """容器名称"""

    detail: str | None = None
    """详细信息"""

@dataclass
class FormattedHover:
    """格式化的悬停信息"""

    contents: str
    """内容"""

    range: FormattedLocation | None = None
    """范围"""

# 默认 LSP 服务器配置
DEFAULT_LSP_SERVERS: list[LSPServerConfig] = [
    LSPServerConfig(
        name="python",
        command="pylsp",
        args=[],
        file_extensions=[".py", ".pyi"],
        language_ids=["python"],
    ),
    LSPServerConfig(
        name="typescript",
        command="typescript-language-server",
        args=["--stdio"],
        file_extensions=[".ts", ".tsx", ".js", ".jsx"],
        language_ids=["typescript", "typescriptreact", "javascript", "javascriptreact"],
    ),
    LSPServerConfig(
        name="rust",
        command="rust-analyzer",
        args=[],
        file_extensions=[".rs"],
        language_ids=["rust"],
    ),
]
