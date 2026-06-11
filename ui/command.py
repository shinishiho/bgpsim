from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
)

from .legend import Legend


class CommandBar(Input):
    """Placed at the bottom, for user to type commands."""

    DEFAULT_PLACEHOLDER = "Lost? Type \"help\" or \"?\" to display the list of commands."
    SULK_PLACEHOLDER = "Lost? Good luck."

    # Flipped on by the app while `no help` is mid-sulk; the watcher swaps the
    # placeholder text to match.
    sulking: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__(
            placeholder=self.DEFAULT_PLACEHOLDER,
            id="command"
        )
        # Shell-style command history. `_index` points at the entry currently
        # surfaced in the bar, or is None when the user is at the live (draft)
        # line below the newest entry. `_draft` stashes whatever was being typed
        # before they started walking back through history.
        self._history: list[str] = []
        self._index: int | None = None
        self._draft: str = ""

    def watch_sulking(self, sulking: bool) -> None:
        self.placeholder = self.SULK_PLACEHOLDER if sulking else self.DEFAULT_PLACEHOLDER

    def record(self, line: str) -> None:
        """Append a submitted command, skipping immediate duplicates, and snap
        the cursor back to the live line."""
        if line and (not self._history or self._history[-1] != line):
            self._history.append(line)
        self._index = None
        self._draft = ""

    def on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self._recall_older()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self._recall_newer()

    def _recall_older(self) -> None:
        """Up arrow: step toward older commands."""
        if not self._history:
            return
        if self._index is None:
            self._draft = self.value
            self._index = len(self._history) - 1
        elif self._index > 0:
            self._index -= 1
        self._fill(self._history[self._index])

    def _recall_newer(self) -> None:
        """Down arrow: step toward newer commands, then back to the draft."""
        if self._index is None:
            return
        if self._index < len(self._history) - 1:
            self._index += 1
            self._fill(self._history[self._index])
        else:
            self._index = None
            self._fill(self._draft)

    def _fill(self, text: str) -> None:
        self.value = text
        self.cursor_position = len(text)


class CommandHistory(Horizontal):
    """Command history section, placed above the command bar.

    Triple column: a list of command history (navigateable), the detailed view
    of the history item in focus, and a legend for connection types in Topology view.
    """

    def __init__(self) -> None:
        super().__init__(id="main")
        self.border_title = "Command History"
        self._cmd_count = 0
        self._details: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield ListView(id="cmd_list")
        yield VerticalScroll(Markdown(id="cmd_detail"), id="cmd_detail_scroll")
        yield Legend()

    async def add_command(self, line: str, detail: str) -> None:
        """Record a command: list it, stash its detail, highlight it."""
        self._cmd_count += 1
        item_id = f"cmd_{self._cmd_count}"
        self._details[item_id] = detail

        listview = self.query_one("#cmd_list", ListView)
        listview.index = None
        await listview.insert(0, [ListItem(Label(f"{self._cmd_count}  {line}"), id=item_id)])
        listview.index = 0

    @on(ListView.Highlighted, "#cmd_list")
    async def _show_highlighted(self, event: ListView.Highlighted) -> None:
        """Swap the detail pane to the freshly highlighted command."""
        if event.item is None:
            return
        detail = self._details.get(event.item.id or "", "")
        await self.query_one("#cmd_detail", Markdown).update(detail)

    async def clear_commands(self) -> None:
        """Remove every command and reset the counter."""
        await self.query_one("#cmd_list", ListView).clear()
        self._details.clear()
        self._cmd_count = 0
        await self.query_one("#cmd_detail", Markdown).update("")
