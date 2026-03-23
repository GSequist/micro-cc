import json


def token_cutter(messages: list[dict], tokenizer, max_tokens: int) -> list[dict]:
    """
    Simple context window management: pop oldest user-assistant pairs
    until we fit in budget. No tool_use/tool_result unwrapping needed -
    we pop whole messages so pairs stay intact naturally.

    Rules:
    1. System message (index 0) always kept
    2. Most recent user message always kept (Claude loses track otherwise)
    3. Pop oldest non-system pairs from front until under budget
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

    # Find the last user message index (must always keep it)
    last_user_idx = None
    for i in range(len(conversation) - 1, -1, -1):
        if conversation[i].get("role") == "user":
            last_user_idx = i
            break

    # Pop from front of conversation until under budget
    # Always keep at least the last user message
    system_tokens = sum(count_tokens(m) for m in system_msgs)
    budget = max_tokens - system_tokens

    while len(conversation) > 1:
        conv_tokens = sum(count_tokens(m) for m in conversation)
        if conv_tokens <= budget:
            break

        # Don't pop the last user message
        if len(conversation) <= 1:
            break

        # Pop oldest message
        conversation.pop(0)

    # Prepend truncation notice if we trimmed anything
    original_conv_len = len(messages) - len(system_msgs)
    if len(conversation) < original_conv_len:
        conversation.insert(0, {
            "role": "user",
            "content": "[Earlier conversation history has been truncated.]"
        })

    return system_msgs + conversation
