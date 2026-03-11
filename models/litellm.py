"""LiteLLM model caller.

OpenAI-compatible endpoint (LiteLLM-style) wrapping Bedrock Claude.
Translates between Anthropic message format (used by claude_loop_)
and OpenAI chat completion format (used by the proxy).

Env vars:
    LITELLM_BASE_URL: Proxy base URL
    LITELLM_API_KEY:  API key for Authorization: Bearer header
"""

from typing import List, Dict, Any, Optional, Union
from openai import AsyncOpenAI
from dotenv import load_dotenv
from dataclasses import dataclass, field
import asyncio
import json
import os

load_dotenv()


# ---------------------------------------------------------------------------
# Lightweight wrappers matching Anthropic SDK response shape
# so claude_loop_.py works unchanged
# ---------------------------------------------------------------------------

@dataclass
class ContentBlock:
    type: str
    text: str = ""
    thinking: str = ""
    signature: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    id: str = ""


@dataclass
class Response:
    content: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------

def _tools_to_openai(anthropic_tools):
    """Anthropic tool schemas -> OpenAI function-calling format."""
    out = []
    for t in anthropic_tools:
        if t.get("type") == "mcp_toolset":
            continue  # MCP not supported through proxy
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return out or None


def _msgs_to_openai(anthropic_msgs):
    """Convert Anthropic-format message list to OpenAI chat format.

    Handles: system, user (string/multimodal/tool_result),
    assistant (string/tool_use+thinking blocks).
    Images converted to OpenAI image_url format.
    """
    out = []
    for msg in anthropic_msgs:
        role = msg.get("role")
        content = msg.get("content", "")

        # System messages pass through
        if role == "system":
            out.append({"role": "system", "content": content})
            continue

        # --- Assistant with content blocks (tool_use, text, thinking) ---
        if role == "assistant" and isinstance(content, list):
            text_parts = []
            tool_calls = []
            thinking_blocks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"]),
                        },
                    })
                elif block.get("type") == "thinking":
                    # LiteLLM requires thinking_blocks resent on assistant messages
                    thinking_blocks.append({
                        "type": "thinking",
                        "thinking": block.get("thinking", ""),
                        "signature": block.get("signature", ""),
                    })

            m = {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
            }
            if tool_calls:
                m["tool_calls"] = tool_calls
            if thinking_blocks:
                m["thinking_blocks"] = thinking_blocks
            out.append(m)
            continue

        # --- User with content blocks (tool_result, text, image, document) ---
        if role == "user" and isinstance(content, list):
            tool_results = []
            other_parts = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block.get("content", ""),
                    })
                elif block.get("type") == "text":
                    other_parts.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "image":
                    source = block.get("source", {})
                    media = source.get("media_type", "image/jpeg")
                    other_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media};base64,{source.get('data', '')}",
                        },
                    })
                elif block.get("type") == "document":
                    # Document blocks from tool results (vectorstore citations etc.)
                    # No OpenAI equivalent — convert to text placeholder
                    other_parts.append({
                        "type": "text",
                        "text": "[PDF document attached]",
                    })

            # Tool results become separate messages
            out.extend(tool_results)

            # Remaining content becomes a user message
            if other_parts:
                if len(other_parts) == 1 and other_parts[0].get("type") == "text":
                    out.append({"role": "user", "content": other_parts[0]["text"]})
                else:
                    out.append({"role": "user", "content": other_parts})
            continue

        # --- Simple string content ---
        out.append({"role": role, "content": content})

    return out


def _wrap_response(openai_resp):
    """OpenAI ChatCompletion -> Anthropic-like Response object."""
    choice = openai_resp.choices[0]
    msg = choice.message
    blocks = []

    # LiteLLM returns thinking_blocks for Anthropic models
    thinking_blocks = getattr(msg, "thinking_blocks", None)
    if thinking_blocks:
        # Use first thinking block (matches claude_loop_ which extracts one)
        tb = thinking_blocks[0]
        blocks.append(ContentBlock(
            type="thinking",
            thinking=tb.get("thinking", "") if isinstance(tb, dict) else getattr(tb, "thinking", ""),
            signature=tb.get("signature", "") if isinstance(tb, dict) else getattr(tb, "signature", ""),
        ))
    elif getattr(msg, "reasoning_content", None):
        # Fallback: some LiteLLM versions return reasoning_content as string
        blocks.append(ContentBlock(
            type="thinking",
            thinking=msg.reasoning_content,
            signature="",
        ))

    if msg.content:
        blocks.append(ContentBlock(type="text", text=msg.content))

    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            blocks.append(ContentBlock(
                type="tool_use",
                name=tc.function.name,
                input=args,
                id=tc.id,
            ))

    return Response(content=blocks)


# ---------------------------------------------------------------------------
# Main model call — same signature as models/anthropic.py
# ---------------------------------------------------------------------------

async def l_model_call(
    input: Union[List[Dict[str, Any]], str],
    model="bedrock.anthropic.claude-opus-4-6",
    encoded_image: Optional[Union[str, List[str]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    mcp_servers: list = None,  # ignored — no MCP through proxy
    stream: bool = False,
    thinking=False,
    max_tokens: int = 120000,
    client_timeout: int = 480,
    pdf: str = None,
    retries: int = 3,
):
    client = AsyncOpenAI(
        base_url=os.getenv(
            "LITELLM_BASE_URL", ""
        ),
        api_key=os.getenv("LITELLM_API_KEY", ""),
        timeout=client_timeout,
    )
    base_sleep = 2

    # ---- Build messages ----
    messages = []

    if pdf:
        # Proxy doesn't support native PDF blocks; pass as note
        messages = [{"role": "user", "content": f"[PDF document attached]\n\n{input}"}]
    elif encoded_image:
        content = []
        imgs = [encoded_image] if isinstance(encoded_image, str) else encoded_image
        for img in imgs:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img}"},
            })
        content.append({"type": "text", "text": input})
        messages = [{"role": "user", "content": content}]
    elif isinstance(input, str):
        messages = [{"role": "user", "content": input}]
    else:
        # Conversation history — convert from Anthropic format
        # System msgs pass through as role: "system" inline in the array
        messages = _msgs_to_openai(input)

    # ---- API parameters ----
    api_params = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": False,  # TODO: streaming support
        "messages": messages,
    }

    if thinking:
        api_params["reasoning_effort"] = "medium"

    if tools:
        openai_tools = _tools_to_openai(tools)
        if openai_tools:
            api_params["tools"] = openai_tools
            api_params["tool_choice"] = "auto"

    # ---- Call with retries ----
    for attempt in range(retries):
        try:
            response = await client.chat.completions.create(**api_params)
            return _wrap_response(response)

        except Exception as e:
            import traceback
            print(f"\n[litellm model_call]: {e}", flush=True)
            traceback.print_exc()
            if attempt < retries - 1:
                sleep_time = base_sleep * (2 ** attempt)
                print(
                    f"\n[litellm model_call]: Retrying in {sleep_time}s (attempt {attempt + 1}/{retries})..."
                )
                await asyncio.sleep(sleep_time)
            else:
                print(f"\n[litellm model_call]: Failed after {retries} attempts")

    return None


##########################################################

