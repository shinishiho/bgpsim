from textual import on
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

    def watch_sulking(self, sulking: bool) -> None:
        self.placeholder = self.SULK_PLACEHOLDER if sulking else self.DEFAULT_PLACEHOLDER


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
