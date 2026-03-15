import asyncio
import json
import os
import shutil
import uuid

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from claude_loop_ import claude_loop
from utils.file_watcher_ import FileWatcher
from utils.msg_store_ import erase_msgs
from cache.redis_cache import RedisStateManager

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

watchers = {}   # project_dir → FileWatcher
pending = {}    # project_dir → {"generator": async_gen, "approval": dict, "state": dict}


# ─── Session + Upload ─────────────────────────────────────────────

@app.post("/session")
async def create_session(request: Request):
    """Initialize a project session — starts file watcher."""
    data = await request.json()
    project_dir = data.get("project_dir", "")
    if not project_dir:
        return {"status": "error", "message": "project_dir required"}

    os.makedirs(project_dir, exist_ok=True)
    if project_dir not in watchers:
        w = FileWatcher(project_dir)
        w.start()
        watchers[project_dir] = w
    return {"status": "ok", "project_dir": project_dir}


@app.post("/upload")
async def upload_files(
    project_dir: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """Upload files into the project directory."""
    uploaded = []
    for f in files:
        dest = os.path.join(project_dir, f.filename)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        uploaded.append(f.filename)
    return {"uploaded": uploaded}


# ─── SSE Chat (Vercel AI SDK v5 — UI Message Stream Protocol) ─────
#
# Format: data: {json}\n\n   (standard SSE)
# Header: x-vercel-ai-ui-message-stream: v1
#
# Chunk types:
#   start              → {type:"start", messageId}
#   start-step         → {type:"start-step"}
#   reasoning-start    → {type:"reasoning-start", id}
#   reasoning-delta    → {type:"reasoning-delta", id, delta}
#   reasoning-end      → {type:"reasoning-end", id}
#   text-start         → {type:"text-start", id}
#   text-delta         → {type:"text-delta", id, delta}
#   text-end           → {type:"text-end", id}
#   tool-input-available  → {type:"tool-input-available", toolCallId, toolName, input, dynamic:true}
#   tool-approval-request → {type:"tool-approval-request", approvalId, toolCallId}
#   tool-output-available → {type:"tool-output-available", toolCallId, output}
#   tool-output-denied    → {type:"tool-output-denied", toolCallId}
#   finish-step        → {type:"finish-step"}
#   finish             → {type:"finish", finishReason}
#   abort              → {type:"abort", reason?}
#   [DONE]             → signals stream end
#

SSE_HEADERS = {
    "x-vercel-ai-ui-message-stream": "v1",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse(chunk: dict) -> str:
    """Format a chunk as an SSE data line."""
    return f"data: {json.dumps(chunk)}\n\n"


async def sse_claude_loop(query, project_dir, end_resp, watcher, resume=False):
    """Async generator: consumes claude_loop events, yields Vercel AI SDK v5 SSE lines.

    When resume=True, picks up a paused generator from pending[] instead of
    starting a new claude_loop. The approval dict is resolved before resuming.
    """

    redis_state = RedisStateManager()

    # ── Resume existing paused generator ──
    if resume and project_dir in pending:
        p = pending.pop(project_dir)
        gen = p["generator"]
        p["approval"]["approved"] = True
        state = p["state"]
        in_thinking = state["in_thinking"]
        in_text = state["in_text"]
        step_open = state["step_open"]
        thinking_id = state["thinking_id"]
        text_id = state["text_id"]
        msg_id = state["msg_id"]
    else:
        # ── Fresh generator ──
        gen = claude_loop(
            query=query,
            project_dir=project_dir,
            end_resp=end_resp,
            watcher=watcher,
        )
        in_thinking = False
        in_text = False
        step_open = False
        thinking_id = None
        text_id = None
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"

    # ── State tracking helpers ──
    def close_thinking():
        nonlocal in_thinking
        if in_thinking:
            tid = thinking_id
            in_thinking = False
            return sse({"type": "reasoning-end", "id": tid})
        return None

    def close_text():
        nonlocal in_text
        if in_text:
            tid = text_id
            in_text = False
            return sse({"type": "text-end", "id": tid})
        return None

    def open_step():
        nonlocal step_open
        if not step_open:
            step_open = True
            return sse({"type": "start-step"})
        return None

    def close_step():
        nonlocal step_open
        if step_open:
            step_open = False
            return sse({"type": "finish-step"})
        return None

    # ── Message start (only for fresh streams, not resume) ──
    if not resume:
        yield sse({"type": "start", "messageId": msg_id})

    async for event in gen:
        # ── Check if frontend requested stop ──
        if not redis_state.get_streaming_state(project_dir):
            line = close_thinking()
            if line:
                yield line
            line = close_text()
            if line:
                yield line
            line = close_step()
            if line:
                yield line
            yield sse({"type": "finish", "finishReason": "stop"})
            yield "data: [DONE]\n\n"
            return

        etype = event.get("type")

        # ── Approval: pause the generator, end this SSE stream ──
        if etype == "approval_request":
            tool_call_id = event.get("id", f"call-{uuid.uuid4().hex[:8]}")
            approval_id = f"approval-{uuid.uuid4().hex[:8]}"

            pending[project_dir] = {
                "generator": gen,
                "approval": event["approval"],
                "state": {
                    "in_thinking": in_thinking,
                    "in_text": in_text,
                    "step_open": step_open,
                    "thinking_id": thinking_id,
                    "text_id": text_id,
                    "msg_id": msg_id,
                },
                "tool_name": event.get("name", ""),
                "tool_input": event.get("input", {}),
                "tool_id": tool_call_id,
                "approval_id": approval_id,
            }
            # Emit native tool-approval-request so frontend sees approval state
            yield sse({
                "type": "tool-approval-request",
                "approvalId": approval_id,
                "toolCallId": tool_call_id,
            })
            yield "data: [DONE]\n\n"
            return

        if etype == "cancelled":
            line = close_step()
            if line:
                yield line
            yield sse({"type": "finish", "finishReason": "stop"})
            yield "data: [DONE]\n\n"
            return

        # ── Thinking deltas ──
        if etype == "thinking_delta":
            line = open_step()
            if line:
                yield line

            if not in_thinking:
                in_thinking = True
                thinking_id = f"think-{uuid.uuid4().hex[:8]}"
                yield sse({"type": "reasoning-start", "id": thinking_id})

            content = event.get("content", "")
            if content:
                for char in content:
                    yield sse({"type": "reasoning-delta", "id": thinking_id, "delta": char})
                    await asyncio.sleep(0.01)
            continue

        # ── Text deltas ──
        if etype == "text_delta":
            line = close_thinking()
            if line:
                yield line

            line = open_step()
            if line:
                yield line

            content = event.get("content", "")
            if not content:
                continue

            if not in_text:
                in_text = True
                text_id = f"text-{uuid.uuid4().hex[:8]}"
                yield sse({"type": "text-start", "id": text_id})

            for char in content:
                yield sse({"type": "text-delta", "id": text_id, "delta": char})
                await asyncio.sleep(0.01)
            continue

        # ── Tool call ──
        if etype == "tool_call":
            line = close_thinking()
            if line:
                yield line
            line = close_text()
            if line:
                yield line

            line = open_step()
            if line:
                yield line

            tool_call_id = event.get("id", f"call-{uuid.uuid4().hex[:8]}")
            tool_name = event.get("name", "unknown")
            tool_input = event.get("input", {})

            yield sse({
                "type": "tool-input-available",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "input": tool_input,
                "dynamic": True,
            })
            continue

        # ── Tool result ──
        if etype == "tool_result":
            tool_call_id = event.get("id", "")
            output = event.get("output", "")

            yield sse({
                "type": "tool-output-available",
                "toolCallId": tool_call_id,
                "output": output,
            })
            continue

        # ── Status messages ──
        if etype == "status":
            continue

        # ── Error ──
        if etype == "error":
            error_msg = event.get("message", "Unknown error")
            line = close_thinking()
            if line:
                yield line
            yield sse({"type": "error", "errorText": f"⚠️ {error_msg}"})
            continue

        # ── Final text signal ──
        if etype == "final_text":
            line = close_thinking()
            if line:
                yield line
            line = close_text()
            if line:
                yield line
            line = close_step()
            if line:
                yield line
            continue

        # ── Done ──
        if etype == "done":
            line = close_thinking()
            if line:
                yield line
            line = close_text()
            if line:
                yield line
            line = close_step()
            if line:
                yield line
            continue

    # ── Message finish ──
    yield sse({"type": "finish", "finishReason": "stop"})
    yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat(request: Request):
    data = await request.json()
    project_dir = data.get("project_dir", "")
    endpoint = data.get("endpoint", "LiteLLM")
    messages = data.get("messages", [])

    # useChat sends full message history as UIMessage[] (with parts[]).
    # We only need the latest user message text.
    # claude_loop manages its own message history via msg_store.
    latest_message = ""
    if messages:
        last = messages[-1]
        # v6 UIMessage format: {id, role, parts: [{type:"text", text:"..."}, ...]}
        parts = last.get("parts", [])
        if parts:
            text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
            latest_message = "\n".join(text_parts)
        else:
            # Fallback for plain string content (shouldn't happen with v6 but defensive)
            content = last.get("content", "")
            if isinstance(content, str):
                latest_message = content

    if not latest_message:
        return StreamingResponse(
            iter(["data: [DONE]\n\n"]),
            media_type="text/event-stream",
        )

    # Ensure session is initialized
    if project_dir not in watchers:
        os.makedirs(project_dir, exist_ok=True)
        w = FileWatcher(project_dir)
        w.start()
        watchers[project_dir] = w

    watcher = watchers.get(project_dir)

    # Mark stream as active
    redis_state = RedisStateManager()
    redis_state.set_streaming_state(project_dir, True)

    return StreamingResponse(
        sse_claude_loop(
            query=latest_message,
            project_dir=project_dir,
            end_resp=endpoint,
            watcher=watcher,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# ─── Approval Flow ────────────────────────────────────────────────

@app.post("/api/chat/approve")
async def approve_tool(request: Request):
    """Resume a paused generator after human approves a tool call."""
    data = await request.json()
    project_dir = data.get("project_dir", "")

    if project_dir not in pending:
        return {"status": "error", "message": "no pending approval for this project"}

    redis_state = RedisStateManager()
    redis_state.set_streaming_state(project_dir, True)

    return StreamingResponse(
        sse_claude_loop(
            query="",
            project_dir=project_dir,
            end_resp="",
            watcher=None,
            resume=True,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/api/chat/deny")
async def deny_tool(request: Request):
    """Reject a pending tool call — emits tool-output-denied, then cleans up generator."""
    data = await request.json()
    project_dir = data.get("project_dir", "")

    if project_dir not in pending:
        return {"status": "error", "message": "no pending approval for this project"}

    p = pending.pop(project_dir)
    p["approval"]["approved"] = False

    tool_call_id = p.get("tool_id", "")

    async def denied_stream():
        yield sse({"type": "tool-output-denied", "toolCallId": tool_call_id})
        yield sse({"type": "finish-step"})
        yield sse({"type": "finish", "finishReason": "stop"})
        yield "data: [DONE]\n\n"

    # Drain the generator in background so it can clean up
    async def drain_cancelled():
        async for event in p["generator"]:
            break

    asyncio.create_task(drain_cancelled())

    return StreamingResponse(
        denied_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.get("/api/chat/pending")
async def get_pending(project_dir: str = ""):
    """Check if there's a pending approval for a project."""
    if project_dir in pending:
        p = pending[project_dir]
        return {
            "pending": True,
            "tool_name": p.get("tool_name", ""),
            "tool_input": str(p.get("tool_input", {})),
            "tool_id": p.get("tool_id", ""),
        }
    return {"pending": False}


# ─── Stop / Clear ─────────────────────────────────────────────────

@app.post("/api/chat/stop")
async def stop_stream(request: Request):
    """Signal the active stream to stop."""
    redis_state = RedisStateManager()
    data = await request.json()
    project_dir = data.get("project_dir", "")

    redis_state.set_streaming_state(project_dir, False)
    return {"status": "stopping", "project_dir": project_dir}


@app.post("/clear")
async def clear(request: Request):
    data = await request.json()
    project_dir = data.get("project_dir", "")
    erase_msgs(project_dir)
    return {"status": "cleared"}


# ─── Run ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
