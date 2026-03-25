"""Client-side MCP tool resolution for non-Anthropic backends.

When using LiteLLM/OpenAI proxy, we can't use Anthropic's server-side MCP.
Instead we act as the MCP client ourselves:
  1. Connect to MCP server → fetch tool schemas via tools/list
  2. Convert to OpenAI function-calling format → inject into tools array
  3. Route tool_use calls back to MCP server via tools/call

Uses the `mcp` package (pip install mcp).
Tries streamable HTTP first, falls back to SSE for older servers.
"""

from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client


# Cache: server_url -> {tools: [...openai format], routing: {name: server_url}}
_tool_cache = {}


@asynccontextmanager
async def _connect(server_url: str):
    """Connect to MCP server, trying streamable HTTP then SSE."""
    try:
        async with streamablehttp_client(server_url) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                yield session
                return
    except Exception:
        pass
    # Fallback: SSE (older servers like Manifold)
    async with sse_client(server_url) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            yield session


def _mcp_tool_to_openai(t) -> dict:
    """Convert a single MCP Tool to OpenAI ChatCompletionToolParam."""
    schema = dict(t.inputSchema or {})
    if "type" not in schema:
        schema["type"] = "object"
    if "properties" not in schema:
        schema["properties"] = {}
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": schema,
        },
    }


async def fetch_mcp_tools(server_url: str, server_name: str) -> tuple[list[dict], dict[str, str]]:
    """Connect to MCP server, return tools as OpenAI function schemas.

    Returns:
        (openai_tools, mcp_routing)
        - openai_tools: list of OpenAI ChatCompletionToolParam dicts
        - mcp_routing: {tool_name: server_url} for routing calls back
    """
    if server_url in _tool_cache:
        cached = _tool_cache[server_url]
        return cached["tools"], cached["routing"]

    openai_tools = []
    routing = {}

    async with _connect(server_url) as session:
        result = await session.list_tools()
        for t in result.tools:
            openai_tools.append(_mcp_tool_to_openai(t))
            routing[t.name] = server_url

    _tool_cache[server_url] = {"tools": openai_tools, "routing": routing}
    return openai_tools, routing


async def call_mcp_tool(server_url: str, tool_name: str, arguments: dict) -> str:
    """Execute a tool call on the MCP server. Returns result as string."""
    try:
        async with _connect(server_url) as session:
            result = await session.call_tool(tool_name, arguments)

            parts = []
            for block in (result.content or []):
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(f"[binary data: {block.mimeType}]")
                else:
                    parts.append(str(block))

            return "\n".join(parts) if parts else "OK"
    except Exception as e:
        return f"MCP tool error: {e}"


async def resolve_mcp_for_litellm(mcp_catalog_entries: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Given MCP_CATALOG entries, fetch all tools client-side.

    Args:
        mcp_catalog_entries: list of MCP_CATALOG values, each with "server" key

    Returns:
        (all_openai_tools, routing_map)
    """
    all_tools = []
    all_routing = {}

    for entry in mcp_catalog_entries:
        server = entry["server"]
        url = server["url"]
        name = server.get("name", url)
        try:
            tools, routing = await fetch_mcp_tools(url, name)
            all_tools.extend(tools)
            all_routing.update(routing)
        except Exception as e:
            print(f"[mcp_client] Failed to connect to {name}: {e}")

    return all_tools, all_routing
