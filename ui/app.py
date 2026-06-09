from textual import on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import (
    Container,
    Horizontal,
    ScrollableContainer,
    Vertical,
)
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
)

from models.world import World
from models.router import Router
from commands import apply_command, CommandResult, _help_egg

from .command import CommandBar, CommandHistory
from .timeline import TimelinePanel
from .inspector import InspectorPanel
from .overlay import TooSmallOverlay
from .topology import TopologyView


def _table_cell(text: str) -> str:
    """Escape a markdown string so a stray pipe can't break the table."""
    return text.replace("|", "\\|")


def _cisco_cell(detail: str) -> str:
    """Collapse a multi-line Cisco snippet into one inline-code table cell."""
    if not detail:
        return ""
    oneline = " ; ".join(p.strip() for p in detail.splitlines() if p.strip())
    return f"`{_table_cell(oneline)}`"


class BGPSimApp(App):
    """BGP Simulator app in Textual. Textual is cool!"""

    TITLE = "ChatBGP"
    SUB_TITLE = "The BGP Simulator you didn't ask for"

    CSS_PATH = "style.tcss"
    BINDINGS = [
        Binding("t", "timeline_toggle", "Toggle Timeline panel"),
        Binding("i", "inspector_toggle", "Toggle Inspector panel"),
        Binding("h", "history_toggle", "Toggle Command history"),
        Binding("q", "quit", "Quit"),
    ]

    PANEL_FULL_WIDTH = 41   # full side-panel footprint: width 40 + 1 border
    PANEL_NARROW_WIDTH = 25 # narrow side-panel footprint: width 24 + 1 border
    MIN_CENTER_WIDTH = 80   # narrow the panels once the center drops below this
    MIN_CENTER_FLOOR = 60   # below this center width, even shrunk, show popup
    SHORT_HEIGHT = 24       # threshold to use small command bar
    MIN_HEIGHT = 36         # ew, smol (and TH)

    world: World

    async def on_mount(self) -> None:
        self.theme = "rose-pine"
        self.world = World()
        self._apply_responsive(self.size.width, self.size.height)
        await self._refresh_world_views()

    def _router_by_name(self, name: str | None) -> Router | None:
        """Resolve a router name to its live Router, or None."""
        if name is None:
            return None
        return next(
            (r for r in self.world.routers.routers if r.name == name), None
        )

    async def _refresh_world_views(self) -> None:
        """Repaint every world-backed surface from the live world state."""
        self.query_one(TopologyView).sync(self.world)
        await self.query_one(TimelinePanel).sync(self.world)
        selected = self.query_one(TopologyView).selected
        self.query_one(InspectorPanel).show(self._router_by_name(selected))

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()

        with Horizontal(id="body"):
            yield TimelinePanel()
            with Vertical(id="center"):
                topology_scroll = ScrollableContainer(
                    TopologyView(), id="topology_scroll"
                )
                topology_scroll.border_title = "Topology"
                yield topology_scroll
                yield CommandHistory()
            yield InspectorPanel()

        with Container(id="bottom_bar"):
            with Horizontal(id="command_row"):
                yield Button("🧹", id="clear_history")
                yield CommandBar()
            yield Footer()

        yield TooSmallOverlay()

    @on(Input.Submitted)
    async def append_msg(self, event: Input.Submitted) -> None:
        """Parse the typed command, apply it to the world, echo + refresh."""
        line = event.value.strip()
        event.input.clear()
        if not line:
            return

        result = apply_command(self.world, line)
        # Jump the playhead to the events this command just recorded, so the
        # Timeline shows them. Only when it produced events, so a failed or
        # no-op command doesn't yank the cursor away from where the user is.
        if result.events:
            self.world.clock.to_last()
        await self.query_one(CommandHistory).add_command(
            line, self._format_result(line, result)
        )
        self.query_one(CommandBar).sulking = _help_egg.is_sulking()
        await self._refresh_world_views()

    @staticmethod
    def _format_result(line: str, result: CommandResult) -> str:
        """Markdown for one history entry: the command echo, then a table of the
        events it produced (category · narration · Cisco) so the wide-but-short
        pane reads across columns instead of stacking tall code blocks."""
        md = [f"`{line}`", ""]
        if not result.ok:
            md.append(f"❌ {result.error}")
            return "\n".join(md)
        if result.note:
            md.append(result.note)
            md.append("")
        if result.events:
            md.append("| Tag | Event | Cisco |")
            md.append("| --- | --- | --- |")
            for event in result.events:
                md.append(
                    f"| {event.category.upper()} "
                    f"| {_table_cell(event.summary)} "
                    f"| {_cisco_cell(event.detail)} |"
                )
        elif not result.note:
            md.append("*(no change)*")
        return "\n".join(md)

    @on(TopologyView.RouterSelected)
    def _on_router_selected(self, message: TopologyView.RouterSelected) -> None:
        """Reveal + populate the Inspector for the clicked router."""
        inspector = self.query_one(InspectorPanel)
        if message.name is not None and inspector.has_class("-hidden"):
            inspector.remove_class("-hidden")
            self._apply_responsive(self.size.width, self.size.height)
        inspector.show(self._router_by_name(message.name))

    @on(Button.Pressed, "#playhead_next")
    async def _playhead_next(self, _: Button.Pressed) -> None:
        """Scrub forward through recorded events; at the head, step the sim."""
        clock = self.world.clock
        if clock.can_advance:
            clock.advance()
        else:
            self.world.step()
            clock.to_last()
        await self._refresh_world_views()

    @on(Button.Pressed, "#playhead_last")
    async def _playhead_last(self, _: Button.Pressed) -> None:
        """Converge the sim and jump the cursor to the newest event."""
        result = self.world.converge()
        if not result.converged:
            self.notify(
                f"Stopped after {result.ticks} ticks without converging "
                "(possible route oscillation).",
                title="Did not converge",
                severity="warning",
            )
        self.world.clock.to_last()
        await self._refresh_world_views()

    @on(Button.Pressed, "#playhead_prev")
    async def _playhead_prev(self, _: Button.Pressed) -> None:
        """Rewind the read cursor over the event log (topology stays live)."""
        self.world.clock.rewind()
        await self._refresh_world_views()

    @on(Button.Pressed, "#playhead_first")
    async def _playhead_first(self, _: Button.Pressed) -> None:
        """Jump the read cursor to the earliest recorded tick."""
        self.world.clock.to_first()
        await self._refresh_world_views()

    @on(Button.Pressed, "#clear_history")
    async def clear_history(self, _: Button.Pressed) -> None:
        """Wipe the command history when the broom button is pressed."""
        await self.query_one(CommandHistory).clear_commands()

    def action_timeline_toggle(self) -> None:
        """Toggle the visibility of the Timeline panel"""
        self.query_one(TimelinePanel).toggle_class("-hidden")
        self._apply_responsive(self.size.width, self.size.height)

    def action_inspector_toggle(self) -> None:
        """Toggle the visibility of the Inspector panel"""
        self.query_one(InspectorPanel).toggle_class("-hidden")
        self._apply_responsive(self.size.width, self.size.height)

    def action_history_toggle(self) -> None:
        """Toggle the Command history (and its Legend), handing the space to the
        topology view."""
        self.query_one(CommandHistory).toggle_class("-hidden")

    def on_resize(self, event: events.Resize) -> None:
        """Re-evaluate the responsive layout whenever the terminal resizes."""
        self._apply_responsive(event.size.width, event.size.height)

    def _apply_responsive(self, width: int, height: int) -> None:
        """Toggle layout classes and too small warning popup.

        There are three modes: normal mode with everything spacious and comfy;
        `narrow` mode where side panels are shrunk, apply when the main panel's
        width is too narrow; `short` when the window is too short, then the
        CommandBar collapses to a single line.

        A pop up will appear when the real-estate crosses a certain threshold,
        forcing the user to resize the window, or close a sidebar (if that helps).
        """
        open_panels = (
            (not self.query_one(TimelinePanel).has_class("-hidden"))
            + (not self.query_one(InspectorPanel).has_class("-hidden"))
        )
        projected_center = width - open_panels * self.PANEL_FULL_WIDTH
        narrow = projected_center < self.MIN_CENTER_WIDTH
        self.screen.set_class(narrow, "-narrow")
        self.screen.set_class(height < self.SHORT_HEIGHT, "-short")

        # Center width given the panel footprint we just settled on.
        panel_width = self.PANEL_NARROW_WIDTH if narrow else self.PANEL_FULL_WIDTH
        center = width - open_panels * panel_width

        too_small = center < self.MIN_CENTER_FLOOR or height < self.MIN_HEIGHT
        self.screen.set_class(too_small, "-too-small")

        overlay = self.query_one(TooSmallOverlay)
        overlay.win_width = width
        overlay.win_height = height
        overlay.need_width = open_panels * self.PANEL_NARROW_WIDTH + self.MIN_CENTER_FLOOR
        overlay.need_height = self.MIN_HEIGHT
        overlay.open_panels = open_panels
        overlay.show_overlay = too_small
