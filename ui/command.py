from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Collapsible,
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
    NL_PLACEHOLDER = "Ask in plain English, or prefix > for a literal command (e.g. \"> help\")."
    SULK_PLACEHOLDER = "Lost? Good luck."

    # Flipped on by the app while `no help` is mid-sulk, and set once at mount
    # when the natural-language router is available; the watchers swap the
    # placeholder text to match.
    sulking: reactive[bool] = reactive(False)
    nl_enabled: reactive[bool] = reactive(False)

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

    def _refresh_placeholder(self) -> None:
        if self.sulking:
            self.placeholder = self.SULK_PLACEHOLDER
        elif self.nl_enabled:
            self.placeholder = self.NL_PLACEHOLDER
        else:
            self.placeholder = self.DEFAULT_PLACEHOLDER

    def watch_sulking(self, sulking: bool) -> None:
        self._refresh_placeholder()

    def watch_nl_enabled(self, nl_enabled: bool) -> None:
        self._refresh_placeholder()

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
        # item_id -> (markdown detail, optional raw debug payload). The debug
        # text, when present, is tucked behind a "Debug info" Collapsible so the
        # friendly message stays front-and-centre.
        self._details: dict[str, tuple[str, str | None]] = {}

    def compose(self) -> ComposeResult:
        yield ListView(id="cmd_list")
        yield VerticalScroll(id="cmd_detail_scroll")
        yield Legend()

    async def _render_detail(self, detail: str, debug: str | None) -> None:
        """Repaint the detail pane: the markdown body, then an optional
        collapsed "Debug info" panel holding the raw router output."""
        scroll = self.query_one("#cmd_detail_scroll", VerticalScroll)
        await scroll.remove_children()
        widgets: list[Widget] = [Markdown(detail)]
        if debug:
            widgets.append(
                Collapsible(
                    Markdown(f"```\n{debug}\n```"),
                    title="Debug info",
                    collapsed=True,
                )
            )
        await scroll.mount(*widgets)

    async def add_command(self, line: str, detail: str, debug: str | None = None) -> None:
        """Record a command: list it, stash its detail, highlight it."""
        self._cmd_count += 1
        item_id = f"cmd_{self._cmd_count}"
        self._details[item_id] = (detail, debug)

        listview = self.query_one("#cmd_list", ListView)
        listview.index = None
        await listview.insert(0, [ListItem(Label(f"{self._cmd_count}  {line}"), id=item_id)])
        listview.index = 0

    @on(ListView.Highlighted, "#cmd_list")
    async def _show_highlighted(self, event: ListView.Highlighted) -> None:
        """Swap the detail pane to the freshly highlighted command."""
        if event.item is None:
            return
        detail, debug = self._details.get(event.item.id or "", ("", None))
        await self._render_detail(detail, debug)

    async def clear_commands(self) -> None:
        """Remove every command and reset the counter."""
        await self.query_one("#cmd_list", ListView).clear()
        self._details.clear()
        self._cmd_count = 0
        await self._render_detail("", None)
