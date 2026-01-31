import asyncio
import datetime
import json
import os
from functools import partial

from dotenv import load_dotenv

from cache.redis_cache import RedisStateManager
from execute_tool import execute_tool_call
from models.schema import function_to_schema
from models.anthropic import model_call
from skills.skill_loader import get_skill_summary
from tools.bash_tool import bash_
from tools.file_tools_ import read_, write_, edit_, glob_, grep_
from tools.search_tool_ import search_tools
from tools.plan_tools_ import make_plan, update_step, add_step, show_full_plan, get_contextual_plan_reminder
from tools.skill_tools_ import read_skill, list_skills
from tools.search_tool_ import (
    get_tool_schema,
    get_tool_func,
    get_mcp_server,
    get_mcp_toolset,
)
from utils.msg_store_ import store_msgs, load_msgs
from utils.tokenization import token_cutter
from utils.helpers import tokenizer
from utils.claude_md_loader import load_claude_md_file


load_dotenv()

DANGEROUS_TOOLS = {"bash_", "edit_", "write_"}


async def claude_loop(
    query,
    *,
    project_dir,
    watcher=None
):
    """micro cc"""

    max_tokens = 90000
    redis_state = RedisStateManager()

    msgs = load_msgs(project_dir)

    skills_summary = get_skill_summary()
    claude_md_content = load_claude_md_file(project_dir)

    # Core tools: bash, file ops, search, planning, skills
    default_tools = [
        bash_,
        read_, write_, edit_, glob_, grep_,
        search_tools,
        make_plan, update_step, show_full_plan, add_step,
        read_skill, list_skills,
    ]
    tool_schemas = []
    for tool in default_tools:
        tool_schemas.append(function_to_schema(tool))

    ######construct all for execute_tool func!
    tools = {}
    for tool in default_tools:
        if isinstance(tool, partial):
            tools[tool.func.__name__] = tool
        else:
            tools[tool.__name__] = tool

    #########discovered tools
    discovered_tools = redis_state.get_discovered_tools(project_dir)
    for tool_name in discovered_tools:
        if tool_name not in default_tools:
            tool_schemas.append(get_tool_schema(tool_name))
            tools[tool_name] = get_tool_func(tool_name)

    ###############discovered mcps - add toolsets to tools array!
    discovered_mcp = redis_state.get_discovered_mcps(project_dir)
    mcp_servers = [get_mcp_server(name) for name in discovered_mcp]
    mcp_toolsets = [get_mcp_toolset(name) for name in discovered_mcp]
    tool_schemas.extend(mcp_toolsets)  # MCP toolsets go in tools array!

    yield {"type": "status", "message": "starting..."}

    # If this is the first message, initialize with system prompt
    if not msgs:
        claude_md_section = f"\n\n<project-instructions>\n{claude_md_content}\n</project-instructions>" if claude_md_content else ""

        msgs = [
            {
                "role": "system",
                "content": f"""You are micro claude code - a CLI assistant for software engineering.

## Environment
- Project directory: {project_dir}
- Date: {datetime.datetime.now().strftime("%B %d, %Y")}

## Core Tools (always available)
- bash_: Execute shell commands (runs in project_dir, use path param or absolute paths for elsewhere)
- read_: Read file contents with line numbers
- write_: Create/overwrite files
- edit_: Surgical string replacement in files
- glob_: Find files by pattern
- grep_: Search file contents with regex

## Discoverable Tools
Use search_tools to discover: web tools, vision, etc.

## Planning Tools
make_plan, update_step, show_full_plan, add_step

{skills_summary}
{claude_md_section}

## Guidelines
- Read files before editing them
- Use absolute paths when working outside project_dir
- Prefer edit_ over write_ for existing files
- For multi-step complex work, write a plan and update it.
- Plans are private to you - Always explain findings to user, as well as what you are doing
- Use <show_full_plan> before final steps to review all accumulated findings
""",
            }
        ]

    # Add user query
    msgs.append(
        {
            "role": "user",
            "content": f"""
{query}\n
            """,
        }
    )

    while True:
        # Build system reminders
        reminders = []

        # Inject file changes if watcher detected any
        if watcher:
            file_changes = watcher.format_changes()
            if file_changes:
                reminders.append(f"Files changed since last turn:\n{file_changes}")

        plan_data = redis_state.get_plan(project_dir)
        if plan_data:
            plan = json.loads(plan_data)
            contextual_reminder = get_contextual_plan_reminder(plan)
            reminders.append(f"{contextual_reminder}\n\nUse 'update_step' after each research/work session.")

        # Inject reminder into messages for this call (only if we have any)
        if reminders:
            reminder_msg = {
                "role": "system",
                "content": f"<system-reminder>\n" + "\n\n".join(reminders) + "\n</system-reminder>"
            }
            trimmed_loop_msgs = token_cutter(msgs + [reminder_msg], tokenizer, max_tokens)
        else:
            trimmed_loop_msgs = token_cutter(msgs, tokenizer, max_tokens)

        try:
            response = await model_call(
                input=trimmed_loop_msgs,
                model="claude-4.5",
                tools=tool_schemas,
                thinking=True,
                stream=False,
            )

            thinking_block = next(
                (block for block in response.content if block.type == "thinking"), None
            )
            text_block = next(
                (block for block in response.content if block.type == "text"), None
            )
            tool_use_blocks = [
                block for block in response.content if block.type == "tool_use"
            ]

            if thinking_block:
                yield {
                    "type": "thinking",
                    "content": thinking_block.thinking,
                }

            if text_block and tool_use_blocks:
                # Intermediate text (more tool calls coming)
                yield {
                    "type": "text",
                    "content": text_block.text,
                }

            if tool_use_blocks:
                # Filter out MCP tool calls - they execute internally in API
                local_tool_blocks = [tb for tb in tool_use_blocks if tb.name in tools]

                for tool_use_block in local_tool_blocks:
                    yield {
                        "type": "tool_call",
                        "name": tool_use_block.name,
                        "input": tool_use_block.input,
                    }

                    if tool_use_block.name in DANGEROUS_TOOLS:
                        approval = {"approved": None}
                        yield {
                            "type": "approval_request",
                            "name": tool_use_block.name,
                            "input": tool_use_block.input,
                            "approval": approval
                        }
                        # Generator resumes here after consumeloop sets approval
                        if not approval["approved"]:
                            store_msgs(project_dir,msgs)
                            yield {
                                "type": "cancelled"
                            }
                            return

                tool_tasks = [
                    execute_tool_call(
                        tool_block,
                        tools,
                        project_dir
                    )
                    for tool_block in local_tool_blocks
                ]
                tool_results = await asyncio.gather(*tool_tasks)

                # Only append local tool calls to messages (not MCP)
                if local_tool_blocks:
                    content_blocks = []
                    if thinking_block:
                        content_blocks.append(
                            {
                                "type": "thinking",
                                "thinking": thinking_block.thinking,
                                "signature": thinking_block.signature,
                            }
                        )
                    for tb in local_tool_blocks:
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tb.id,
                                "name": tb.name,
                                "input": tb.input,
                            }
                        )
                    msgs.append({"role": "assistant", "content": content_blocks})

                    tool_result_blocks = []
                    for tool_block, result in zip(local_tool_blocks, tool_results):
                        # Handle search_tools discovery
                        if tool_block.name == "search_tools" and isinstance(result, dict):
                            redis_state.add_discovered_tools(
                                project_dir,
                                result.get("discovered_tools", []),
                            )
                            redis_state.add_discovered_mcps(
                                project_dir,
                                result.get("discovered_mcps", []),
                            )

                        yield {
                            "type": "tool_result",
                            "name": tool_block.name,
                            "output": str(result)[:500],  # Truncated for display
                        }

                        # API requires string content - serialize dicts
                        content = str(result) if isinstance(result, dict) else (result or "")
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_block.id,
                                "content": content,
                            }
                        )

                    msgs.append({"role": "user", "content": tool_result_blocks})

            # No tool use = final response
            if not tool_use_blocks and text_block:
                msgs.append({"role": "assistant", "content": text_block.text})
                store_msgs(project_dir, msgs)

                yield {
                    "type": "final_text",
                    "content": text_block.text,
                }
                break

        except Exception as e:
            yield {
                "type": "error",
                "message": f"Error: {type(e).__name__}: {e}",
            }
            break  # Exit on error

    # Always save messages at end
    store_msgs(project_dir, msgs)

    yield {"type": "done"}
