"""
memory 抽取提示词构造模块。

这里负责生成提示词，用于指导模型分析最近消息，
并把可长期保留的记忆写入 memory 目录。
"""

MEMORY_TYPES = """## Memory types

Save memories that match one of these categories:

1. **User preferences & style** — coding style, formatting preferences, tool preferences,
   communication style, preferred languages/frameworks. Save when the user corrects you
   or explicitly states a preference.

2. **Project facts & patterns** — architecture decisions, key file locations, naming
   conventions, build/test commands, deployment workflows. Save when you discover
   important project context the user confirmed.

3. **Feedback & corrections** — when the user tells you something you did wrong,
   a pattern to avoid, or a better approach. These are critical to remember.

4. **Task context** — ongoing multi-session tasks, important decisions made,
   requirements that span sessions. Save when the user describes long-running work.
"""

WHAT_NOT_TO_SAVE = """## What NOT to save

- Do NOT save trivial or transient information (one-off commands, temporary debug output)
- Do NOT save information that's already in the codebase (README, config files)
- Do NOT save secrets, API keys, passwords, or credentials
- Do NOT duplicate existing memories — update them instead
- Do NOT save unless there is genuinely useful information to retain
"""

FRONTMATTER_EXAMPLE = """```markdown
---
name: Short title for this memory
description: Short one-line description of this memory
type: feedback | preference | project_fact | task_context
---

The actual memory content goes here.
Markdown formatting is supported.
```"""

def build_extract_prompt(
    new_message_count: int,
    existing_memories: str,
    memory_dir: str,
) -> str:
    """
    为 memory 抽取 agent 构建提示词。

    为个人场景做过简化：
    - 不支持 team memory（单用户）
    - 不包裹 REPL 工具层
    - 仅使用直接文件操作
    """
    manifest = ""
    if existing_memories:
        manifest = f"""

## Existing memory files

{existing_memories}

Check this list before writing — update an existing file rather than creating a duplicate."""

    return f"""You are now acting as the memory extraction subagent. Analyze the most recent ~{new_message_count} messages above and use them to update your persistent memory systems.

Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail), and Edit/Write for paths inside the memory directory ({memory_dir}) only.

You have a limited turn budget. The efficient strategy is: turn 1 — Read all files you might update in parallel; turn 2 — Write/Edit all updates in parallel. Do not interleave reads and writes across multiple turns.

You MUST only use content from the last ~{new_message_count} messages to update your persistent memories. Do not investigate or verify content further.{manifest}

If the user explicitly asked to remember something, save it immediately. If they asked to forget something, find and remove it.

{MEMORY_TYPES}

{WHAT_NOT_TO_SAVE}

## How to save memories

Saving a memory is a two-step process:

**Step 1** — write the memory to its own file (e.g., `user_preferences.md`, `feedback_testing.md`) using this frontmatter format:

{FRONTMATTER_EXAMPLE}

**Step 2** — add a pointer to that file in `MEMORY.md`. `MEMORY.md` is an index, not a memory — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. It has no frontmatter. Never write memory content directly into `MEMORY.md`.

- `MEMORY.md` is always loaded into your system prompt — lines after 200 will be truncated, so keep the index concise
- Organize memory semantically by topic, not chronologically
- Update or remove memories that are wrong or outdated
- Do not write duplicate memories. Check existing memories first.
"""
