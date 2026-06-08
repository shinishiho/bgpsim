from textual.app import ComposeResult
from textual.containers import (
    HorizontalGroup,
    Vertical,
    VerticalScroll,
)
from textual.widget import Widget
from textual.widgets import (
    Button,
    Digits,
    Collapsible,
    Label,
    Markdown,
    Static,
)

from models.world import World


class TimelinePanel(Vertical):
    """Timeline panel, docked to the left of the screen.

    It contains next and previous button to advance, rewind the
    playback cursor on the world, and a VerticalScroll displaying
    updates occured in that one tick, each is a Collapsible.

    Between next and previous button is the tick number, the
    current tick that the playback cursor is sitting on.
    """

    def __init__(self) -> None:
        super().__init__(id="timeline_panel", classes="-hidden")

    def compose(self) -> ComposeResult:
        yield Static("Timeline")
        yield HorizontalGroup(
            Button("<<", id="playhead_first"),
            Button("<", id="playhead_prev"),

            # Digits when we have real estate, otherwise label
            # Controlled by css class
            Digits("1", id="playhead_tick"),
            Label("1", id="playhead_tick_small"),

            Button(">", id="playhead_next"),
            Button(">>", id="playhead_last"),
            id="playhead_controls",
        )
        yield VerticalScroll(id="timeline", can_focus=False)

    async def sync(self, world: World) -> None:
        """Rebuild timeline events list"""
        clock = world.clock
        container = self.query_one("#timeline", VerticalScroll)
        await container.remove_children()

        events: list[Widget] = []
        for event in clock.current_events:
            widgets: list[Widget] = [Markdown(event.summary)]
            if event.detail:
                widgets.append(Static(event.detail, classes="event_config"))

            events.append(
                Collapsible(
                    *widgets,
                    title=f"\\[{event.category.upper()}] {event.title}",
                    classes="timeline_event",
                )
            )

        if events:
            await container.mount(*events)

        tick = str(clock.cursor_position)
        self.query_one("#playhead_tick", Digits).update(tick)
        self.query_one("#playhead_tick_small", Label).update(tick)

        # Disable backward controls when the playhead is on the earliest tick.
        # Forward controls always have a move: `>` steps the sim, `>>` converges.
        self.query_one("#playhead_first", Button).disabled = not clock.can_rewind
        self.query_one("#playhead_prev", Button).disabled = not clock.can_rewind
