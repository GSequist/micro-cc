import json


def token_cutter(messages: list[dict], tokenizer, max_tokens: int) -> list[dict]:
    """
    Context window management via atomic chunk dropping.

    Groups tool cycles (assistant tool_use + tool_result + optional reminder)
    as indivisible units. Drops oldest chunks first. Always preserves the
    last real user message so the model never loses track of the query.

    Rules:
    1. System messages always kept
    2. Last real user message (not tool_result/system-reminder) always kept
    3. Tool cycles dropped as atomic units — no orphaned tool_use/tool_result
    4. Oldest chunks dropped first until under budget
    """
    if not messages:
        return messages

    def count_tokens(msg):
        content = msg.get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        return len(tokenizer.encode(content or ""))

    total = sum(count_tokens(m) for m in messages)
    if total <= max_tokens:
        return messages

    # Separate system (always kept) from conversation
    system_msgs = []
    conversation = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            conversation.append(msg)

    if not conversation:
        return messages

    system_tokens = sum(count_tokens(m) for m in system_msgs)
    budget = max_tokens - system_tokens

    # --- Identify the last real user message (must always keep) ---
    def is_real_user_msg(msg):
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
        # tool_result blocks → not a real user query
        if isinstance(content, list):
            return not any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            )
        # system-reminder injections → not a real user query
        if isinstance(content, str) and content.strip().startswith("<system-reminder>"):
            return False
        return True

    last_real_user_idx = None
    for i in range(len(conversation) - 1, -1, -1):
        if is_real_user_msg(conversation[i]):
            last_real_user_idx = i
            break

    # --- Group into atomic chunks ---
    # A tool cycle = assistant(tool_use) + user(tool_result) + optional user(system-reminder)
    # Everything else is a single-message chunk
    chunks = []  # each chunk is a list of conversation indices
    i = 0
    while i < len(conversation):
        msg = conversation[i]
        content = msg.get("content")

        # Detect assistant with tool_use blocks
        has_tool_use = (
            msg.get("role") == "assistant"
            and isinstance(content, list)
            and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
        )

        if has_tool_use:
            chunk = [i]
            j = i + 1
            # Grab following tool_result
            if j < len(conversation):
                nc = conversation[j].get("content")
                if isinstance(nc, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in nc
                ):
                    chunk.append(j)
                    j += 1
            # Grab following system-reminder (plan nudge)
            if j < len(conversation):
                nc = conversation[j].get("content", "")
                if (
                    conversation[j].get("role") == "user"
                    and isinstance(nc, str)
                    and nc.strip().startswith("<system-reminder>")
                ):
                    chunk.append(j)
                    j += 1
            chunks.append(chunk)
            i = j
        else:
            chunks.append([i])
            i += 1

    # --- Find which chunk is protected (contains last real user msg) ---
    protected_chunk_idx = None
    if last_real_user_idx is not None:
        for ci, chunk in enumerate(chunks):
            if last_real_user_idx in chunk:
                protected_chunk_idx = ci
                break

    # --- Drop oldest chunks first, skip protected ---
    conv_tokens = sum(count_tokens(m) for m in conversation)
    dropped = set()

    for ci, chunk in enumerate(chunks):
        if conv_tokens <= budget:
            break
        if ci == protected_chunk_idx:
            continue
        chunk_tokens = sum(count_tokens(conversation[idx]) for idx in chunk)
        conv_tokens -= chunk_tokens
        dropped.update(chunk)

    # Build trimmed conversation
    trimmed = [conversation[i] for i in range(len(conversation)) if i not in dropped]

    # Prepend truncation notice if we dropped anything
    if dropped and trimmed:
        trimmed.insert(0, {
            "role": "user",
            "content": "[Earlier conversation history has been truncated.]"
        })

    return system_msgs + trimmed
