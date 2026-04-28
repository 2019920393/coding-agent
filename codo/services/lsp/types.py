"""
LSP 类型定义

基于 lsprotocol 的类型定义和自定义类型
"""

from typing import Optional, List, Union, Dict, Any, Literal
from dataclasses import dataclass
from pydantic import BaseModel, Field
from lsprotocol.types import (
    Position as LSPProtocolPosition,
    Location as LSPProtocolLocation,
    LocationLink as LSPProtocolLocationLink,
    SymbolInformation as LSPProtocolSymbolInformation,
    DocumentSymbol as LSPProtocolDocumentSymbol,
    Hover as LSPProtocolHover,
    CallHierarchyItem as LSPProtocolCallHierarchyItem,
    CallHierarchyIncomingCall as LSPProtocolCallHierarchyIncomingCall,
    CallHierarchyOutgoingCall as LSPProtocolCallHierarchyOutgoingCall,
    Range as LSPProtocolRange,
    TextDocumentIdentifier,
    TextDocumentItem,
    VersionedTextDocumentIdentifier,
)

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

    args: List[str]
    """命令参数"""

    file_extensions: List[str]
    """支持的文件扩展名（如 ['.py', '.pyi']）"""

    language_ids: List[str]
    """支持的语言 ID（如 ['python']）"""

    env: Optional[Dict[str, str]] = None
    """环境变量"""

    initialization_options: Optional[Dict[str, Any]] = None
    """初始化选项"""

    workspace_folders: Optional[List[str]] = None
    """工作区文件夹"""

    disabled: bool = False
    """是否禁用"""

@dataclass
class LSPServerInfo:
    """LSP 服务器信息"""

    config: LSPServerConfig
    """服务器配置"""

    process: Optional[Any] = None
    """服务器进程"""

    initialized: bool = False
    """是否已初始化"""

    capabilities: Optional[Dict[str, Any]] = None
    """服务器能力"""

    opened_files: Dict[str, int] = None
    """已打开的文件（文件路径 -> 版本号）"""

    def __post_init__(self):
        if self.opened_files is None:
            self.opened_files = {}

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

    params: Dict[str, Any]
    """请求参数"""

class LSPResponse(BaseModel):
    """LSP 响应"""

    result: Optional[Any] = None
    """响应结果"""

    error: Optional[Dict[str, Any]] = None
    """错误信息"""

# LSP 结果类型
LSPDefinitionResult = Union[
    LSPLocation,
    List[LSPLocation],
    List[LSPLocationLink],
    None
]

LSPReferencesResult = Optional[List[LSPLocation]]

LSPHoverResult = Optional[LSPHover]

LSPDocumentSymbolResult = Union[
    List[LSPDocumentSymbol],
    List[LSPSymbolInformation],
    None
]

LSPWorkspaceSymbolResult = Optional[List[LSPSymbolInformation]]

LSPImplementationResult = Union[
    LSPLocation,
    List[LSPLocation],
    List[LSPLocationLink],
    None
]

LSPCallHierarchyPrepareResult = Optional[List[LSPCallHierarchyItem]]

LSPCallHierarchyIncomingCallsResult = Optional[List[LSPCallHierarchyIncomingCall]]

LSPCallHierarchyOutgoingCallsResult = Optional[List[LSPCallHierarchyOutgoingCall]]

@dataclass
class FormattedLocation:
    """格式化的位置信息"""

    file_path: str
    """文件路径（相对路径）"""

    line: int
    """行号（1-based）"""

    character: int
    """字符位置（1-based）"""

    end_line: Optional[int] = None
    """结束行号（1-based）"""

    end_character: Optional[int] = None
    """结束字符位置（1-based）"""

    context: Optional[str] = None
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

    container_name: Optional[str] = None
    """容器名称"""

    detail: Optional[str] = None
    """详细信息"""

@dataclass
class FormattedHover:
    """格式化的悬停信息"""

    contents: str
    """内容"""

    range: Optional[FormattedLocation] = None
    """范围"""

# 默认 LSP 服务器配置
DEFAULT_LSP_SERVERS: List[LSPServerConfig] = [
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
