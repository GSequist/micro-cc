import asyncio
import sys
from claude_loop_ import claude_loop
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.completion import PathCompleter
from rich.console import Console
from rich.markdown import Markdown
from utils.file_watcher_ import FileWatcher


async def consumeloop(query, project_dir, console, watcher: FileWatcher):
    async for event in claude_loop(query=query, project_dir=project_dir, watcher=watcher):
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

        elif etype == "final_text":
            # Final response - print full
            console.print(Markdown(f"\n{event.get('content', '')}\n"))

        elif etype == "error":
            console.print(Markdown(f"\n⚠️  {event.get('message', 'Unknown error')}\n"))

        elif etype == "done":
            pass  # Ready for next input




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

    console.print("\n╭─ micro-cc ─────────────────────────────╮")
    console.print("│                                        │")
    console.print("│  Option→Enter  submit /clear  reset    │")
    console.print("│  Ctrl+C     interrupt /exit   quit     │")
    console.print("│                                        │")
    console.print("╰────────────────────────────────────────╯\n")

    # Get project dir from arg or prompt
    if len(sys.argv) > 1:
        project_dir = sys.argv[1]
    else:
        dir_session = PromptSession(completer=PathCompleter(only_directories=True, expanduser=True))
        project_dir = (await dir_session.prompt_async("project directory: ")).strip()

    if not project_dir:
        console.print(Markdown("Error: project directory required"))
        return

    console.print(Markdown(f"→ {project_dir}\n"))

    # Start file watcher
    watcher = FileWatcher(project_dir)
    watcher.start()
    console.print("  👁 watching for file changes\n")

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
                console.print(Markdown("conversation cleared\n"))
                continue

            try:
                await consumeloop(query, project_dir, console, watcher)
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
