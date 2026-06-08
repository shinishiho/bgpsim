from textual.app import ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Static


class TooSmallOverlay(Container):
    """Eww, why is it so small?"""
    show_overlay: reactive[bool] = reactive(False)
    win_width: reactive[int] = reactive(0)
    win_height: reactive[int] = reactive(0)
    need_width: reactive[int] = reactive(0)
    need_height: reactive[int] = reactive(0)
    open_panels: reactive[int] = reactive(0)
    message: reactive[str] = reactive("")

    def __init__(self) -> None:
        super().__init__(id="too_small_overlay")

    def compose(self) -> ComposeResult:
        yield Static(self.message, id="too_small_dialog")

    def watch_show_overlay(self, show: bool) -> None:
        self.set_class(show, "-show")

    def compute_message(self) -> str:
        hint = (
            "Close a side panel (press t or i) or resize the window."
            if self.open_panels
            else "Please resize the window."
        )
        return (
            "Window too small\n\n"
                f"Current size: {self.win_width} x {self.win_height}\n"
                f"Need at least: {self.need_width} x {self.need_height}\n\n"
                f"{hint}"
        )

    def watch_message(self, msg: str) -> None:
        if self.is_mounted:
            self.query_one("#too_small_dialog", Static).update(msg)
