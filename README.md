# Codo

AI coding agent powered by LLM APIs. A terminal-based conversational agent that can read, write, edit files, execute shell commands, and orchestrate complex multi-step coding tasks.

## Features

- **Conversational REPL** — interactive chat interface in the terminal
- **Textual TUI** — rich terminal UI with real-time tool execution feedback
- **Tool System** — Bash, Read, Write, Edit, Glob, Grep, WebFetch, NotebookEdit, and more
- **Agent Subprocesses** — delegate tasks to specialized sub-agents
- **Streaming Execution** — concurrent tool execution with progress tracking
- **Context Management** — automatic compaction for long conversations
- **Memory System** — persistent memory across sessions (MEMORY.md / CODO.md)
- **MCP Support** — Model Context Protocol for extensible tool integrations
- **Session Persistence** — JSONL-based session storage with restore/resume
- **Token Estimation** — budget tracking with auto-compact thresholds

## Installation

### Prerequisites

- Python 3.10+
- pip or conda

### Using conda

```bash
conda env create -f environment.yml
conda activate Codo
```

### Using pip

```bash
pip install anthropic>=0.40.0 click>=8.1.0 rich>=13.0.0 textual>=0.50.0 pydantic>=2.0.0 aiofiles>=23.0.0 python-dotenv>=1.0.0
```

## Quick Start

1. Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=your-api-key
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro
```

2. Run Codo:

```bash
python -m codo
```

Or with a single prompt:

```bash
python -m codo -p "Explain the code in query.py"
```

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | API key for your LLM provider |
| `ANTHROPIC_BASE_URL` | API base URL |
| `ANTHROPIC_MODEL` | Default model name |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Model for Opus-tier tasks |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Model for Sonnet-tier tasks |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Model for Haiku-tier tasks |
| `CODO_DIAGNOSTICS_FILE` | Path for diagnostic JSONL logs |
| `CODO_DISABLE_CODO_MDS` | Set to `true` to skip CODO.md loading |
| `CODO_OVERRIDE_DATE` | Override the current date string |
| `CODO_REMOTE` | Set to `true` to skip git status (remote mode) |
| `CODO_REMOTE_MEMORY_DIR` | Override memory storage directory |

### CODO.md

Codo reads `CODO.md` files from multiple locations to inject project-specific instructions:

- `~/.codo/CODO.md` — user-level instructions
- `~/.codo/rules/*.md` — user-level rule files
- `CODO.md` — project root (and ancestor directories)
- `.codo/CODO.md` — hidden project config
- `.codo/rules/*.md` — project rule files

Supports `@include` directives for composing multiple files.

### Memory System

Memories are stored in `~/.codo/projects/<project>/memory/` with `MEMORY.md` as the index file. The system automatically extracts and persists relevant information across sessions.

## Architecture

```
codo/
├── main.py              # CLI entry point (click)
├── query.py             # Core query loop (state machine)
├── query_engine.py      # High-level engine wrapper
├── runtime_protocol.py  # Bidirectional UI-engine bridge
├── cli/tui/             # Textual terminal UI
├── services/
│   ├── compact/         # Context compaction
│   ├── memory/          # Persistent memory & extraction
│   ├── prompt/          # System prompt assembly
│   ├── api/             # API error handling & retry
│   ├── mcp/             # Model Context Protocol
│   ├── lsp/             # Language Server Protocol
│   └── tools/           # Tool execution orchestration
├── tools/               # Individual tool implementations
│   ├── bash_tool/
│   ├── read_tool/
│   ├── write_tool/
│   ├── edit_tool/
│   ├── glob_tool/
│   ├── grep_tool/
│   ├── web_fetch_tool/
│   ├── agent_tool/
│   ├── notebook_edit_tool/
│   └── ...
├── session/             # Session storage & export
├── team/                # Multi-agent team orchestration
├── constants/           # Prompts, tool limits
├── types/               # Shared type definitions
└── utils/               # Config, diagnostics, helpers
```

### Core Loop

```
User Input
  → QueryEngine.submit_message_stream()
    → query() → query_loop()  (state machine)
      ├── prepare_turn      (token estimation, compact check)
      ├── stream_assistant  (streaming API call)
      ├── execute_tools     (concurrent tool execution)
      ├── collect_results   (append results to history)
      └── post_turn         (memory extraction, stop hooks)
```

## CLI Usage

```bash
# Interactive REPL
python -m codo

# Single prompt
python -m codo -p "Fix the bug in auth.py"

# Resume a session
python -m codo --resume <session-id>

# Set working directory
python -m codo -d /path/to/project

# Verbose mode
python -m codo -v -p "What does this code do?"
```

## Development

### Running Tests

```bash
pytest tests/
```

### Project Structure

Tests mirror the source structure under `tests/`. Test files use pytest with async support (`pytest-asyncio`).

## License

MIT
