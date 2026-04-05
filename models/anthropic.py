from typing import List, Dict, Any, Optional, Union
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from dataclasses import dataclass, field
import asyncio
import json


import os
load_dotenv(os.path.expanduser("~/.micro-cc/.env"))
load_dotenv()


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


async def _wrap_stream(raw_stream):
    """Iterate Anthropic SDK stream, yield standardized deltas + final Response."""
    text = ""
    thinking = ""
    thinking_signature = ""
    tool_blocks = []
    current_tool = None

    async for event in raw_stream:
        if event.type == "content_block_start":
            if event.content_block.type == "tool_use":
                current_tool = {"id": event.content_block.id, "name": event.content_block.name, "input_json": ""}

        elif event.type == "content_block_delta":
            if event.delta.type == "text_delta":
                text += event.delta.text
                yield {"type": "text_delta", "text": event.delta.text}
            elif event.delta.type == "thinking_delta":
                thinking += event.delta.thinking
                yield {"type": "thinking_delta", "thinking": event.delta.thinking}
            elif event.delta.type == "signature_delta":
                thinking_signature += event.delta.signature or ""
            elif event.delta.type == "input_json_delta":
                if current_tool:
                    current_tool["input_json"] += event.delta.partial_json

        elif event.type == "content_block_stop":
            if current_tool:
                args = json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                tool_blocks.append(current_tool | {"input": args})
                current_tool = None

    blocks = []
    if thinking:
        blocks.append(ContentBlock(type="thinking", thinking=thinking, signature=thinking_signature))
    if text:
        blocks.append(ContentBlock(type="text", text=text))
    for tb in tool_blocks:
        blocks.append(ContentBlock(type="tool_use", name=tb["name"], input=tb["input"], id=tb["id"]))

    yield {"type": "response", "response": Response(content=blocks)}

async def a_model_call(
    input: Union[List[Dict[str, Any]], str],
    model="claude-4.6",
    encoded_image: Optional[Union[str, List[str]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    mcp_servers: list = None,
    stream: bool = False,
    thinking=False,
    max_tokens: int = 60000,
    client_timeout: int = 480,
    pdf: str = None,
    retries: int = 3,
):
    client = AsyncAnthropic(timeout=client_timeout)
    base_sleep = 2

    MODEL_MAP = {
        "opus-4.6": "claude-opus-4-6",
        "sonnet-4.6": "claude-sonnet-4-6",
        "haiku-4.5": "claude-haiku-4-5-20251001",
        # legacy aliases
        "claude-4.6": "claude-sonnet-4-6",
        "claude-4.5-haiku": "claude-haiku-4-5-20251001",
    }
    model = MODEL_MAP.get(model, "claude-sonnet-4-6")

    system_prompts = []
    messages = []
    if pdf:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf,
                        },
                    },
                    {
                        "type": "text",
                        "text": input,
                    },
                ],
            }
        ]
    elif encoded_image:
        content = []
        if isinstance(encoded_image, str):
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": encoded_image,
                    },
                }
            )
        else:
            for img in encoded_image:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img,
                        },
                    }
                )

        content.append({"type": "text", "text": input})
        messages = [{"role": "user", "content": content}]
    elif isinstance(input, str):
        messages = [{"role": "user", "content": input}]
    else:
        for msg in input:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("system"):
                if isinstance(content, str):
                    system_prompts.append(content)
                continue
            messages.append({"role": role, "content": content})

    api_parameters = {"model": model}

    if system_prompts:
        api_parameters["system"] = "\n".join(system_prompts)

    if tools:
        api_parameters["tools"] = tools
        api_parameters["tool_choice"] = {
            "type": "auto",
            "disable_parallel_tool_use": False,
        }

    betas = ["mcp-client-2025-11-20"]
    api_parameters["betas"] = betas

    if mcp_servers:
        api_parameters["mcp_servers"] = mcp_servers

    if thinking and model != "claude-haiku-4-5-20251001":
        api_parameters["thinking"] = {"type": "adaptive"}
        api_parameters["output_config"] = {"effort": "high"}
        api_parameters["max_tokens"] = max_tokens
    else:
        api_parameters["max_tokens"] = max_tokens

    api_parameters["messages"] = messages
    api_parameters["stream"] = stream

    for attempt in range(retries):
        try:
            response = await client.beta.messages.create(**api_parameters)
            return _wrap_stream(response) if stream else response

        except Exception as e:
            print(f"\n[model_call]: {e}", flush=True)
            if attempt < retries - 1:
                sleep_time = base_sleep * (2**attempt)
                print(
                    f"\n[model_call]: Retrying in {sleep_time}s (attempt {attempt + 1}/{retries})..."
                )
                await asyncio.sleep(sleep_time)
            else:
                print(f"\n[model_call]: Failed after {retries} attempts")

    return None


##########################################################
