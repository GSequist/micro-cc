import asyncio
import os
import re
import signal
import sys
from claude_loop_ import claude_loop
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.completion import WordCompleter
from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from utils.file_watcher_ import FileWatcher


async def consumeloop(query, project_dir, end_resp, console, watcher: FileWatcher):
    _stream_text = ""
    _live = None
    _thinking_started = False

    try:
        async for event in claude_loop(
            query=query, project_dir=project_dir, end_resp=end_resp, watcher=watcher
        ):
            etype = event.get("type")

            # Stop live display when transitioning away from text deltas
            if _live and etype != "text_delta":
                _live.stop()
                _live = None
                _stream_text = ""

            # End thinking line when transitioning away
            if _thinking_started and etype != "thinking_delta":
                print()
                _thinking_started = False

            if etype == "status":
                console.print(f"  ⋯ {event.get('message', '')}")

            elif etype == "approval_request":
                name = event.get("name", "")
                inp = event.get("input", {})
                approval = event.get("approval", None)

                console.print(f"\n ⚠️  {name}: {inp}")

                approval_session = PromptSession()
                response = (
                    (await approval_session.prompt_async("  Execute? [Y/n]: "))
                    .strip()
                    .lower()
                )

                if response in ("", "yes", "y"):
                    approval["approved"] = True
                else:
                    approval["approved"] = False
                    console.print("  ⊘ cancelled")

            elif etype == "cancelled":
                console.print("  ⊘ cancelled · conversation saved")

            elif etype == "thinking_delta":
                if not _thinking_started:
                    print("  💭 ", end="", flush=True)
                    _thinking_started = True
                print(event.get("content", ""), end="", flush=True)

            elif etype == "text_delta":
                _stream_text += event.get("content", "")
                if _live is None:
                    _live = Live(
                        Markdown(_stream_text), console=console, refresh_per_second=10
                    )
                    _live.start()
                else:
                    _live.update(Markdown(_stream_text))

            elif etype == "tool_call":
                name = event.get("name", "?")
                console.print(Markdown(f"  🔧 `{name}`"))

            elif etype == "tool_result":
                name = event.get("name", "?")
                output = event.get("output", "")
                # Dangerous/mutating tools — show full output with markdown
                if name in ("bash_tool", "write_", "edit_"):
                    console.print(f"  ✓ [bold]{name}[/bold]")
                    if output.strip():
                        console.print(Markdown(f"```\n{output[:3000]}\n```"))
                    console.print("  ─────────────────────────────────────")
                else:
                    # Read-only tools — compact one-liner
                    short = output.replace("\n", " ").strip()[:120]
                    console.print(f"  ✓ [dim]{name}[/dim] {short}")

            elif etype == "final_text":
                pass  # signal only — text already rendered via deltas

            elif etype == "error":
                console.print(Markdown(f"\n⚠️  {event.get('message', 'Unknown error')}\n"))

            elif etype == "done":
                pass
    finally:
        # Always clean up Live display — left active it hijacks the terminal
        if _live:
            try:
                _live.stop()
            except Exception:
                pass


def print_banner(console):
    console.print("""
  [bold white]███╗   ███╗██╗ ██████╗██████╗  ██████╗[/]
  [bold white]████╗ ████║██║██╔════╝██╔══██╗██╔═══██╗[/]
  [bold white]██╔████╔██║██║██║     ██████╔╝██║   ██║[/]
  [dim]██║╚██╔╝██║██║██║     ██╔══██╗██║   ██║[/]
  [dim]██║ ╚═╝ ██║██║╚██████╗██║  ██║╚██████╔╝[/]
  [dim]╚═╝     ╚═╝╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝[/]
  [dim]─────────[/] [bold]cc[/] [dim]──────────────────────────[/]

  [dim]⌥↩  submit   /clear  reset   ctrl+c  interrupt[/]
  [dim]↩   newline  /exit   quit    /plan   plan[/]
""")


async def start_():
    """Minimal CLI entry point for micro-cc.

    Usage: python start_.py [project_dir]
    """

    bindings = KeyBindings()

    @bindings.add("enter")  # Enter = newline (natural typing)
    def _(event):
        event.current_buffer.insert_text("\n")

    @bindings.add("escape", "enter")  # Esc then Enter = submit
    def _(event):
        event.current_buffer.validate_and_handle()

    slash_completer = WordCompleter(
        ["/clear", "/exit", "/plan", "/quit"],
        sentence=True
    )

    session = PromptSession(key_bindings=bindings, multiline=True,completer=slash_completer,)


    # Large-paste condensing — keep buffer small, easy to select/delete
    _paste_store = {}
    _paste_id = [0]
    _orig_insert = session.default_buffer.insert_text
    _PASTE_THRESH = 2000
    _PASTE_RE = re.compile(r'⟪paste:(\d+)\|\d+ chars, \d+ lines⟫')

    def _condensed_insert(data, overwrite=False, move_cursor=True, fire_event=True):
        if len(data) > _PASTE_THRESH:
            _paste_id[0] += 1
            pid = _paste_id[0]
            _paste_store[pid] = data
            n_lines = data.count('\n') + 1
            tag = f"⟪paste:{pid}|{len(data)} chars, {n_lines} lines⟫"
            return _orig_insert(tag, overwrite, move_cursor, fire_event)
        return _orig_insert(data, overwrite, move_cursor, fire_event)

    session.default_buffer.insert_text = _condensed_insert

    def _expand_pastes(text):
        def _replacer(m):
            pid = int(m.group(1))
            return _paste_store.get(pid, m.group(0))
        result = _PASTE_RE.sub(_replacer, text)
        _paste_store.clear()
        _paste_id[0] = 0
        return result

    console = Console()

    print_banner(console)

    # Get project dir from arg or default to cwd (always absolute)
    if len(sys.argv) > 1:
        project_dir = os.path.abspath(sys.argv[1])
    else:
        project_dir = os.getcwd()

    # Get model choice

    endpoint = PromptSession()
    endp_resp = (
        (await endpoint.prompt_async("  Endpoint (LiteLLM (l) | Anthropic (a)): "))
        .strip()
        .lower()
    )

    if endp_resp in ("", "a"):
        endp_resp = "Anthropic"
    else:
        endp_resp = "LiteLLM"
    console.print(f"  ⚡ {endp_resp}")

    # Set endpoint for browser/md_convert (module-level, avoids threading through class hierarchy)
    from browser._md_convert import set_endpoint

    set_endpoint(endp_resp)

    if not project_dir:
        console.print("  ✗ project directory required")
        return

    console.print(f"  📂 {project_dir}\n")

    # Replay stored conversation history
    from utils.msg_store_ import load_msgs

    existing_msgs = load_msgs(project_dir)
    if existing_msgs:
        console.print("  ╭─── conversation history ───")
        for msg in existing_msgs:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                continue

            if role == "user":
                if isinstance(content, str) and not content.startswith(
                    "<system-reminder>"
                ):
                    console.print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    console.print(Markdown(f"\n› {content.strip()}\n"))
                    console.print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                elif isinstance(content, list):
                    # tool_result blocks — show compact
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                        ):
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

        console.print("  ╰─── end history · /clear to reset ───\n")

    # Start file watcher + process tracker + cache cleanup
    watcher = FileWatcher(project_dir)
    watcher.start()
    from utils.process_tracker import init as init_process_tracker
    from cache.redis_cache import RedisStateManager

    init_process_tracker()
    _state_mgr = RedisStateManager()
    _state_mgr.start_cleanup_task()
    console.print("  👾 watching for file changes\n")

    # Main loop
    while True:
        try:
            query = (await session.prompt_async("› ")).strip()
            query = _expand_pastes(query)

            if not query:
                continue

            if query.lower() in ("/exit", "/quit", "exit", "quit"):
                console.print("\n  👋 bye\n")
                break

            if query.lower() == "/clear":
                from utils.msg_store_ import erase_msgs, erase_summary
                from tools.browser_tool_ import close_browser
                import shutil

                erase_msgs(project_dir)
                erase_summary(project_dir)
                await close_browser()
                ss_dir = os.path.join(project_dir, ".browser_screenshots")
                if os.path.isdir(ss_dir):
                    shutil.rmtree(ss_dir)
                _paste_store.clear()
                _paste_id[0] = 0
                console.clear()
                print_banner(console)
                console.print(f"  ⚡ {endp_resp}")
                console.print(f"  📂 {project_dir}\n")
                continue

            console.print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            # Run as a task so SIGINT can cleanly cancel it
            task = asyncio.create_task(
                consumeloop(query, project_dir, endp_resp, console, watcher)
            )

            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, task.cancel)

            try:
                await task
            except asyncio.CancelledError:
                console.print("\n  ⊘ interrupted")
            finally:
                loop.remove_signal_handler(signal.SIGINT)

        except KeyboardInterrupt:
            # Ctrl+C while at the prompt — just redraw
            continue
        except EOFError:
            watcher.stop()
            _state_mgr.stop_cleanup_task()
            console.print("\n  👋 bye\n")
            break

    watcher.stop()
    _state_mgr.stop_cleanup_task()


if __name__ == "__main__":
    try:
        asyncio.run(start_())
    except KeyboardInterrupt:
        pass  # already handled inside
