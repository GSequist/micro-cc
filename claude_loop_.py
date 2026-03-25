import asyncio
import datetime
import json
import os
from functools import partial

from dotenv import load_dotenv

from cache.redis_cache import RedisStateManager
from execute_tool import execute_tool_call
from models.schema import function_to_schema
from models.anthropic import a_model_call
from models.litellm import l_model_call
from skills.skill_loader import get_skill_summary
from tools.bash_tool import bash_
from tools.file_tools_ import read_, write_, edit_, glob_, grep_
from tools.search_tool_ import search_tools
from tools.plan_tools_ import make_plan, update_step, add_step, show_full_plan, get_contextual_plan_reminder, get_post_tool_plan_reminder
from tools.skill_tools_ import read_skill, list_skills
from tools.search_tool_ import (
    get_tool_schema,
    get_tool_func,
    get_mcp_server,
    get_mcp_toolset,
    MCP_CATALOG,
)
from tools.mcp_client_ import resolve_mcp_for_litellm, call_mcp_tool
from utils.msg_store_ import store_msgs, load_msgs, load_summary, summarize_and_trim
from utils.tokenization_simple import token_cutter
from utils.helpers import tokenizer
from utils.claude_md_loader import load_claude_md_file


load_dotenv()

DANGEROUS_TOOLS = {"bash_", "edit_", "write_"}


async def claude_loop(
    query,
    *,
    project_dir,
    end_resp,
    watcher=None,
    cancel_event=None
):
    """micro cc"""

    max_tokens = 80000
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
        if tool_name not in tools:
            tool_schemas.append(get_tool_schema(tool_name))
            tools[tool_name] = get_tool_func(tool_name)

    ###############discovered mcps
    discovered_mcp = redis_state.get_discovered_mcps(project_dir)
    mcp_servers = []       # Anthropic server-side: server configs for API param
    mcp_openai_tools = []  # LiteLLM client-side: pre-fetched OpenAI-format tool defs
    mcp_routing = {}       # LiteLLM client-side: {tool_name: server_url}

    if discovered_mcp:
        if end_resp == "LiteLLM":
            # Client-side: we ARE the MCP client — fetch tools, convert to OpenAI format
            mcp_entries = [MCP_CATALOG[name] for name in discovered_mcp if name in MCP_CATALOG]
            if mcp_entries:
                mcp_openai_tools, mcp_routing = await resolve_mcp_for_litellm(mcp_entries)
        else:
            # Anthropic server-side: just pass configs, API handles everything
            mcp_servers = [get_mcp_server(name) for name in discovered_mcp]
            tool_schemas.extend([get_mcp_toolset(name) for name in discovered_mcp])

    yield {"type": "status", "message": ""}

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

    # Checkpoint: persist user message immediately (survives crashes mid-loop)
    store_msgs(project_dir, msgs)

    while True:
        # Check cancellation at top of each iteration
        if cancel_event and cancel_event.is_set():
            break

        # Build system-level context (prepended as role:system, not user)
        system_context = []

        # File changes
        if watcher:
            file_changes = watcher.format_changes()
            if file_changes:
                system_context.append({"role": "system", "content": f"<file-changes>\n{file_changes}\n</file-changes>"})

        # Background process status
        from utils.process_tracker import format_status as process_status
        proc_info = process_status()
        if proc_info:
            system_context.append({"role": "system", "content": f"<process-status>\n{proc_info}\n</process-status>"})

        # Conversation summary from sliding window
        conversation_summary = load_summary(project_dir)
        if conversation_summary:
            system_context.append({"role": "system", "content": f"<conversation-summary>\n{conversation_summary}\n</conversation-summary>"})

        # Plan reminder (full contextual, as system)
        plan_msg = []
        plan_data = redis_state.get_plan(project_dir)
        if plan_data:
            plan = json.loads(plan_data)
            contextual_reminder = get_contextual_plan_reminder(plan)
            plan_msg = [{"role": "system", "content": f"<system-reminder>{contextual_reminder}\n\nUse 'update_step' after each research/work session.</system-reminder>"}]

        # Assemble: system context + plan prepended, then conversation
        trimmed_loop_msgs = token_cutter(msgs, tokenizer, max_tokens)
        trimmed_loop_msgs = system_context + plan_msg + trimmed_loop_msgs

        try:
            if end_resp == "LiteLLM":
                stream = await l_model_call(
                    input=trimmed_loop_msgs,
                    model="bedrock.anthropic.claude-opus-4-6",
                    tools=tool_schemas,
                    mcp_openai_tools=mcp_openai_tools or None,
                    thinking=True,
                    stream=True,
                )
            else:
                stream = await a_model_call(
                    input=trimmed_loop_msgs,
                    model="opus-4.6",
                    tools=tool_schemas,
                    mcp_servers=mcp_servers if mcp_servers else None,
                    thinking=True,
                    stream=True,
                )

            if stream is None:
                yield {"type": "error", "message": "model call failed after retries"}
                break

            response = None
            async for event in stream:
                if cancel_event and cancel_event.is_set():
                    break
                if event["type"] == "text_delta":
                    yield {"type": "text_delta", "content": event["text"]}
                elif event["type"] == "thinking_delta":
                    yield {"type": "thinking_delta", "content": event["thinking"]}
                elif event["type"] == "response":
                    response = event["response"]

            if cancel_event and cancel_event.is_set():
                break

            if response is None:
                yield {"type": "error", "message": "model call failed after retries"}
                break

            thinking_block = next(
                (block for block in response.content if block.type == "thinking"), None
            )
            text_block = next(
                (block for block in response.content if block.type == "text"), None
            )
            tool_use_blocks = [
                block for block in response.content if block.type == "tool_use"
            ]

            # thinking + text already streamed as deltas above

            if tool_use_blocks:
                # Auto-re-resolve tools that expired from Redis but exist in catalogs
                for tb in tool_use_blocks:
                    if tb.name not in tools and tb.name not in mcp_routing:
                        if tb.name in TOOL_CATALOG:
                            # Re-inject from catalog + refresh Redis
                            tool_schemas.append(get_tool_schema(tb.name))
                            tools[tb.name] = get_tool_func(tb.name)
                            redis_state.add_discovered_tools(project_dir, [tb.name])
                        elif tb.name in MCP_CATALOG:
                            # Re-inject MCP + refresh Redis
                            redis_state.add_discovered_mcps(project_dir, [tb.name])
                            if end_resp == "LiteLLM":
                                new_oai_tools, new_routing = await resolve_mcp_for_litellm([MCP_CATALOG[tb.name]])
                                mcp_openai_tools.extend(new_oai_tools)
                                mcp_routing.update(new_routing)
                            else:
                                mcp_servers.append(get_mcp_server(tb.name))
                                tool_schemas.append(get_mcp_toolset(tb.name))

                # Split into: local tools, client-side MCP tools, truly unknown
                local_tool_blocks = [tb for tb in tool_use_blocks if tb.name in tools]
                mcp_tool_blocks = [tb for tb in tool_use_blocks if tb.name in mcp_routing]
                unknown_blocks = [tb for tb in tool_use_blocks
                                  if tb.name not in tools and tb.name not in mcp_routing]

                # Hallucinated tool names — must send error tool_result or API hangs
                if unknown_blocks:
                    content_blocks = []
                    if thinking_block:
                        content_blocks.append({
                            "type": "thinking",
                            "thinking": thinking_block.thinking,
                            "signature": thinking_block.signature,
                        })
                    for tb in unknown_blocks:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tb.id,
                            "name": tb.name,
                            "input": tb.input,
                        })
                    msgs.append({"role": "assistant", "content": content_blocks})
                    msgs.append({"role": "user", "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": f"Error: tool '{tb.name}' does not exist. Use search_tools to discover available tools.",
                            "is_error": True,
                        }
                        for tb in unknown_blocks
                    ]})
                    store_msgs(project_dir, msgs)
                    if not local_tool_blocks and not mcp_tool_blocks:
                        continue  # No valid tools this round, re-enter loop

                for tool_use_block in local_tool_blocks:
                    yield {
                        "type": "tool_call",
                        "name": tool_use_block.name,
                        "input": tool_use_block.input,
                        "id": tool_use_block.id,
                    }

                    if tool_use_block.name in DANGEROUS_TOOLS:
                        approval = {"approved": None}
                        yield {
                            "type": "approval_request",
                            "id": tool_use_block.id,
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

                if cancel_event and cancel_event.is_set():
                    break

                # Execute local tools
                tool_tasks = [
                    execute_tool_call(
                        tool_block,
                        tools,
                        project_dir,
                        end_resp,
                    )
                    for tool_block in local_tool_blocks
                ]
                # Execute MCP tools (client-side, for LiteLLM)
                mcp_tasks = [
                    call_mcp_tool(mcp_routing[tb.name], tb.name, tb.input)
                    for tb in mcp_tool_blocks
                ]

                all_blocks = local_tool_blocks + mcp_tool_blocks
                all_results = await asyncio.gather(*tool_tasks, *mcp_tasks)

                if all_blocks:
                    content_blocks = []
                    if thinking_block:
                        content_blocks.append(
                            {
                                "type": "thinking",
                                "thinking": thinking_block.thinking,
                                "signature": thinking_block.signature,
                            }
                        )
                    for tb in all_blocks:
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
                    for tool_block, result in zip(all_blocks, all_results):
                        # Handle search_tools discovery - inject into live loop
                        if tool_block.name == "search_tools" and isinstance(result, dict):
                            new_tools = result.get("discovered_tools", [])
                            new_mcps = result.get("discovered_mcps", [])
                            redis_state.add_discovered_tools(project_dir, new_tools)
                            redis_state.add_discovered_mcps(project_dir, new_mcps)
                            # Inject into current loop so next API call has them
                            for t_name in new_tools:
                                if t_name not in tools:
                                    tool_schemas.append(get_tool_schema(t_name))
                                    tools[t_name] = get_tool_func(t_name)
                            for m_name in new_mcps:
                                if m_name in MCP_CATALOG and m_name not in [s.get("name") for s in mcp_servers]:
                                    if end_resp == "LiteLLM":
                                        new_oai_tools, new_routing = await resolve_mcp_for_litellm([MCP_CATALOG[m_name]])
                                        mcp_openai_tools.extend(new_oai_tools)
                                        mcp_routing.update(new_routing)
                                    else:
                                        mcp_servers.append(get_mcp_server(m_name))
                                        tool_schemas.append(get_mcp_toolset(m_name))

                        yield {
                            "type": "tool_result",
                            "name": tool_block.name,
                            "output": str(result)[:500],  # Truncated for display
                            "id": tool_block.id,
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

                    # Post-tool nudge (lightweight user reminder, not full context)
                    plan_data_post = redis_state.get_plan(project_dir)
                    if plan_data_post:
                        post_reminder = get_post_tool_plan_reminder(plan_data_post, all_blocks[0].name)
                        msgs.append({"role": "user", "content": f"<system-reminder>{post_reminder}</system-reminder>"})
                    else:
                        msgs.append({"role": "user", "content": "<system-reminder>You just used a tool. Note what you found, then continue working silently — no narration needed.</system-reminder>"})

                    # Checkpoint after each tool round
                    store_msgs(project_dir, msgs)

            # No tool use = final response (text already streamed as deltas)
            if not tool_use_blocks and text_block:
                msgs.append({"role": "assistant", "content": text_block.text})
                store_msgs(project_dir, msgs)
                # Sliding window: summarize in background — don't block user prompt
                asyncio.create_task(summarize_and_trim(project_dir, msgs, end_resp))
                yield {"type": "final_text"}
                break

            # No text and no tools - break
            if not tool_use_blocks and not text_block:
                yield {"type": "final_text"}
                break

        except asyncio.CancelledError:
            break  # Propagate cancellation cleanly
        except Exception as e:
            yield {
                "type": "error",
                "message": f"{type(e).__name__}: {e}",
            }
            break  # Exit on error

    # Always save messages at end
    store_msgs(project_dir, msgs)

    yield {"type": "done"}
