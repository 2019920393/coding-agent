"""
LSPTool prompt

LSPTool 提示词
"""

DESCRIPTION = """Use the language server for semantic code intelligence operations.

Supports these operations:
- goToDefinition: Jump to symbol definition
- findReferences: Find all references to a symbol
- hover: Get hover information (type, documentation)
- documentSymbol: List all symbols in a document
- workspaceSymbol: Search for symbols across workspace
- goToImplementation: Jump to symbol implementation
- prepareCallHierarchy: Prepare call hierarchy item
- incomingCalls: Find all functions that call this function
- outgoingCalls: Find all functions this function calls

This tool is read-only and concurrency-safe.
It uses the appropriate language server based on file extension.
"""

PROMPT = """Use the LSPTool when you need semantic code intelligence that goes beyond text search.

When to use LSPTool:
- Finding the true definition of a symbol (especially across files)
- Finding all semantic references to a symbol
- Getting type information or documentation via hover
- Understanding call relationships between functions
- Listing symbols in a file or workspace

When NOT to use LSPTool:
- Simple text search: use Grep instead
- File name search: use Glob instead
- Reading file contents: use Read instead
- Basic string matching: use Grep instead

Input requirements:
- file_path: absolute path to the file
- line: 1-based line number
- character: 1-based character position
- query: required only for workspaceSymbol operation

Supported file types depend on installed LSP servers:
- Python (.py, .pyi) -> pylsp
- TypeScript/JavaScript (.ts, .tsx, .js, .jsx) -> typescript-language-server
- Rust (.rs) -> rust-analyzer

Safety notes:
- This tool is read-only
- File size limit: 10MB
- UNC paths are blocked for security
"""
