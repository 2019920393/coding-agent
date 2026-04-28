"""
内置代理定义

- built-in/exploreAgent.ts — Explore 只读搜索代理
- built-in/planAgent.ts — Plan 只读规划代理
- loadAgentsDir.ts — AgentDefinition 类型 + parseAgentFromMarkdown() 逻辑

[Workflow]
1. 定义 AgentDefinition 数据类（对齐 BaseAgentDefinition）
2. 定义内置 Explore / Plan 代理
3. 提供从 Markdown 目录加载自定义代理的函数
4. 提供合并内置 + 用户级 + 项目级代理的函数
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 模块级日志记录器，用于记录代理加载过程中的警告
logger = logging.getLogger(__name__)

@dataclass
class AgentDefinition:
    """
    代理定义

    [Workflow]
    存储单个 agent 的所有元数据，包括类型标识、使用说明、
    系统提示词、工具限制、模型选择和来源标记

    Attributes:
        agent_type:       代理类型标识 (e.g. "Explore", "Plan")
        when_to_use:      何时使用此代理的描述（给 LLM 看）
        system_prompt:    子代理的 system prompt（正文内容）
        tools:            允许使用的工具列表（None 表示继承父代理）
        disallowed_tools: 禁止使用的工具列表
        model:            模型选择（None = 继承父代理模型）
        max_turns:        最大对话轮数限制
        is_read_only:     是否只读代理（不允许写文件）
        source:           来源标记（built-in / project / user）
    """
    agent_type: str                                    # 代理类型唯一标识
    when_to_use: str                                   # 使用场景描述（给 LLM 决策用）
    system_prompt: str                                 # 子代理系统提示词
    tools: Optional[List[str]] = None                  # 允许工具列表（None=继承）
    disallowed_tools: List[str] = field(default_factory=list)  # 禁止工具列表
    model: Optional[str] = None                        # 模型名称（None=继承）
    max_turns: int = 10                                # 最大对话轮数
    is_read_only: bool = False                         # 是否只读模式
    source: str = "built-in"                           # 来源：built-in / project / user

# ============================================================================
# Explore Agent

# ============================================================================

EXPLORE_SYSTEM_PROMPT = """You are a file search specialist. You excel at thoroughly navigating and exploring codebases.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code. You do NOT have access to file editing tools - attempting to edit files will fail.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use Glob for broad file pattern matching
- Use Grep for searching file contents with regex
- Use Read when you know the specific file path you need to read
- Use Bash ONLY for read-only operations (ls, git status, git log, git diff, find, cat, head, tail)
- NEVER use Bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, or any file creation/modification
- Adapt your search approach based on the thoroughness level specified by the caller
- Communicate your final report directly as a regular message - do NOT attempt to create files

NOTE: You are meant to be a fast agent that returns output as quickly as possible. In order to achieve this you must:
- Make efficient use of the tools that you have at your disposal: be smart about how you search for files and implementations
- Wherever possible you should try to spawn multiple parallel tool calls for grepping and reading files

Complete the user's search request efficiently and report your findings clearly."""

EXPLORE_WHEN_TO_USE = (
    'Fast agent specialized for exploring codebases. Use this when you need to '
    'quickly find files by patterns (eg. "src/components/**/*.tsx"), search code '
    'for keywords (eg. "API endpoints"), or answer questions about the codebase '
    '(eg. "how do API endpoints work?"). When calling this agent, specify the '
    'desired thoroughness level: "quick" for basic searches, "medium" for '
    'moderate exploration, or "very thorough" for comprehensive analysis across '
    'multiple locations and naming conventions.'
)

EXPLORE_AGENT = AgentDefinition(
    agent_type="Explore",
    when_to_use=EXPLORE_WHEN_TO_USE,
    system_prompt=EXPLORE_SYSTEM_PROMPT,
    disallowed_tools=["Agent", "Edit", "Write"],
    model="claude-haiku-4-5-20251001",
    is_read_only=True,
    source="built-in",  # 标记为内置代理
)

# ============================================================================
# Plan Agent

# ============================================================================

PLAN_SYSTEM_PROMPT = """You are a software architect and planning specialist. Your role is to explore the codebase and design implementation plans.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY planning task. You are STRICTLY PROHIBITED from:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to explore the codebase and design implementation plans. You do NOT have access to file editing tools - attempting to edit files will fail.

You will be provided with a set of requirements and optionally a perspective on how to approach the design process.

## Your Process

1. **Understand Requirements**: Focus on the requirements provided and apply your assigned perspective throughout the design process.

2. **Explore Thoroughly**:
   - Read any files provided to you in the initial prompt
   - Find existing patterns and conventions using Glob, Grep, and Read
   - Understand the current architecture
   - Identify similar features as reference
   - Trace through relevant code paths
   - Use Bash ONLY for read-only operations (ls, git status, git log, git diff, find, cat, head, tail)
   - NEVER use Bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, or any file creation/modification

3. **Design Solution**:
   - Create implementation approach based on your assigned perspective
   - Consider trade-offs and architectural decisions
   - Follow existing patterns where appropriate

4. **Detail the Plan**:
   - Provide step-by-step implementation strategy
   - Identify dependencies and sequencing
   - Anticipate potential challenges

## Required Output

End your response with:

### Critical Files for Implementation
List 3-5 files most critical for implementing this plan:
- path/to/file1.py
- path/to/file2.py
- path/to/file3.py

REMEMBER: You can ONLY explore and plan. You CANNOT and MUST NOT write, edit, or modify any files. You do NOT have access to file editing tools."""

PLAN_WHEN_TO_USE = (
    'Software architect agent for designing implementation plans. Use this when '
    'you need to plan the implementation strategy for a task. Returns step-by-step '
    'plans, identifies critical files, and considers architectural trade-offs.'
)

PLAN_AGENT = AgentDefinition(
    agent_type="Plan",
    when_to_use=PLAN_WHEN_TO_USE,
    system_prompt=PLAN_SYSTEM_PROMPT,
    disallowed_tools=["Agent", "Edit", "Write"],
    model=None,  # inherit from parent
    is_read_only=True,
    source="built-in",  # 标记为内置代理
)

# ============================================================================
# 内置代理注册表
# ============================================================================

BUILTIN_AGENTS: Dict[str, AgentDefinition] = {
    "Explore": EXPLORE_AGENT,
    "Plan": PLAN_AGENT,
}

def get_builtin_agents() -> Dict[str, AgentDefinition]:
    """获取所有内置代理定义"""
    return BUILTIN_AGENTS.copy()

def find_agent_by_type(agent_type: str) -> Optional[AgentDefinition]:
    """根据类型查找代理定义"""
    return BUILTIN_AGENTS.get(agent_type)

# ============================================================================
# Frontmatter 解析工具

# ============================================================================

def _parse_frontmatter(content: str) -> Tuple[dict, str]:
    """
    解析 Markdown 文件的 frontmatter

    [Workflow]
    1. 检查文件内容是否以 --- 开头（标准 YAML frontmatter 格式）
    2. 找到结束的 --- 分隔符位置
    3. 提取 frontmatter 文本和正文内容
    4. 逐行解析简单的 key: value 格式
    5. 返回 (frontmatter 字典, 正文内容) 元组

    Args:
        content: Markdown 文件的完整文本内容

    Returns:
        (frontmatter 字典, 正文内容字符串) 元组
        如果没有 frontmatter，返回 ({}, 原始内容)
    """
    # 检查是否以 --- 开头，不是则没有 frontmatter
    if not content.startswith("---"):
        return {}, content  # 无 frontmatter，直接返回空字典和原始内容

    # 从第 3 个字符开始查找结束的 ---（跳过开头的 ---）
    end_idx = content.find("---", 3)
    if end_idx == -1:
        # 没有找到结束分隔符，视为无效 frontmatter
        return {}, content

    # 提取 frontmatter 文本（去除首尾空白）
    frontmatter_text = content[3:end_idx].strip()
    # 提取正文内容（--- 之后的部分，去除首尾空白）
    body = content[end_idx + 3:].strip()

    # 解析简单的 YAML-like 格式（key: value）
    frontmatter: dict = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        # 跳过空行和注释行
        if not line or line.startswith("#"):
            continue
        # 只处理包含冒号的行（key: value 格式）
        if ":" in line:
            # 使用 partition 只分割第一个冒号，支持值中包含冒号
            key, _, value = line.partition(":")
            key = key.strip()    # 去除键名首尾空白
            value = value.strip()  # 去除值首尾空白
            # 处理逗号分隔的列表格式（如 tools: Bash, Read, Write）
            if "," in value:
                # 分割并去除每个元素的首尾空白
                frontmatter[key] = [v.strip() for v in value.split(",")]
            else:
                # 单值直接存储
                frontmatter[key] = value

    return frontmatter, body

# ============================================================================
# 从目录加载自定义 Agent 定义

# ============================================================================

def load_agents_from_dir(agents_dir: str) -> List[AgentDefinition]:
    """
    从目录加载自定义 agent 定义

    具体对齐 parseAgentFromMarkdown() 的解析逻辑

    [Workflow]
    1. 检查目录是否存在，不存在直接返回空列表
    2. 扫描目录下所有 .md 文件（按文件名排序，保证加载顺序稳定）
    3. 对每个文件：解析 frontmatter 和正文
    4. 从 frontmatter 提取 name（agent_type）、description（when_to_use）
    5. 提取可选字段：tools、model、max_turns
    6. 正文作为 system_prompt
    7. 跳过缺少必需字段的文件，记录警告
    8. 返回成功解析的 AgentDefinition 列表

    Agent Markdown 文件格式：
    ```markdown
    ---
    name: my-agent
    description: 这个 agent 用于...
    tools: Bash, Read, Write
    model: claude-sonnet-4-20250514
    max_turns: 10
    ---

    你是一个专门用于...的助手。
    ```

    Args:
        agents_dir: agents 目录路径（字符串）

    Returns:
        成功解析的 AgentDefinition 列表，目录不存在时返回空列表
    """
    # 将字符串路径转为 Path 对象，便于后续操作
    agents_path = Path(agents_dir)

    # 目录不存在或不是目录时，直接返回空列表（正常情况，用户可能未创建）
    if not agents_path.exists() or not agents_path.is_dir():
        return []

    # 存储成功解析的代理定义
    agents: List[AgentDefinition] = []

    # 扫描所有 .md 文件，sorted() 保证加载顺序稳定（按文件名字母序）
    for md_file in sorted(agents_path.glob("*.md")):
        try:
            # 读取文件内容，使用 UTF-8 编码支持中文
            content = md_file.read_text(encoding="utf-8")
            # 解析 frontmatter 和正文
            frontmatter, body = _parse_frontmatter(content)

            # 提取必需字段：name（代理类型标识）
            agent_type = frontmatter.get("name", "")
            # 提取必需字段：description（使用场景描述）
            when_to_use = frontmatter.get("description", "")

            # 跳过缺少必需字段的文件（可能是普通文档，不是代理定义）
            if not agent_type or not when_to_use:
                continue

            # ---- 提取可选字段：tools ----
            tools_raw = frontmatter.get("tools")
            if isinstance(tools_raw, list):
                # frontmatter 已解析为列表（逗号分隔）
                tools: Optional[List[str]] = tools_raw
            elif isinstance(tools_raw, str) and tools_raw:
                # 字符串格式，手动分割（兼容解析器未处理的情况）
                tools = [t.strip() for t in tools_raw.split(",")]
            else:
                # 未指定 tools，继承父代理工具集
                tools = None

            # ---- 提取可选字段：model ----
            model_raw = frontmatter.get("model")
            # 空字符串视为未指定，转为 None
            model: Optional[str] = model_raw if model_raw else None

            # ---- 提取可选字段：max_turns（支持 snake_case 和 camelCase）----
            max_turns_raw = frontmatter.get("max_turns") or frontmatter.get("maxTurns")
            max_turns: int = 10  # 默认 10 轮
            if max_turns_raw:
                try:
                    # 尝试将字符串转为整数
                    max_turns = int(max_turns_raw)
                except (ValueError, TypeError):
                    # 转换失败时使用默认值，不报错
                    pass

            # ---- 正文作为 system_prompt ----
            system_prompt = body.strip()
            # 没有正文内容的文件跳过（system_prompt 是必需的）
            if not system_prompt:
                continue

            # 创建 AgentDefinition 对象
            agent = AgentDefinition(
                agent_type=agent_type,          # 代理类型标识
                when_to_use=when_to_use,        # 使用场景描述
                system_prompt=system_prompt,    # 系统提示词（正文）
                tools=tools,                    # 允许工具列表（None=继承）
                model=model,                    # 模型名称（None=继承）
                max_turns=max_turns,            # 最大对话轮数
                source="project",               # 来源标记（从目录加载的视为 project）
            )
            agents.append(agent)  # 添加到结果列表

        except Exception as e:
            # 捕获所有异常，记录警告后继续处理下一个文件
            logger.warning(f"加载 agent 文件失败 {md_file}: {e}")
            continue  # 跳过当前文件，继续处理其他文件

    return agents

def load_all_agents(cwd: str) -> List[AgentDefinition]:
    """
    加载所有 agent 定义（内置 + 用户级 + 项目级）

    [Workflow]
    1. 加载内置 agents（get_builtin_agents()）
    2. 加载用户级自定义 agents（~/.codo/agents/），source="user"
    3. 加载项目级自定义 agents（{cwd}/.codo/agents/），source="project"
    4. 按优先级合并（后面的覆盖前面的，按 agent_type 去重）：
       内置 < 用户级 < 项目级
    5. 返回合并后的列表

    Args:
        cwd: 当前工作目录（用于查找项目级 agents）

    Returns:
        所有 AgentDefinition 的列表（已去重，高优先级覆盖低优先级）
    """
    # 步骤 1：加载内置 agents 作为基础
    builtin = list(get_builtin_agents().values())

    # 步骤 2：加载用户级自定义 agents（~/.codo/agents/）
    user_agents_dir = os.path.join(os.path.expanduser("~"), ".codo", "agents")
    user_agents = load_agents_from_dir(user_agents_dir)
    # 将用户级 agents 的 source 标记为 "user"
    for agent in user_agents:
        agent.source = "user"

    # 步骤 3：加载项目级自定义 agents（{cwd}/.codo/agents/）
    project_agents_dir = os.path.join(cwd, ".codo", "agents")
    project_agents = load_agents_from_dir(project_agents_dir)
    # project_agents 的 source 已在 load_agents_from_dir 中设为 "project"

    # 步骤 4：按优先级合并（使用字典，后面的覆盖前面的）
    agent_map: Dict[str, AgentDefinition] = {}

    # 内置 agents 优先级最低，先放入
    for agent in builtin:
        agent_map[agent.agent_type] = agent

    # 用户级 agents 覆盖内置 agents
    for agent in user_agents:
        agent_map[agent.agent_type] = agent

    # 项目级 agents 优先级最高，最后覆盖
    for agent in project_agents:
        agent_map[agent.agent_type] = agent

    # 返回去重后的代理列表（保持字典插入顺序）
    return list(agent_map.values())
