"""Realistic message sequence as produced by claude_loop_.

Used by tokenization and genai conversion tests.
Matches exact shapes from claude_loop_.py:
  - System prompt: role=system (line 92)
  - User query: role=user, string content (line 130)
  - Assistant text: role=assistant, string content (line 289)
  - Assistant tool_use: role=assistant, content=[thinking+tool_use] (lines 236-253)
  - Tool result: role=user, content=[tool_result] (lines 276-285)
  - System reminder: role=user, content=<system-reminder>...</system-reminder> (line 157-159)
"""

PAD = "X" * 20000


TEST_MESSAGES = [
    # Initial system prompt (claude_loop_ line 92)
    {
        "role": "system",
        "content": "You are micro claude code - a CLI assistant for software engineering.\n\n## Environment\n- Project directory: /tmp/test-project\n",
    },
    # User query (claude_loop_ line 130)
    {"role": "user", "content": "Help me analyze these contracts"},
    # Simple assistant text response (claude_loop_ line 289)
    {
        "role": "assistant",
        "content": f"I'll help you analyze the contracts. Let me start by creating a plan. {PAD}",
    },
    # --- Tool cycle 1: thinking + tool_use + tool_result + reminder ---
    # Assistant with thinking + tool_use (claude_loop_ lines 236-253)
    {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": f"I will first make some notes {PAD}",
                "signature": "sig_abc123",
            },
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "write_to_journal",
                "input": {"entry": "Step 1: Read contracts"},
            },
        ],
    },
    # Tool result (claude_loop_ lines 276-285)
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": "Journal updated with 1 entry",
            }
        ],
    },
    # System reminder (claude_loop_ line 157-159) — role=user with <system-reminder> tags
    {
        "role": "user",
        "content": f"<system-reminder>\nHere are your current journal entries: {PAD}\n</system-reminder>",
    },
    # --- Tool cycle 2 ---
    {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": f"Now I need to search the vectorstore for relevant clauses {PAD}",
                "signature": "sig_def456",
            },
            {
                "type": "tool_use",
                "id": "call_2",
                "name": "vectorstore",
                "input": {"query": "contract terms"},
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call_2",
                "content": f"Retrieved 3 documents from vectorstore {PAD}",
            }
        ],
    },
    {
        "role": "user",
        "content": f"<system-reminder>\nYou just used a tool. Update your journal. Current journal: {PAD}\n</system-reminder>",
    },
    # --- Tool cycle 3 ---
    {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": f"I should update my journal with new findings {PAD}",
                "signature": "sig_ghi789",
            },
            {
                "type": "tool_use",
                "id": "call_3",
                "name": "write_to_journal",
                "input": {"entry": "Found 3 relevant clauses"},
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call_3",
                "content": f"Journal updated, diff: {PAD}",
            }
        ],
    },
]
