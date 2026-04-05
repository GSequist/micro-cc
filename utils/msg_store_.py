import datetime
import hashlib
import json
import os
from pathlib import Path
from utils.helpers import tokenizer


MAX_MSGS_BEFORE_SUMMARY = 10
MAX_SUMMARY_INPUT_TOKENS = 10000


def _get_storage_dir(project_dir: str) -> Path:
    """Get CC-style storage path: ~/.micro-cc/projects/{project_hash}/

    Hash ensures valid folder name regardless of project path characters.
    """
    # Normalize and hash the project path
    normalized = os.path.abspath(os.path.expanduser(project_dir))
    path_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]

    # Human-readable prefix (last folder name)
    folder_name = os.path.basename(normalized) or "root"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in folder_name)

    # Store in ~/.micro-cc/projects/ (works for both source and pip installs)
    storage_dir = Path.home() / ".micro-cc" / "projects" / f"{safe_name}_{path_hash}"
    storage_dir.mkdir(parents=True, exist_ok=True)

    # Store project path mapping for debugging
    mapping_file = storage_dir / "project_path.txt"
    if not mapping_file.exists():
        mapping_file.write_text(normalized)

    return storage_dir


def store_msgs(project_dir: str, msgs: list) -> None:
    """Append messages to JSONL file for project.

    Overwrites file with all messages (full state save).
    Each line is one message JSON object.
    """
    storage_dir = _get_storage_dir(project_dir)
    jsonl_path = storage_dir / "messages.jsonl"

    with open(jsonl_path, "w") as f:
        for msg in msgs:
            normalized = _normalize_message(msg)
            f.write(json.dumps(normalized) + "\n")


def load_msgs(project_dir: str) -> list:
    """Load all messages from JSONL file for project."""
    storage_dir = _get_storage_dir(project_dir)
    jsonl_path = storage_dir / "messages.jsonl"

    if not jsonl_path.exists():
        return []

    msgs = []
    with open(jsonl_path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    normalized = json.loads(line)
                    msgs.append(_reconstruct_message(normalized))
                except json.JSONDecodeError:
                    continue  # Skip malformed lines

    return msgs


def erase_msgs(project_dir: str) -> None:
    """Clear conversation history for project."""
    storage_dir = _get_storage_dir(project_dir)
    jsonl_path = storage_dir / "messages.jsonl"

    if jsonl_path.exists():
        jsonl_path.unlink()


def _normalize_message(msg: dict) -> dict:
    """Convert message to serializable dict - strips thinking blocks (API rejects them)."""
    normalized = {
        "role": msg.get("role"),
        "ts": datetime.datetime.now().isoformat(),
    }

    content = msg.get("content")

    # Simple string content
    if isinstance(content, str):
        normalized["content"] = content

    # List content - preserve structure for API (NO thinking blocks)
    elif isinstance(content, list):
        normalized["content"] = []
        for item in content:
            # Anthropic SDK objects - convert to dict
            if hasattr(item, "type"):
                if item.type == "thinking":
                    continue  # API doesn't accept thinking as input
                elif item.type == "tool_use":
                    normalized["content"].append({
                        "type": "tool_use",
                        "id": item.id,
                        "name": item.name,
                        "input": item.input
                    })
                elif item.type == "text":
                    normalized["content"].append({
                        "type": "text",
                        "text": item.text
                    })
                elif item.type == "tool_result":
                    normalized["content"].append({
                        "type": "tool_result",
                        "tool_use_id": item.tool_use_id,
                        "content": item.content
                    })
            # Already a dict
            elif isinstance(item, dict):
                # Skip thinking blocks in dict form too
                if item.get("type") == "thinking":
                    continue
                normalized["content"].append(item)

        # Unwrap single text block
        if len(normalized["content"]) == 1 and normalized["content"][0].get("type") == "text":
            normalized["content"] = normalized["content"][0]["text"]

    return normalized


def _reconstruct_message(normalized: dict) -> dict:
    """Reconstruct API-compatible message from stored format."""
    return {
        "role": normalized["role"],
        "content": normalized.get("content", "")
    }


# ---------------------------------------------------------------------------
# Conversation summary (sliding window)
# ---------------------------------------------------------------------------

def load_summary(project_dir: str) -> str:
    """Load conversation summary if it exists."""
    storage_dir = _get_storage_dir(project_dir)
    summary_path = storage_dir / "summary.json"
    if not summary_path.exists():
        return ""
    try:
        data = json.loads(summary_path.read_text())
        return data.get("content", "")
    except (json.JSONDecodeError, KeyError):
        return ""


def _store_summary(project_dir: str, summary: str) -> None:
    """Persist conversation summary."""
    storage_dir = _get_storage_dir(project_dir)
    summary_path = storage_dir / "summary.json"
    summary_path.write_text(json.dumps({
        "content": summary,
        "ts": datetime.datetime.now().isoformat(),
    }))


def erase_summary(project_dir: str) -> None:
    """Delete conversation summary."""
    storage_dir = _get_storage_dir(project_dir)
    summary_path = storage_dir / "summary.json"
    if summary_path.exists():
        summary_path.unlink()


def _extract_excess_text(msgs: list) -> str:
    """Filter user/assistant string content from excess msgs, reversed, capped by tokens."""
    parts = []
    total_tokens = 0

    for msg in reversed(msgs):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if not isinstance(content, str) or role not in ("user", "assistant"):
            continue

        line = f"{role}: {content}"
        line_tokens = len(tokenizer.encode(line))
        if total_tokens + line_tokens > MAX_SUMMARY_INPUT_TOKENS:
            break

        parts.append(line)
        total_tokens += line_tokens

    parts.reverse()
    return "\n".join(parts)


async def _call_haiku_summary(prev_summary: str, messages_text: str) -> str:
    """Call Haiku to produce/enrich conversation summary."""
    prompt = (
        "You are a precise summarization assistant. Progressively summarize "
        "conversation history while maintaining critical context.\n\n"
        "INSTRUCTIONS:\n"
        "1. Build upon the previous summary by incorporating new information chronologically\n"
        "2. Preserve: file paths, function names, key decisions, errors, code changes\n"
        "3. Keep temporal sequence of events\n"
        "4. IMPORTANT: Your summary MUST be under 2000 tokens. Be concise but complete.\n"
        "5. If new content adds nothing, return previous summary unchanged.\n\n"
    )
    if prev_summary:
        prompt += f"Current summary:\n{prev_summary}\n\n"
    prompt += f"New messages:\n{messages_text}\n\nNew summary:"

    try:
        from utils.helpers import get_endpoint
        if get_endpoint() == "LiteLLM":
            from models.litellm import l_model_call
            resp = await l_model_call(
                input=prompt,
                model="bedrock.anthropic.claude-haiku-4-5",
                max_tokens=2000,
            )
        else:
            from models.anthropic import a_model_call
            resp = await a_model_call(
                input=prompt,
                model="claude-4.5-haiku",
                max_tokens=2000,
            )

        if resp and resp.content:
            summary = resp.content[0].text
            # Hard cap: truncate if Haiku exceeded token limit
            tokens = tokenizer.encode(summary)
            if len(tokens) > 2000:
                summary = tokenizer.decode(tokens[:2000])
            # print(f"adding summary to store {summary}\n")
            return summary
    except Exception as e:
        print(f"[summarize] Haiku call failed: {e}")

    return prev_summary or ""


async def summarize_and_trim(project_dir: str, msgs: list) -> None:
    """Sliding window: summarize excess messages into summary.json. Does NOT touch msgs."""
    # Skip system messages
    non_system = [m for m in msgs if m.get("role") != "system"]

    if len(non_system) <= MAX_MSGS_BEFORE_SUMMARY:
        return

    excess_count = len(non_system) - MAX_MSGS_BEFORE_SUMMARY
    to_summarize = non_system[:excess_count]

    # Filter to user/assistant text, reversed, capped by token window
    messages_text = _extract_excess_text(to_summarize)
    if not messages_text.strip():
        return

    prev_summary = load_summary(project_dir)
    new_summary = await _call_haiku_summary(prev_summary, messages_text)
    _store_summary(project_dir, new_summary)
