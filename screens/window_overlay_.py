from textual.app import RenderableType
from textual.widget import Widget
from rich.markdown import Markdown
from rich.text import Text
from rich.console import Group

BANNER = """
[bold white]███╗   ███╗ ██████╗ ██████╗ ███████╗██╗[/]
[bold white]████╗ ████║██╔═══██╗██╔══██╗██╔════╝██║[/]
[bold white]██╔████╔██║██║   ██║██║  ██║█████╗  ██║[/]
[dim]██║╚██╔╝██║██║   ██║██║  ██║██╔══╝  ██║[/]
[dim]██║ ╚═╝ ██║╚██████╔╝██████╔╝███████╗███████╗[/]
[dim]╚═╝     ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝╚══════╝[/]
[dim]───────[/] [bold]on the metal[/] [dim]─────────────────────[/]
[dim]Knowledge work is by extension a coding problem.[/]

[dim]enter  submit[/]
[dim]esc  interrupt[/]
[dim]/exit   quit microcc[/]
[dim]/clear  erase messages[/]
[dim]/model  choose model[/]
"""

class MessageList(Widget):
    """Reads app.messages, renders everything"""

    def render(self) -> RenderableType:
        """Called by Textual whenever refresh() is triggered"""
        parts = []
        for msg in self.app.messages:
            if msg["type"] == "user":
                parts.append(Text(f"› {msg['content']}", style="bold"))
            elif msg["type"] == "text":
                parts.append(Markdown(msg["content"]))
            elif msg["type"] == "thinking":
                parts.append(Text(f"💭 {msg['content']}", style="dim italic"))
            elif msg["type"] == "tool_call":
                if msg["result"] is None:
                    parts.append(Text(f"🔧 {msg['name']} ⋯", style="yellow"))
                else:
                    parts.append(Text(f"✓ {msg['name']}", style="green"))
                    parts.append(Text(f"  {msg['result']}", style="dim"))
            elif msg["type"] == "error":
                parts.append(Text(f"⚠️ {msg['content']}", style="red"))
            elif msg["type"] == "approval":
                parts.append(Text(f"⚠️ Approve? {msg['name']}", style="bold yellow"))
                parts.append(Text(f"  {msg['input']}", style="dim"))
        return Group(*parts) if parts else Text("")
