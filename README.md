# micro-cc

```
        ┌─────────────────────────────────────────────────────────────┐
        │                                                             │
        │    ███╗   ███╗ ██╗  ██████╗ ██████╗   ██████╗               │
        │    ████╗ ████║ ██║ ██╔════╝ ██╔══██╗ ██╔═══██╗              │
        │    ██╔████╔██║ ██║ ██║      ██████╔╝ ██║   ██║              │
        │    ██║╚██╔╝██║ ██║ ██║      ██╔══██╗ ██║   ██║              │
        │    ██║ ╚═╝ ██║ ██║ ╚██████╗ ██║  ██║ ╚██████╔╝              │
        │    ╚═╝     ╚═╝ ╚═╝  ╚═════╝ ╚═╝  ╚═╝  ╚═════╝               │
        │                                                             │
        │              ██████╗  ██████╗                               │
        │             ██╔════╝ ██╔════╝                               │
        │    ─────────██║      ██║       ─────────────────────────    │
        │             ██║      ██║                                    │
        │             ╚██████╗ ╚██████╗                               │
        │              ╚═════╝  ╚═════╝                               │
        │                                                             │
        │    minimal local claude code-like app                       │
        │                                                             │
        └─────────────────────────────────────────────────────────────┘
```

Minimal Claude Code-like local application. Point it at any project directory and work.

## Install

```bash
git clone https://github.com/GSequist/micro-cc.git
cd micro-cc
python -m venv env
source env/bin/activate
pip install -r requirements.txt
```

## Environment

Create `.env` in the micro-cc directory:

```
ANTHROPIC_API_KEY=sk-ant-
OPENAI_API_KEY=sk-proj-
SERPAPI_KEY=
```

## Usage

```bash
source env/bin/activate
python start_.py /path/to/your/project
```

**Controls:**
- `Option+Enter` — submit prompt
- `Enter` — newline
- `Ctrl+C` — interrupt
- `/clear` — reset conversation
- `/exit` — quit

## Terminal

Recommended: **iTerm2**

For `Option+Enter` (submit) to work, configure iTerm2:

`iTerm2 → Settings → Profiles → Keys → General → Left Option Key → Esc+`

## Shell Alias

Add to `~/.zshrc`:

```bash
microcc() {
    (cd ~/micro-cc && source env/bin/activate && python start_.py "$@")
}
```

Then: `microcc /path/to/project`

## Architecture

```
start_.py (CLI)                     prompt_toolkit + rich rendering
       │
       │ async for event in claude_loop()
       ▼
claude_loop_.py (Core)              API calls, tool execution, JSONL storage
       │
       │ execute_tool_call()
       ▼
tools/                              bash_, read_, write_, edit_, glob_, grep_
       │
       ▼
micro-cc/projects/{name}_{hash}/  conversation persistence
```

**How it works:**
1. CLI starts FileWatcher on project dir, enters prompt loop
2. User query → `claude_loop()` async generator
3. Builds system prompt with project's `CLAUDE.md` + any file changes detected
4. Calls Anthropic API with tool definitions
5. Executes tool calls locally, loops until Claude stops calling tools
6. Yields events (`thinking`, `tool_call`, `tool_result`, `final_text`) for CLI to render
7. Saves conversation to JSONL (stripped of thinking blocks for replay)

**Tools:**
| Tool | Description |
|------|-------------|
| `bash_` | Subprocess execution in project_dir, timeout support |
| `read_` | Read files with line numbers, offset/limit |
| `write_` | Create/overwrite files, auto-creates dirs |
| `edit_` | Surgical string replacement (fails if ambiguous) |
| `glob_` | Find files by pattern, sorted by mtime |
| `grep_` | Regex search with context lines |

**Path handling:** Relative paths resolve to `project_dir`, absolute paths work anywhere.

**File watcher:** Detects external changes, injects into system prompt so Claude sees what you edited.
