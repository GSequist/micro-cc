import asyncio
import os
import sys
from claude_loop_ import claude_loop
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.completion import PathCompleter
from rich.console import Console
from rich.markdown import Markdown
from utils.file_watcher_ import FileWatcher


async def consumeloop(query, project_dir, end_resp, console, watcher: FileWatcher):
    async for event in claude_loop(query=query, project_dir=project_dir, end_resp=end_resp, watcher=watcher):
        etype = event.get("type")

        if etype == "status":
            console.print(f"  ⋯ {event.get('message', '')}")

        elif etype == "approval_request":
            name = event.get("name","")
            inp = event.get("input", {})
            approval = event.get("approval", None)

            console.print(f"\n ⚠️  {name}: {inp}")

            approval_session = PromptSession()
            response = (await approval_session.prompt_async("  Execute? [Y/n]: ")).strip().lower()

            if response in ("", "yes", "y"):
                approval["approved"] = True
            else:
                approval["approved"] = False
                console.print("  [cancelled]")

        elif etype == "cancelled":
            console.print("  [operation cancelled, conversation saved]")

        elif etype == "thinking":
            # Show truncated thinking as status
            thinking = event.get("content", "")
            console.print(Markdown(f"  💭 {thinking}"))

        elif etype == "text":
            # Intermediate text (before tool calls)
            text = event.get("content", "")
            console.print(Markdown(f"  📝 {text}"))

        elif etype == "tool_call":
            name = event.get("name", "?")
            console.print(Markdown(f"  🔧 `{name}`"))

        elif etype == "tool_result":
            name = event.get("name", "?")
            output = event.get("output", "")[:2000]
            console.print(Markdown(f"  ✓ `{name}`: {output}..."))
            console.print("  ─────────────────────────────────────")

        elif etype == "final_text":
            # Final response - print full
            console.print(Markdown(f"\n{event.get('content', '')}\n"))

        elif etype == "error":
            console.print(Markdown(f"\n⚠️  {event.get('message', 'Unknown error')}\n"))

        elif etype == "done":
            pass  # Ready for next input


def print_banner(console):
    console.print("\n╭─ micro-cc ─────────────────────────────╮")
    console.print("│                                        │")
    console.print("│  Option→Enter  submit /clear  reset    │")
    console.print("│  Ctrl+C     interrupt /exit   quit     │")
    console.print("│                                        │")
    console.print("╰────────────────────────────────────────╯\n")


async def start_():
    """Minimal CLI entry point for micro-cc.

    Usage: python start_.py [project_dir]
    """

    bindings = KeyBindings()

    @bindings.add('enter')  # Enter = newline (natural typing)
    def _(event):
        event.current_buffer.insert_text('\n')

    @bindings.add('escape', 'enter')  # Esc then Enter = submit
    def _(event):
        event.current_buffer.validate_and_handle()

    session = PromptSession(key_bindings=bindings, multiline=True)

    console = Console()

    print_banner(console)

    # Get project dir from arg or default to cwd (always absolute)
    if len(sys.argv) > 1:
        project_dir = os.path.abspath(sys.argv[1])
    else:
        project_dir = os.getcwd()

    # Get model choice

    endpoint = PromptSession()
    endp_resp = (await endpoint.prompt_async("  Endpoint (LiteLLM (l) | Anthropic (a)): ")).strip().lower()

    if endp_resp in ("", "a"):
        endp_resp = "Anthropic"
    else:
        endp_resp = "LiteLLM"
    console.print(f"  Endpoint: {endp_resp}")

    # Set endpoint for browser/md_convert (module-level, avoids threading through class hierarchy)
    from browser._md_convert import set_endpoint
    set_endpoint(endp_resp)

    if not project_dir:
        console.print(Markdown("Error: project directory required"))
        return

    console.print(Markdown(f"→ {project_dir}\n"))

    # Replay stored conversation history
    from utils.msg_store_ import load_msgs
    existing_msgs = load_msgs(project_dir)
    if existing_msgs:
        console.print("  ─── conversation history ───")
        for msg in existing_msgs:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                continue

            if role == "user":
                if isinstance(content, str) and not content.startswith("<system-reminder>"):
                    console.print(Markdown(f"\n› {content.strip()}\n"))
                elif isinstance(content, list):
                    # tool_result blocks — show compact
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            out = str(block.get("content", ""))[:200]
                            console.print(f"  ✓ {out}...")

            elif role == "assistant":
                if isinstance(content, str):
                    console.print(Markdown(f"\n{content}\n"))
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                console.print(Markdown(f"\n{block['text']}\n"))
                            elif block.get("type") == "tool_use":
                                console.print(Markdown(f"  🔧 `{block['name']}`"))

        console.print("  ─── end history (/clear to reset) ───\n")

    # Start file watcher + process tracker
    watcher = FileWatcher(project_dir)
    watcher.start()
    from utils.process_tracker import init as init_process_tracker
    init_process_tracker()
    console.print("  👾 watching for file changes\n")

    # Main loop
    while True:
        try:
            query = (await session.prompt_async("› ")).strip()

            if not query:
                continue

            if query.lower() in ("/exit", "/quit", "exit", "quit"):
                console.print(Markdown("bye"))
                break

            if query.lower() == "/clear":
                from utils.msg_store_ import erase_msgs
                erase_msgs(project_dir)
                console.clear()
                print_banner(console)
                console.print(f"  Endpoint: {endp_resp}")
                console.print(Markdown(f"→ {project_dir}\n"))
                continue

            try:
                await consumeloop(query, project_dir, endp_resp, console, watcher)
            except asyncio.CancelledError:
                console.print("\n[interrupted]")

        except KeyboardInterrupt:
            console.print("\n[interrupted]")
            continue
        except EOFError:
            watcher.stop()
            console.print(Markdown("\nbye"))
            break

    watcher.stop()


if __name__ == "__main__":
    try:
        asyncio.run(start_())
    except KeyboardInterrupt:
        pass  # already handled inside
