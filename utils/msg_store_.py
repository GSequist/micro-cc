import datetime
import hashlib
import json
import os
from pathlib import Path


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
