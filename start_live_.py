import asyncio
import os
import re
import sys
from claude_loop_ import claude_loop
from utils.msg_store_ import erase_msgs, erase_summary
from tools.browser_tool_ import close_browser
from utils.msg_store_ import load_msgs
from utils.process_tracker import init as init_process_tracker
from cache.redis_cache import RedisStateManager
from screens.window_overlay_ import MessageList, BANNER
from utils.file_watcher_ import FileWatcher
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static
from textual.reactive import reactive
from textual.binding import Binding
from textual.widgets import TextArea
from textual.message import Message
from textual.widgets import LoadingIndicator
from textual.widgets import OptionList

class PromptInput(TextArea):
    """Multiline input: Enter submits, Shift+Enter inserts newline."""

    _paste_store = {}
    _paste_id = [0]
    _PASTE_THRESH = 200

    def _on_paste(self, event):
        if len(event.text) > self._PASTE_THRESH:
            PromptInput._paste_id += 1
            pid = PromptInput._paste_id
            self._paste_store[pid] = event.text
            n_lines = event.text.count('\n') + 1
            event.prevent_default()
            self.insert(f"⟪paste:{pid}|{len(event.text)} chars, {n_lines} lines⟫")
            return

    async def _on_key(self, event):
        # DEBUG: log every keypress to file
        # with open("/tmp/textual_keys.log", "a") as f:
        #     f.write(f"key={event.key!r} character={event.character!r}\n")
        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            self.insert("\n")
            return
        if event.key == "enter":
            event.prevent_default()
            self.post_message(self.Submitted(self))
            return
        await super()._on_key(event)

    class Submitted(Message):
        def __init__(self, textarea):
            super().__init__()
            self.value = textarea.text

class MicroApp(App):

    MODEL_OPTIONS = ["opus-4.6", "sonnet-4.6", "haiku-4.5"]

    _current_model = "sonnet-4.6"

    ALLOW_SELECT = True

    # BINDINGS is a list of tuples that maps (or binds)
    # keys to actions in your app. The first value in
    # the tuple is the key; the second value is the
    # name of the action; the final value
    # is a short description.
    BINDINGS = [
        Binding("escape", "cancel_query", "Interrupt"),
    ]

    # How TCSS works

    # Same model as CSS — selectors target Textual's widget DOM tree:

    # ┌───────────┬───────────────────────────┬──────────────────────────┐
    # │ Selector  │          Targets          │         Example          │
    # ├───────────┼───────────────────────────┼──────────────────────────┤
    # │ #id       │ Widget with id="..."      │ #loader, #statusbar      │
    # ├───────────┼───────────────────────────┼──────────────────────────┤
    # │ ClassName │ Widget class name         │ PromptInput, MessageList │
    # ├───────────┼───────────────────────────┼──────────────────────────┤
    # │ .class    │ Widget with classes="..." │ .highlighted             │
    # └───────────┴───────────────────────────┴──────────────────────────┘

    # The styles cascade down the DOM just like CSS. MessageList gets scrollbar styles because Textual widgets with overflow:
    # auto (the default for scrollable containers) automatically render scrollbars — you're just restyling the built-in ones.

    # How to discover what you can style

    # Every Textual widget exposes a set of CSS properties. The full reference:

    # For PromptInput (inherits from TextArea):
    # PromptInput {
    #     border: solid white;          /* what you have now */
    #     border: round green;          /* rounded corners, green */
    #     border: double $accent;       /* double-line border */
    #     border: none;                 /* remove it */
    #     background: $surface;
    #     color: $text;
    #     padding: 1 2;                 /* vertical horizontal */
    #     margin: 0 1;
    #     scrollbar-color: white;
    #     cursor-color: white;          /* TextArea-specific: the blinking cursor */
    #     selection-color: $accent;     /* TextArea-specific: selected text bg */
    # }

    # Border styles available: ascii, blank, dashed, double, heavy, hidden, hkey, inner, none, outer, panel, round, solid, tall,
    # vkey, wide

    # Pseudo-classes work too:
    # PromptInput:focus {
    #     border: solid $accent;        /* highlight when focused */
    # }

    # Color tokens — Textual has built-in theme variables: $primary, $secondary, $accent, $surface, $background, $text,
    # $text-muted, $error, $warning, $success, or use direct colors like red, #ff0000, rgb(255,0,0).

    # Quick way to experiment

    # Textual has a live CSS reload. Run with:

    # textual run --dev start_live_.py

    # Then edit microcc-styles.tcss — changes apply instantly without restarting. Also textual CLI has textual colors to preview
    # the palette and textual borders to preview all border styles.

    CSS_PATH = "microcc-styles.tcss"

    def __init__(self, project_dir, watcher, messages):
        super().__init__()
        self._project_dir = project_dir
        self._watcher = watcher
        self.__existing_msgs = messages
        self._state_mgr = RedisStateManager()
        init_process_tracker()

    messages = reactive([])  # state change → auto re-render
    active = reactive(None)  # current streaming/thinking
    _pending_approval = None  # asyncio.Event when waiting
    _approval_result = None # True/False bool

    # compose() is where we construct a user interface with widgets.
    # The compose() method may return a list of widgets, but
    # it is generally easier to yield them (making this method a generator).
    # In the example code we yield an instance of each of the
    # widget classes we imported, i.e. Header() and Footer().
    def compose(self) -> ComposeResult:
        yield Static(BANNER)  # always at top
        yield MessageList()  # grows as conversation happens
        with Vertical(id="bottom-bar"):
            yield LoadingIndicator(id="loader")
            yield OptionList(*MicroApp.MODEL_OPTIONS, id="model-picker")
            yield PromptInput(id="prompt")
            yield Static("", id="statusbar")

    def action_cancel_query(self):
        # cancel the running worker — same as exclusive=True starting a new one
        self._state_mgr.set_stop_signal(self._project_dir)
        self.workers.cancel_all()
        self.messages.append({"type": "error", "content": "⊘ interrupted"})
        self.query_one(MessageList).refresh()

    async def on_mount(self):
        # Start background services
        self._watcher.start()
        self._state_mgr.start_cleanup_task()

        # Status bar
        self.query_one("#statusbar", Static).update(f"📂 {self._project_dir} | ⚙️  Model: {self._current_model}")

        # Load history
        for msg in self.__existing_msgs:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                continue

            if role == "user":
                if isinstance(content, str) and not content.startswith(
                    "<system-reminder>"
                ):
                    self.messages.append({"type": "user", "content": content.strip()})
                elif isinstance(content, list):
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                        ):
                            out = str(block.get("content", ""))[:200]
                            self.messages.append(
                                {"type": "tool_call", "name": "tool", "result": out.strip()}
                            )

            elif role == "assistant":
                if isinstance(content, str):
                    self.messages.append({"type": "text", "content": content})
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                self.messages.append(
                                    {"type": "text", "content": block["text"]}
                                )
                            elif block.get("type") == "tool_use":
                                self.messages.append(
                                    {"type": "tool_call", "name": block["name"], "result": "✓"}
                                )

        self.query_one(MessageList).refresh()

    # PromptInput posts a custom message at line 49:
    # self.post_message(self.Submitted(self))
    # Textual's message dispatch rule:
    # 1. Takes the widget class name → PromptInput → snake_case → prompt_input
    # 2. Takes the message class name → Submitted → snake_case → submitted
    # 3. Looks for on_{widget}_{message} → on_prompt_input_submitted
    # The message bubbles up the DOM (widget → parent → screen → app) until something handles it. Since MicroApp defines
    # on_prompt_input_submitted, it catches it at the app level.
    # So three dispatch mechanisms:
    # ┌────────────────────┬──────────────────────────────┬─────────────────────────────┐
    # │     Mechanism      │          Convention          │           Example           │
    # ├────────────────────┼──────────────────────────────┼─────────────────────────────┤
    # │ Key events         │ _on_key() override           │ PromptInput._on_key()       │
    # ├────────────────────┼──────────────────────────────┼─────────────────────────────┤
    # │ Bindings → Actions │ action_{name} method         │ action_cancel_query()       │
    # ├────────────────────┼──────────────────────────────┼─────────────────────────────┤
    # │ Messages           │ on_{widget}_{message} method │ on_prompt_input_submitted() │
    # └────────────────────┴──────────────────────────────┴─────────────────────────────┘

    async def on_prompt_input_submitted(self, event):
        # replaces your prompt_toolkit loop
        query = event.value.strip()
        query = re.sub(
            r'⟪paste:(\d+)\|\d+ chars, \d+ lines⟫',
            lambda m: PromptInput._paste_store.get(int(m.group(1)), m.group(0)),
            query,
        )
        PromptInput._paste_store.clear()
        PromptInput._paste_id = 0
        if self._pending_approval is not None and not self._pending_approval.is_set():
            self._approval_result = query.lower() in ("", "y", "yes")
            self._pending_approval.set()
            self.query_one(PromptInput).clear()
            return
        if not query:
            return
        # Slash commands
        if query.lower() in ("/exit", "/quit"):
            self.exit()  # Textual's built-in — closes app, returns from app.run()
            return

        if query.lower() == "/clear":            
            erase_msgs(self._project_dir)
            erase_summary(self._project_dir)
            import shutil
            await close_browser()
            for ss_folder in (".browser_screenshots", ".computer_screenshots"):
                ss_dir = os.path.join(self._project_dir, ss_folder)
                if os.path.isdir(ss_dir):
                    shutil.rmtree(ss_dir)
            self.messages = []
            self.query_one(MessageList).refresh()
            self.query_one(PromptInput).clear()
            return

        if query.lower() == "/model":
            picker = self.query_one("#model-picker")
            picker.display = True
            picker.focus()
            self.query_one(PromptInput).clear()
            return

        # Normal query
        self.messages.append({"type": "user", "content": query})
        self.query_one(PromptInput).clear()
        self.run_worker(self.do_query(query), exclusive=True)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self._current_model = str(event.option.prompt)
        self.query_one("#model-picker").display = False
        self.query_one("#statusbar", Static).update(
            f"📂 {self._project_dir}  ⟨{self._current_model}⟩"
        )
        self.query_one(PromptInput).focus()
        # Status bar
        self.query_one("#statusbar", Static).update(
            f"📂 {self._project_dir} | ⚙️  Model: {self._current_model}"
        )

    async def do_query(self, query):
        self._state_mgr.clear_stop_signal(self._project_dir)
        self.query_one("#loader").display = True

        try:
            async for event in claude_loop(
                query=query,
                project_dir=self._project_dir,
                watcher=self._watcher,
                model=self._current_model,
            ):
                etype = event.get("type")

                if etype in ("text_delta", "thinking_delta", "tool_call", "tool_result"):
                    self.query_one("#loader").display = False

                if etype == "text_delta":
                    if not self.messages or self.messages[-1]["type"] != "text":
                        self.messages.append({"type": "text", "content": ""})
                    self.messages[-1]["content"] += event.get("content", "")
                    self.query_one(MessageList).refresh()

                elif etype == "thinking_delta":
                    if not self.messages or self.messages[-1]["type"] != "thinking":
                        self.messages.append({"type": "thinking", "content": ""})
                    self.messages[-1]["content"] += event.get("content", "")
                    self.query_one(MessageList).refresh()

                elif etype == "tool_call":
                    self.messages.append({
                        "type": "tool_call",
                        "name": event.get("name", "?"),
                        "result": None,
                    })
                    self.query_one(MessageList).refresh()

                elif etype == "tool_result":
                    for msg in reversed(self.messages):
                        if msg["type"] == "tool_call" and msg["result"] is None:
                            msg["result"] = event.get("output", "")[:60]
                            break
                    self.query_one(MessageList).refresh()
                    # Show loader again — model goes back to API
                    self.query_one("#loader").display = True

                elif etype == "approval_request":
                    approval = event.get("approval")
                    name = event.get("name", "")
                    inp = event.get("input", {})

                    self.messages.append({
                        "type": "approval",
                        "name": name,
                        "input": inp,
                    })
                    self.query_one(MessageList).refresh()

                    self._pending_approval = asyncio.Event()
                    self._approval_result = None
                    await self._pending_approval.wait()

                    approval["approved"] = self._approval_result
                    # Remove the approval prompt — replace with result
                    for i, msg in enumerate(self.messages):
                        if msg.get("type") == "approval" and msg.get("name") == name:
                            if self._approval_result:
                                self.messages[i] = {"type": "tool_call", "name": name, "result": None}
                            else:
                                self.messages[i] = {"type": "error", "content": f"⊘ {name} cancelled"}
                            break
                    self._pending_approval = None
                    self.query_one(MessageList).refresh()

                elif etype == "final_text":
                    pass

                elif etype == "error":
                    self.messages.append(
                        {"type": "error", "content": event.get("message", "Unknown error")}
                    )
                    self.query_one(MessageList).refresh()

                elif etype == "done":
                    self.query_one("#loader").display = False

        except asyncio.CancelledError:
            self.query_one("#loader").display = False
            self.messages.append({"type": "error", "content": "⊘ interrupted"})
            self.query_one(MessageList).refresh()

    def on_unmount(self):
        self._watcher.stop()
        self._state_mgr.stop_cleanup_task()


def start_():
    if len(sys.argv) > 1:
        project_dir = os.path.abspath(sys.argv[1])
    else:
        project_dir = os.getcwd()

    existing_msgs = load_msgs(project_dir)
    watcher = FileWatcher(project_dir)

    app = MicroApp(project_dir, watcher=watcher, messages=existing_msgs)
    app.run(mouse=False)


if __name__ == "__main__":
    start_()
