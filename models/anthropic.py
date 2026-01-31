from typing import List, Dict, Any, Optional, Union
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from utils.helpers import tokenizer
import datetime
import asyncio
import json

load_dotenv()


def log_tokens(input_tokens: int, output_tokens: int, model: str):
    """utility function to log token usage"""
    log = {
        "timestamp": datetime.datetime.now().isoformat(),
        "input": input_tokens,
        "output": output_tokens,
        "model": model,
    }
    with open("/Users/georgesalapa/micro-cc/utils/token_usage.json", "a") as f:
        f.write(json.dumps(log) + "\n")


async def model_call(
    input: Union[List[Dict[str, Any]], str],
    model="claude-4.5",
    encoded_image: Optional[Union[str, List[str]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    mcp_servers: list = None,
    stream: bool = False,
    thinking=False,
    max_tokens: int = 60000,
    client_timeout: int = 480,
    pdf: str = None,
):
    client = AsyncAnthropic(timeout=client_timeout)
    retries = 3
    sleep_time = 2

    if model == "opus-4.5":
        model = "claude-opus-4-5-20251101"
    elif model == "claude-4.5":
        model = "claude-sonnet-4-5-20250929"
    elif model == "claude-4.5-haiku":
        model = "claude-haiku-4-5-20251001"

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
            "disable_parallel_tool_use": True,
        }

    # Track if we need beta endpoint for MCP
    use_beta = False
    betas = []

    if mcp_servers:
        api_parameters["mcp_servers"] = mcp_servers
        use_beta = True
        betas.append("mcp-client-2025-11-20")

    if thinking:
        api_parameters["thinking"] = {"type": "enabled", "budget_tokens": 16000}
        api_parameters["max_tokens"] = 60000
        use_beta = True
        betas.append("interleaved-thinking-2025-05-14")
    else:
        api_parameters["max_tokens"] = max_tokens

    # Add betas param if using beta endpoint
    if use_beta and betas:
        api_parameters["betas"] = betas

    api_parameters["messages"] = messages
    api_parameters["stream"] = stream

    for attempt in range(retries):
        try:
            if use_beta:
                response = await client.beta.messages.create(**api_parameters)
            else:
                response = await client.messages.create(**api_parameters)
            if hasattr(response, "usage"):
                input_tokens = getattr(response.usage, "input_tokens", 0)
                output_tokens = getattr(response.usage, "output_tokens", 0)
                await asyncio.to_thread(
                    log_tokens, input_tokens, output_tokens, api_parameters["model"]
                )
            return response

        except Exception as e:
            print(f"\n[model_call]: {e}", flush=True)
            if attempt < retries - 1:
                sleep_time = sleep_time * (2**attempt)
                print(f"\n[model_call]: Retrying in {sleep_time} seconds...")
                await asyncio.sleep(sleep_time)
            else:
                print(f"\n[model_call]: Failed after {retries} attempts")
                break

    return None


##########################################################
