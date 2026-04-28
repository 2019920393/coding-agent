# Codo

基于大模型 API 的 AI 编程助手。终端对话式智能代理，能读写文件、执行 Shell 命令、编排复杂多步编程任务。

## 功能特性

- **对话式 REPL** — 终端交互式聊天界面
- **Textual TUI** — 富终端 UI，实时反馈工具执行状态
- **工具系统** — Bash、Read、Write、Edit、Glob、Grep、WebFetch、NotebookEdit 等
- **子代理** — 将任务委派给专用子代理并行执行
- **流式执行** — 并发工具执行，进度实时追踪
- **上下文管理** — 长对话自动压缩，防止窗口溢出
- **记忆系统** — 跨会话持久化记忆（MEMORY.md / CODO.md）
- **MCP 支持** — Model Context Protocol，可扩展工具集成
- **会话持久化** — JSONL 格式存储，支持恢复续接
- **Token 估算** — 预算追踪，自动触发压缩阈值

## 安装

### 环境要求

- Python 3.10+
- pip 或 conda

### conda 安装

```bash
conda env create -f environment.yml
conda activate Codo
```

### pip 安装

```bash
pip install anthropic>=0.40.0 click>=8.1.0 rich>=13.0.0 textual>=0.50.0 pydantic>=2.0.0 aiofiles>=23.0.0 python-dotenv>=1.0.0
```

## 快速开始

1. 在项目根目录创建 `.env` 文件：

```env
ANTHROPIC_API_KEY=你的API密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro
```

2. 启动 Codo：

```bash
python -m codo
```

单次提问模式：

```bash
python -m codo -p "解释 query.py 里的代码逻辑"
```

## 配置说明

### 环境变量

| 变量 | 说明 |
|----------|-------------|
| `ANTHROPIC_API_KEY` | 大模型 API 密钥 |
| `ANTHROPIC_BASE_URL` | API 服务地址 |
| `ANTHROPIC_MODEL` | 默认模型名称 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Opus 级别模型 |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Sonnet 级别模型 |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Haiku 级别模型 |
| `CODO_DIAGNOSTICS_FILE` | 诊断日志 JSONL 文件路径 |
| `CODO_DISABLE_CODO_MDS` | 设为 `true` 跳过 CODO.md 加载 |
| `CODO_OVERRIDE_DATE` | 覆盖当前日期字符串 |
| `CODO_REMOTE` | 设为 `true` 跳过 git 状态检查（远程模式） |
| `CODO_REMOTE_MEMORY_DIR` | 覆盖记忆存储目录 |

### CODO.md 配置文件

Codo 从多个位置读取 `CODO.md` 文件，注入项目专属指令：

- `~/.codo/CODO.md` — 用户级指令
- `~/.codo/rules/*.md` — 用户级规则文件
- `CODO.md` — 项目根目录（及所有祖先目录）
- `.codo/CODO.md` — 隐藏项目配置
- `.codo/rules/*.md` — 项目规则文件

支持 `@include` 指令组合多个文件。

### 记忆系统

记忆存储在 `~/.codo/projects/<项目>/memory/` 目录，以 `MEMORY.md` 为索引文件。系统会在每轮对话后自动提取并持久化相关信息。

## 架构概览

```
codo/
├── main.py              # CLI 入口（click 框架）
├── query.py             # 核心查询循环（状态机）
├── query_engine.py      # 高层引擎封装
├── runtime_protocol.py  # UI 与引擎双向桥接
├── cli/tui/             # Textual 终端 UI
├── services/
│   ├── compact/         # 上下文压缩
│   ├── memory/          # 记忆持久化与提取
│   ├── prompt/          # 系统提示词组装
│   ├── api/             # API 错误处理与重试
│   ├── mcp/             # Model Context Protocol
│   ├── lsp/             # Language Server Protocol
│   └── tools/           # 工具编排执行
├── tools/               # 各工具具体实现
│   ├── bash_tool/       # Shell 命令执行
│   ├── read_tool/       # 文件读取
│   ├── write_tool/      # 文件写入
│   ├── edit_tool/       # 文件编辑
│   ├── glob_tool/       # 文件名匹配
│   ├── grep_tool/       # 内容搜索
│   ├── web_fetch_tool/  # 网页抓取
│   ├── agent_tool/      # 子代理调度
│   ├── notebook_edit_tool/  # Jupyter Notebook 编辑
│   └── ...
├── session/             # 会话存储与导出
├── team/                # 多代理协作
├── constants/           # 提示词模板、工具限制
├── types/               # 共享类型定义
└── utils/               # 配置、诊断、辅助工具
```

### 核心执行链路

```
用户输入
  → QueryEngine.submit_message_stream()
    → query() → query_loop()  （状态机循环）
      ├── prepare_turn      查询轮次准备（token 估算、压缩检查）
      ├── stream_assistant  流式调用 API
      ├── execute_tools     并发执行工具
      ├── collect_results   收集结果追加到历史
      └── post_turn         轮后处理（记忆提取、停止钩子）
```

## CLI 用法

```bash
# 交互式 REPL
python -m codo

# 单次提问
python -m codo -p "修复 auth.py 里的 bug"

# 恢复会话
python -m codo --resume <会话ID>

# 指定工作目录
python -m codo -d /path/to/project

# 详细输出模式
python -m codo -v -p "这段代码做了什么？"
```

## 开发

### 运行测试

```bash
pytest tests/
```

测试目录与源码结构对应，使用 pytest 框架，支持异步测试（pytest-asyncio）。

## License

MIT
