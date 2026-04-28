"""Sub-agent context management for fresh and fork modes.

- agentToolUtils.ts prepareSubAgentContext() (line 100-200+)
- fork 机制：复制父 agent 上下文
- fresh 机制：创建新的专门角色上下文
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import copy

@dataclass
class SubAgentContext:
    """Sub-agent execution context.

    Attributes:
        mode: "fresh" or "fork"
        agent_type: Agent type (e.g., "Explore", "Plan")
        system_prompt: System prompt for sub-agent
        tools: Available tools for sub-agent
        model: Model to use
        parent_context: Parent agent context (for fork mode)
        is_background: Whether running in background
        agent_id: Unique agent identifier
    """
    mode: str  # "fresh" or "fork"
    agent_type: str
    system_prompt: str
    tools: List[Any]
    model: str
    parent_context: Optional[Dict[str, Any]] = None
    is_background: bool = False
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

def prepare_fresh_context(
    agent_type: str,
    system_prompt: str,
    tools: List[Any],
    model: str,
    is_background: bool = False,
) -> SubAgentContext:
    """
    Prepare a fresh sub-agent context.

    Fresh mode creates a new specialized agent with its own identity.
    It does NOT inherit parent conversation history.

    Args:
        agent_type: Type of agent (e.g., "Explore", "Plan")
        system_prompt: System prompt for the agent
        tools: Available tools
        model: Model to use
        is_background: Whether to run in background

    Returns:
        SubAgentContext configured for fresh mode
    """
    import uuid

    return SubAgentContext(
        mode="fresh",
        agent_type=agent_type,
        system_prompt=system_prompt,
        tools=tools,
        model=model,
        is_background=is_background,
        agent_id=f"agent_{uuid.uuid4().hex[:8]}",
        metadata={
            "created_from": "fresh",
            "specialization": agent_type,
        }
    )

def prepare_fork_context(
    parent_context: Dict[str, Any],
    tools: List[Any],
    is_background: bool = False,
) -> SubAgentContext:
    """
    Prepare a forked sub-agent context.

    - 继承父代理的系统提示（byte-exact，保证 prompt cache 命中）
    - 继承父代理的工具集（cache-identical tool definitions）
    - 添加 fork 限制（禁止递归 fork）

    Fork mode creates a copy of the current agent to handle a subtask.
    It inherits the parent's system prompt, tools, and context shape.

    Args:
        parent_context: Parent agent's context to fork from
        tools: Available tools (may be filtered)
        is_background: Whether to run in background

    Returns:
        SubAgentContext configured for fork mode
    """
    import uuid

    # 继承父代理的系统提示
    system_prompt = parent_context.get("system_prompt", "")
    # 继承父代理的模型
    model = parent_context.get("model", "claude-sonnet-4-20250514")

    # 添加 fork 限制
    fork_restrictions = """

=== FORK MODE RESTRICTIONS ===
You are a forked sub-agent handling a specific subtask. You must:
- Focus on completing the assigned subtask only
- Return results directly without excessive explanation
- NOT fork additional sub-agents (no recursive forking)
- NOT act as a user-facing chat interface

Your role is to handle this specific subtask and report back.
"""

    # 只在有系统提示时追加限制（避免空提示 + 限制的奇怪组合）
    enhanced_prompt = (system_prompt + fork_restrictions) if system_prompt else fork_restrictions.strip()

    return SubAgentContext(
        mode="fork",
        agent_type="forked",
        system_prompt=enhanced_prompt,
        tools=tools,
        model=model,
        parent_context=parent_context,
        is_background=is_background,
        agent_id=f"fork_{uuid.uuid4().hex[:8]}",
        metadata={
            "created_from": "fork",
            "parent_agent_id": parent_context.get("agent_id"),
            "inherits_context": True,
        }
    )

def should_use_fork_mode(
    subagent_type: Optional[str],
    parent_context: Dict[str, Any],
) -> bool:
    """
    Decide whether to use fork mode or fresh mode.

    Fork mode is used when:
    - No specific subagent_type is specified
    - Parent context is available
    - Task benefits from context reuse

    Fresh mode is used when:
    - A specific subagent_type is requested (e.g., "Explore", "Plan")
    - Need a specialized agent with different capabilities

    Args:
        subagent_type: Requested agent type (None means fork)
        parent_context: Parent agent context

    Returns:
        True if should use fork mode, False for fresh mode
    """
    # If specific agent type requested, use fresh mode
    if subagent_type:
        return False

    # If no parent context, must use fresh mode
    if not parent_context:
        return False

    # Default to fork mode for context reuse
    return True

def clone_context_for_isolation(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clone context for sub-agent isolation.

    Creates a deep copy of mutable state to prevent sub-agent
    from interfering with parent agent's state.

    Args:
        context: Context to clone

    Returns:
        Cloned context with isolated state
    """
    # Deep copy to isolate mutable state
    cloned = copy.deepcopy(context)

    # Reset certain state that should not be shared
    cloned.pop("conversation_history", None)
    cloned.pop("tool_results_cache", None)
    cloned.pop("ui_control", None)

    return cloned
