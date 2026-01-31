import inspect
from typing import Any, Dict


async def execute_tool_call(
    tool_call,  # Anthropic SDK ToolUseBlock object
    tools: Dict[str, callable],
    project_dir: str
):
    """Execute a tool call from Anthropic SDK response.

    Args:
        tool_call: ToolUseBlock with .name, .input, .id attributes
        tools: Dict mapping tool names to callables
        project_dir: Working directory for tools that need it

    Returns:
        Tool result (str or dict)
    """
    name = tool_call.name
    args = tool_call.input  # Already a dict from SDK

    if name not in tools:
        return f"Unknown tool: {name}"

    tool = tools[name]

    try:
        # Check if tool accepts project_dir
        sig = inspect.signature(tool)
        if "project_dir" in sig.parameters:
            args = {**args, "project_dir": project_dir}

        # Execute tool (sync or async)
        if inspect.iscoroutinefunction(tool):
            result = await tool(**args)
        else:
            result = tool(**args)

        return result

    except Exception as e:
        return f"Tool error ({name}): {type(e).__name__}: {e}"
