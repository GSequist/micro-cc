from typing import List, Dict, Any, Optional, Union
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
import asyncio


load_dotenv()


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

    if model == "opus-4.6":
        model = "claude-opus-4-6"
    elif model == "claude-4.6":
        model = "claude-sonnet-4-6"
    elif model == "claude-4.5-haiku":
        model = "claude-haiku-4-5-20251001"
    else:
        # Fallback for stale DB values (e.g. old "opus-4.5")
        model = "claude-sonnet-4-6"

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

    betas = ["mcp-client-2025-11-20"]
    api_parameters["betas"] = betas

    if mcp_servers:
        api_parameters["mcp_servers"] = mcp_servers

    if thinking and model != "claude-haiku-4-5-20251001":
        api_parameters["thinking"] = {"type": "adaptive"}
        api_parameters["output_config"] = {"effort": "medium"}
        api_parameters["max_tokens"] = max_tokens
    else:
        api_parameters["max_tokens"] = max_tokens

    api_parameters["messages"] = messages
    api_parameters["stream"] = stream

    for attempt in range(retries):
        try:
            response = await client.beta.messages.create(**api_parameters)
            return response

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
