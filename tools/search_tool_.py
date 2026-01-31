from models.schema import function_to_schema
from functools import partial
import numpy as np

# Import all discoverable tools
from tools.vision_tools_ import vision
from tools.web_tools_ import (
    web_search,
    archive_search,
    page_up,
    page_down,
    find_on_page,
    find_next,
    download_from_url,
    text_file,
)
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI(timeout=300)

# tool_name -> {func, schema, search_text, embedding (lazily computed)}
TOOL_CATALOG = {}
_embeddings_computed = False


def _register(func, search_text: str):
    """Register a tool in the catalog at module load time"""
    name = func.func.__name__ if isinstance(func, partial) else func.__name__
    TOOL_CATALOG[name] = {
        "func": func,
        "schema": function_to_schema(func),
        "search_text": f"{name} {search_text}",
        "embedding": None,  # Computed lazily on first search
    }


# Register discoverable tools (NOT always-available ones)

_register(vision, "analyze image vision see picture screenshot OCR read image")

# Web research tools - full suite for autonomous web research
_register(
    web_search,
    "web search google internet query find information online browse",
)
_register(
    archive_search,
    "wayback machine archive historical web page past version internet archive",
)
_register(
    page_up,
    "scroll up page navigation browser view previous content",
)
_register(
    page_down,
    "scroll down page navigation browser view more content continue reading",
)
_register(
    find_on_page,
    "find search text on page ctrl+f locate string browser",
)
_register(
    find_next,
    "find next occurrence search continue browser navigation",
)
_register(
    download_from_url,
    "download file url xlsx pptx docx wav mp3 save file from web",
)
_register(
    text_file,
    "read downloaded file convert to text markdown xlsx pptx docx pdf content",
)

MCP_CATALOG = {
    "pubmed": {
        "server": {
            "type": "url",
            "url": "https://pubmed.mcp.claude.com/mcp",
            "name": "pubmed",
        },
        "toolset": {"type": "mcp_toolset", "mcp_server_name": "pubmed"},
        "search_text": "pubmed medical literature research papers scientific articles",
        "embedding": None,
    },
}


async def _ensure_embeddings():
    """Lazily compute tool embeddings on first search"""
    global _embeddings_computed
    if _embeddings_computed:
        return

    # Batch embed all search texts (tools + MCPs)
    tool_names = list(TOOL_CATALOG.keys())
    mcp_names = list(MCP_CATALOG.keys())

    search_texts = [TOOL_CATALOG[name]["search_text"] for name in tool_names]
    search_texts += [MCP_CATALOG[name]["search_text"] for name in mcp_names]

    if not search_texts:
        _embeddings_computed = True
        return

    response = await client.embeddings.create(
        input=search_texts, model="text-embedding-3-small"
    )

    # Store embeddings back - tools first, then MCPs
    for i, name in enumerate(tool_names):
        TOOL_CATALOG[name]["embedding"] = np.array(response.data[i].embedding)

    offset = len(tool_names)
    for i, name in enumerate(mcp_names):
        MCP_CATALOG[name]["embedding"] = np.array(response.data[offset + i].embedding)

    _embeddings_computed = True


async def search_tools(
    query: str,
    *,
    project_dir: str = "",
):
    """Search for tools by describing what you need to accomplish.

    Args:
        query: Natural language description of the capability you need.
            Examples: "search clinical trials", "analyze image", "run Python code"
    """
    # Ensure tool embeddings are computed (only once)
    await _ensure_embeddings()

    # Embed the query
    query_resp = await client.embeddings.create(
        input=query, model="text-embedding-3-small"
    )
    query_emb = np.array(query_resp.data[0].embedding)

    # Score all tools by cosine similarity
    scores = []
    for name, data in TOOL_CATALOG.items():
        tool_emb = data["embedding"]
        if tool_emb is None:
            continue
        score = np.dot(query_emb, tool_emb) / (
            np.linalg.norm(query_emb) * np.linalg.norm(tool_emb)
        )
        scores.append((name, float(score), data["schema"]["description"][:80]))

    scores.sort(key=lambda x: x[1], reverse=True)
    matches = [(n, d) for n, s, d in scores[:5] if s > 0.3]

    # Score all MCPs
    mcp_scores = []
    for name, data in MCP_CATALOG.items():
        mcp_emb = data["embedding"]
        if mcp_emb is None:
            continue
        score = np.dot(query_emb, mcp_emb) / (
            np.linalg.norm(query_emb) * np.linalg.norm(mcp_emb)
        )
        mcp_scores.append((name, float(score), data["search_text"][:80]))

    mcp_scores.sort(key=lambda x: x[1], reverse=True)
    mcp_matches = [(n, d) for n, s, d in mcp_scores[:3] if s > 0.3]

    if not matches and not mcp_matches:
        return "No matching tools found."

    return {
        "discovered_tools": [n for n, _ in matches],
        "discovered_mcps": [n for n, _ in mcp_matches],
    }


def get_tool_schema(name: str):
    """Get schema for injection"""
    return TOOL_CATALOG[name]["schema"]


def get_tool_func(name: str):
    """Get func for execution"""
    return TOOL_CATALOG[name]["func"]


def get_mcp_server(name: str):
    """Get MCP server config for API call"""
    return MCP_CATALOG[name]["server"]


def get_mcp_toolset(name: str):
    """Get MCP toolset config for tools array"""
    return MCP_CATALOG[name]["toolset"]
