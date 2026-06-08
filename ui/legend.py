from rich.text import Text

from textual.widget import Widget

from .topology import TopologyView


class Legend(Widget):
    """A key for the topology's link types: the rightmost column of the
    CommandHistory box. It lives inside the history, so it shows and hides
    along with it."""

    def __init__(self) -> None:
        super().__init__(id="legend")

    def render(self) -> Text:
        theme = self.app.current_theme
        pal = TopologyView.INTER_PALETTE
        t = Text(no_wrap=True)

        def row(mark: str, style: str, label: str) -> None:
            t.append(mark, style=style)
            t.append(f" {label}\n")

        # Intra-AS: thin line for direct connection, double line for iBGP
        t.append("intra-AS\n", style="bold")
        row("──", TopologyView.STYLE_CABLE, "cable only")
        row("══", theme.success,            "iBGP, cabled")
        row("⌒",  theme.warning,            "iBGP, multi-hop")

        # Inter-AS: thick line for eBGP, triangle for hidden eBGP,
        # circle for hidden direct connection (cable)
        t.append("\ninter-AS\n", style="bold")
        t.append("▸", style=f"bold {pal[0]}")
        t.append(" eBGP peer\n")
        t.append("○", style=pal[1 % len(pal)])
        t.append(" cable, no peer\n")
        t.append("━", style=f"bold {pal[0]}")
        t.append("━", style=f"bold {pal[1 % len(pal)]}")
        t.append(" eBGP (on click)\n")

        # The connector only draws for the selected router; spell that out.
        t.append("\nclick a router to\nreveal its eBGP", style="italic dim")
        return t
