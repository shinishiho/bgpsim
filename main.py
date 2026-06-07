from rich.text import Text

from textual import on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.containers import (
    Container,
    Horizontal,
    HorizontalGroup,
    Vertical,
    VerticalScroll,
)
from textual.widgets import (
    Button,
    Digits,
    Footer,
    Header,
    Input,
    Collapsible,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

class CommandBar(Input):
    """Placed at the bottom, for user to type commands."""

    def __init__(self) -> None:
        super().__init__(placeholder="Type anything!", id="command")


class CommandHistory(TabbedContent):
    """Command history

    It is placed above the CommandBar, each command is stored in a tab,
    new messages create new tabs, and user can cycle back and forth.
    """

    def __init__(self) -> None:
        super().__init__(id="main")
        self.border_title = "Command History"
        self._cmd_count = 0

    async def add_command(self, text: str) -> None:
        """Append a new tab holding the command."""
        self._cmd_count += 1
        pane_id = f"cmd_{self._cmd_count}"

        await self.add_pane(
            TabPane(
                str(self._cmd_count),
                Collapsible(Label(text)),
                id=pane_id,
            )
        )

        self.active = pane_id

    async def clear_commands(self) -> None:
        """Remove every command tab and reset the counter."""
        await self.clear_panes()
        self._cmd_count = 0


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
        yield VerticalScroll(
            Collapsible(
                Static("Content of the update"),
                title="Event 1",
                classes="timeline_event"
            ),
            Collapsible(
                Static("Content of the update"),
                title="Event 1",
                classes="timeline_event"
            ),
            Collapsible(
                Static("Content of the update"),
                title="Event 1",
                classes="timeline_event"
            ),
            id="timeline",
            can_focus=False
        )


class InspectorPanel(Vertical):
    """Inspector panel, docked to the right of the screen.

    It contains a TabbedContent with two tabs: "Routing Table" and
    "BGP Loc RIB", showing the inspected router's state.
    """

    def __init__(self) -> None:
        super().__init__(id="inspector_panel", classes="-hidden")

    def compose(self) -> ComposeResult:
        yield Static("Inspector")
        with TabbedContent(id="inspector_tabs"):
            with TabPane("Routing Table", id="routing_table"):
                yield VerticalScroll(Static("Routing table contents"))
            with TabPane("BGP Loc RIB", id="bgp_loc_rib"):
                yield VerticalScroll(Static("BGP Loc RIB contents"))


class TooSmallOverlay(Container):
    """Eww, why is it so small?"""

    def __init__(self) -> None:
        super().__init__(id="too_small_overlay")

    def compose(self) -> ComposeResult:
        yield Static("Terminal too small", id="too_small_dialog")


class TopologyView(Widget):
    """An ASCII node-graph of the network, docked in the top of the center column.

    Layout is AS-clustered: each AS is a rounded bordered region whose routers
    sit in a row. Intra-AS peerings are drawn inside the region (adjacent peers
    as straight connectors, multi-hop iBGP as arcs over the row); eBGP is drawn
    region-to-region through the gutters. Clicking a router box selects it.

    This is a dummy prototype: the topology is hardcoded so we can judge the
    rendering before wiring it to the real `World`.

    Beautifully sponsored by Claude Opus 4.8.
    """

    # --- Dummy topology -------------------------------------------------------
    # Four ASes: AS1 is a full iBGP mesh; AS2/AS3/AS4 run partial-mesh iBGP
    # (so some sessions are multi-hop, with no direct cable).
    ROUTERS = [
        ("R1", 1), ("R2", 1), ("R3", 1),               # AS1 (full mesh)
        ("R4", 2), ("R5", 2), ("R6", 2),               # AS2
        ("R7", 3), ("R8", 3), ("R9", 3), ("R10", 3),   # AS3
        ("R11", 4), ("R12", 4), ("R13", 4),            # AS4
    ]
    LINKS = [
        ("R1", "R2"), ("R2", "R3"), ("R1", "R3"),      # AS1 meshed cabling
        ("R4", "R5"), ("R5", "R6"),                    # AS2 chain
        ("R7", "R8"), ("R8", "R9"), ("R9", "R10"),     # AS3 chain
        ("R11", "R12"), ("R12", "R13"),                # AS4 chain
        ("R3", "R4"), ("R6", "R7"),                    # eBGP border links
        ("R10", "R11"), ("R1", "R13"),
    ]
    SESSIONS = [
        # AS1: full iBGP mesh (all ride cables -> "peered")
        ("R1", "R2"), ("R1", "R3"), ("R2", "R3"),
        # AS2: full iBGP mesh (R4-R6 is multi-hop -> "session")
        ("R4", "R5"), ("R5", "R6"), ("R4", "R6"),
        # AS3: full iBGP mesh over a chain (several multi-hop)
        ("R7", "R8"), ("R8", "R9"), ("R9", "R10"),
        ("R7", "R9"), ("R7", "R10"), ("R8", "R10"),
        # AS4: full iBGP mesh (R11-R13 multi-hop)
        ("R11", "R12"), ("R12", "R13"), ("R11", "R13"),
        # eBGP sessions ride the border cables -> "peered"
        ("R3", "R4"), ("R6", "R7"), ("R10", "R11"), ("R1", "R13"),
    ]

    # --- Layout geometry ------------------------------------------------------
    BOX_W = 10        # router box width  (border + 8 inner cells)
    BOX_H = 3         # router box height (top, name, bottom)
    ROUTER_GAP = 3    # gap between router boxes inside a region
    PAD = 1           # padding inside a region border
    CLUSTER_COLS = 2  # wrap to a new row of regions after this many ASes
    CLUS_HGUT = 8     # horizontal gutter between regions
    CLUS_VGUT = 4     # vertical gutter between region rows

    # mask of line directions present in a cell -> box-drawing glyph
    _GLYPH = {
        frozenset({"L", "R"}):           "─",
        frozenset({"U", "D"}):           "│",
        frozenset({"D", "R"}):           "┌",
        frozenset({"D", "L"}):           "┐",
        frozenset({"U", "R"}):           "└",
        frozenset({"U", "L"}):           "┘",
        frozenset({"U", "D", "R"}):      "├",
        frozenset({"U", "D", "L"}):      "┤",
        frozenset({"L", "R", "D"}):      "┬",
        frozenset({"L", "R", "U"}):      "┴",
        frozenset({"U", "D", "L", "R"}): "┼",
        frozenset({"L"}):                "─",
        frozenset({"R"}):                "─",
        frozenset({"U"}):                "│",
        frozenset({"D"}):                "│",
    }

    # double-line variant, used for eBGP so inter-AS links stand out from iBGP
    _GLYPH2 = {
        frozenset({"L", "R"}):           "═",
        frozenset({"U", "D"}):           "║",
        frozenset({"D", "R"}):           "╔",
        frozenset({"D", "L"}):           "╗",
        frozenset({"U", "R"}):           "╚",
        frozenset({"U", "L"}):           "╝",
        frozenset({"U", "D", "R"}):      "╠",
        frozenset({"U", "D", "L"}):      "╣",
        frozenset({"L", "R", "D"}):      "╦",
        frozenset({"L", "R", "U"}):      "╩",
        frozenset({"U", "D", "L", "R"}): "╬",
        frozenset({"L"}):                "═",
        frozenset({"R"}):                "═",
        frozenset({"U"}):                "║",
        frozenset({"D"}):                "║",
    }

    STYLE_CABLE = "grey42"
    # paint priority when routes share a cell (higher wins)
    _RANK = {"cable": 1, "peered": 2, "ebgp": 3, "session": 4}
    # per-AS accent colors (cycled), chosen to avoid the cable/peered/session hues
    AS_PALETTE = ["#c4a7e7", "#eb6f92", "#3e8fb0", "#ea9a97", "#a6da95", "#f5c2e7"]

    def __init__(self) -> None:
        super().__init__(id="topology")
        self.border_title = "Topology"
        self.selected: str | None = None
        self._rects: dict[str, tuple[int, int, int, int]] = {}  # name -> (x0,y0,x1,y1)

    # --- Classification -------------------------------------------------------
    def _kind(self, pair, links, sessions) -> str:
        """cable (link only) / peered (link + session) / session (session only)."""
        if pair in links and pair in sessions:
            return "peered"
        return "cable" if pair in links else "session"

    def _assign_lanes(self, arcs):
        """Balance non-adjacent intra-AS arcs above/below the row to limit stacking.

        arcs: list of (lo, hi, kind). Returns (placements, up_count, down_count)
        where placements[i] = (side, level): side in {"up","down"}, level >= 1.
        Each arc is placed on whichever side gives it the lower lane.
        """
        up: list[list[tuple[int, int]]] = []
        down: list[list[tuple[int, int]]] = []
        placements: list[tuple[str, int] | None] = [None] * len(arcs)

        def fit(lanes, lo, hi):
            for i, occ in enumerate(lanes):
                if all(hi < o0 or lo > o1 for o0, o1 in occ):
                    return i
            return len(lanes)

        for i in sorted(range(len(arcs)), key=lambda i: (arcs[i][1] - arcs[i][0], arcs[i][0])):
            lo, hi, _ = arcs[i]
            lu, ld = fit(up, lo, hi), fit(down, lo, hi)
            side, lanes, level = ("up", up, lu) if lu <= ld else ("down", down, ld)
            if level == len(lanes):
                lanes.append([])
            lanes[level].append((lo, hi))
            placements[i] = (side, level + 1)
        return placements, len(up), len(down)

    # --- Layout ---------------------------------------------------------------
    def _layout(self) -> dict:
        """Place AS regions on a grid and routers in a row inside each."""
        links    = {frozenset(p) for p in self.LINKS}
        sessions = {frozenset(p) for p in self.SESSIONS}

        # Group routers by AS, preserving first-seen order.
        order: list[int] = []
        groups: dict[int, list[str]] = {}
        for name, asn in self.ROUTERS:
            if asn not in groups:
                groups[asn] = []
                order.append(asn)
            groups[asn].append(name)
        clusters = [(asn, groups[asn]) for asn in order]
        cluster_of = {m: ci for ci, (_, members) in enumerate(clusters) for m in members}

        # Intra-AS connections + arc lanes per cluster.
        intra = []
        for _, members in clusters:
            pos = {m: i for i, m in enumerate(members)}
            mset = set(members)
            straights, arc_pairs = [], []
            for pair in links | sessions:
                a, b = tuple(pair)
                if a in mset and b in mset:
                    lo, hi = sorted((pos[a], pos[b]))
                    bucket = straights if hi - lo == 1 else arc_pairs
                    bucket.append((lo, hi, self._kind(pair, links, sessions)))
            placements, up_lanes, down_lanes = self._assign_lanes(arc_pairs)
            arcs = [(lo, hi, *placements[k], kind) for k, (lo, hi, kind) in enumerate(arc_pairs)]
            intra.append({"straights": straights, "arcs": arcs,
                          "up": up_lanes, "down": down_lanes})

        # Region sizes. Each arc band gets +1 extra row so risers are visible
        # (a flat arc flush against the box edge reads as a floating bar).
        for info in intra:
            info["arc_up"]   = info["up"] + 1 if info["up"] else 0
            info["arc_down"] = info["down"] + 1 if info["down"] else 0
        sizes = []
        for (_, members), info in zip(clusters, intra):
            n = len(members)
            inner_w = n * self.BOX_W + (n - 1) * self.ROUTER_GAP
            w = inner_w + 2 + 2 * self.PAD                              # borders + padding
            h = info["arc_up"] + self.BOX_H + info["arc_down"] + 2      # arcs + boxes + borders
            sizes.append((w, h))

        # Region grid placement (column widths / row heights = max in line).
        cols = self.CLUSTER_COLS
        nrows = (len(clusters) + cols - 1) // cols
        colw = [0] * cols
        rowh = [0] * nrows
        for ci, (w, h) in enumerate(sizes):
            colw[ci % cols] = max(colw[ci % cols], w)
            rowh[ci // cols] = max(rowh[ci // cols], h)
        xoff = [0] * cols
        for c in range(1, cols):
            xoff[c] = xoff[c - 1] + colw[c - 1] + self.CLUS_HGUT
        yoff = [0] * nrows
        for r in range(1, nrows):
            yoff[r] = yoff[r - 1] + rowh[r - 1] + self.CLUS_VGUT
        width  = (xoff[-1] + colw[-1]) if cols else 1
        height = (yoff[-1] + rowh[-1]) if nrows else 1

        # Region rects + router rects.
        crects, rrects = [], {}
        for ci, ((_, members), (w, h), info) in enumerate(zip(clusters, sizes, intra)):
            x0, y0 = xoff[ci % cols], yoff[ci // cols]
            crects.append((x0, y0, x0 + w - 1, y0 + h - 1))
            row_y = y0 + 1 + info["arc_up"]
            bx0 = x0 + 1 + self.PAD
            for i, m in enumerate(members):
                rx0 = bx0 + i * (self.BOX_W + self.ROUTER_GAP)
                rrects[m] = (rx0, row_y, rx0 + self.BOX_W - 1, row_y + self.BOX_H - 1)

        # Inter-AS connections, per router pair so eBGP ties to the real border
        # routers. A session between ASes is eBGP; a bare inter-AS link is a cable.
        inter: list[tuple[str, str, str]] = []
        for pair in links | sessions:
            a, b = tuple(pair)
            if cluster_of[a] == cluster_of[b]:
                continue
            inter.append((a, b, "ebgp" if pair in sessions else "cable"))

        return {
            "clusters": clusters, "crects": crects, "rrects": rrects,
            "cluster_of": cluster_of, "intra": intra, "inter": inter, "cols": cols,
            "width": max(width, 1), "height": max(height, 1),
        }

    # --- Rendering ------------------------------------------------------------
    def render(self) -> Text:
        L = self._layout()
        width, height = L["width"], L["height"]
        buf   = [[" "] * width for _ in range(height)]
        style = [[""]  * width for _ in range(height)]
        self._rects = L["rrects"]

        # Resolve colors from the live theme (rich can't read $accent / $text).
        theme = self.app.current_theme
        kind_style = {
            "cable":   self.STYLE_CABLE,         # physical link, no peering
            "peered":  theme.success,            # iBGP over a direct cable
            "session": theme.warning,            # iBGP, multi-hop (no direct cable)
            "ebgp":    f"bold {theme.success}",  # eBGP between ASes (double line)
        }
        sel_style = f"bold {theme.background} on {theme.accent}"
        amap = dict(self.ROUTERS)

        as_index = {asn: i for i, (asn, _) in enumerate(L["clusters"])}
        def as_color(asn):
            return self.AS_PALETTE[as_index[asn] % len(self.AS_PALETTE)]

        # 1. Region borders + titles, 2. router boxes -- both before connectors,
        # each tinted by its AS so the clusters are easy to tell apart.
        for (asn, _), rect in zip(L["clusters"], L["crects"]):
            self._draw_region(buf, style, rect, f"AS{asn}", as_color(asn))
        for name, (x0, y0, _, _) in L["rrects"].items():
            s = sel_style if name == self.selected else as_color(amap[name])
            self._draw_box(buf, style, x0, y0, name, s)

        # 2b. eBGP markers: a small arrow on each border router's edge, pointing
        # at its external peer -- a hint that a session is there to reveal on click.
        cof, cols = L["cluster_of"], L["cols"]
        for a, b, k in L["inter"]:
            if k != "ebgp":
                continue
            for r, peer in ((a, b), (b, a)):
                x0, y0, x1, y1 = L["rrects"][r]
                cx, cy = x0 + self.BOX_W // 2, y0 + self.BOX_H // 2
                if cof[r] // cols == cof[peer] // cols:        # peer is left/right
                    gx, gy, ch = ((x1, cy, "▸") if cof[peer] % cols > cof[r] % cols
                                  else (x0, cy, "◂"))
                else:                                          # peer is above/below
                    gx, gy, ch = ((cx, y1, "▾") if cof[peer] // cols > cof[r] // cols
                                  else (cx, y0, "▴"))
                buf[gy][gx] = ch
                style[gy][gx] = kind_style["ebgp"]

        # 3. Connectors fill the remaining empty cells, abutting boxes/borders.
        # Box interiors are spaces, so guard them explicitly or a line would run
        # straight through a router box.
        boxes = list(L["rrects"].values())
        def in_box(x, y):
            return any(bx0 <= x <= bx1 and by0 <= y <= by1 for bx0, by0, bx1, by1 in boxes)

        mask, kind = self._routes(L)
        for (x, y), dirs in mask.items():
            if 0 <= y < height and 0 <= x < width and buf[y][x] == " " and not in_box(x, y):
                k = kind[(x, y)]
                glyphs = self._GLYPH2 if k == "ebgp" else self._GLYPH
                buf[y][x] = glyphs.get(frozenset(dirs), "·")
                style[y][x] = kind_style[k]

        text = Text(no_wrap=True)
        for y in range(height):
            if y:
                text.append("\n")
            x = 0
            while x < width:
                s, run = style[y][x], buf[y][x]
                x += 1
                while x < width and style[y][x] == s:
                    run += buf[y][x]
                    x += 1
                text.append(run, style=s or None)
        return text

    def _draw_box(self, buf, style, x0, y0, name, s) -> None:
        inner = self.BOX_W - 2
        rows = [
            "┌" + "─" * inner + "┐",
            "│" + name.center(inner) + "│",
            "└" + "─" * inner + "┘",
        ]
        for dy, line in enumerate(rows):
            for dx, ch in enumerate(line):
                buf[y0 + dy][x0 + dx] = ch
                style[y0 + dy][x0 + dx] = s

    def _draw_region(self, buf, style, rect, title, s) -> None:
        x0, y0, x1, y1 = rect
        w = x1 - x0 + 1
        cap = f"─ {title} "
        top = "╭" + cap + "─" * (w - 2 - len(cap)) + "╮"
        bot = "╰" + "─" * (w - 2) + "╯"
        for dx, ch in enumerate(top):
            buf[y0][x0 + dx] = ch
            style[y0][x0 + dx] = s
        for dx, ch in enumerate(bot):
            buf[y1][x0 + dx] = ch
            style[y1][x0 + dx] = s
        for y in range(y0 + 1, y1):
            buf[y][x0] = buf[y][x1] = "│"
            style[y][x0] = style[y][x1] = s

    # --- Connector routing ----------------------------------------------------
    def _routes(self, L):
        """Per-cell (directions, kind) for intra-AS and inter-AS connectors."""
        mask: dict[tuple[int, int], set[str]] = {}
        kind: dict[tuple[int, int], str] = {}

        def mark(p, q, k) -> None:
            (x1, y1), (x2, y2) = p, q
            dx = (x2 > x1) - (x2 < x1)
            dy = (y2 > y1) - (y2 < y1)
            cells = [(x1, y1)]
            x, y = x1, y1
            while (x, y) != (x2, y2):
                x, y = x + dx, y + dy
                cells.append((x, y))
            for cell in cells:
                if cell not in kind or self._RANK[k] > self._RANK[kind[cell]]:
                    kind[cell] = k
            for j in range(len(cells) - 1):
                ax, ay = cells[j]
                bx, by = cells[j + 1]
                d  = "R" if bx > ax else "L" if bx < ax else "D" if by > ay else "U"
                rd = {"R": "L", "L": "R", "D": "U", "U": "D"}[d]
                mask.setdefault((ax, ay), set()).add(d)
                mask.setdefault((bx, by), set()).add(rd)

        # Intra-AS: straights in the gaps, multi-hop iBGP as arcs over the row.
        for ci, info in enumerate(L["intra"]):
            members = L["clusters"][ci][1]
            for lo, hi, k in info["straights"]:
                la, ra = L["rrects"][members[lo]], L["rrects"][members[hi]]
                y = la[1] + self.BOX_H // 2
                mark((la[2] + 1, y), (ra[0] - 1, y), k)
            for lo, hi, side, level, k in info["arcs"]:
                la, ra = L["rrects"][members[lo]], L["rrects"][members[hi]]
                lx, rx = la[0] + self.BOX_W // 2, ra[0] + self.BOX_W // 2
                if side == "up":
                    edge = la[1] - 1            # row just above the boxes
                    lane = edge - level         # arc horizontal sits above
                else:
                    edge = la[3] + 1            # row just below the boxes
                    lane = edge + level         # arc horizontal sits below
                mark((lx, lane), (lx, edge), k)  # left riser to the box
                mark((lx, lane), (rx, lane), k)  # arc across the band
                mark((rx, lane), (rx, edge), k)  # right riser to the box

        # Inter-AS: anchor to the actual border routers. Same-region-row pairs
        # route horizontally through the column gutter; stacked pairs route
        # vertically through the row gutter, jogging to line up the endpoints.
        cols, cof, cr, rr = L["cols"], L["cluster_of"], L["crects"], L["rrects"]
        mid = self.BOX_H // 2
        vtrack: dict[int, int] = {}  # gutter-top y -> next free channel offset
        for a, b, k in L["inter"]:
            # Interactive reveal: only the selected router's eBGP is drawn, so the
            # canvas stays clean and we never route many crossings at once.
            if self.selected not in (a, b):
                continue
            ra, rb = rr[a], rr[b]
            if cof[a] // cols == cof[b] // cols:
                (ln, left), (rn, right) = sorted(
                    [(a, ra), (b, rb)], key=lambda t: t[1][0])
                ya, yb = left[1] + mid, right[1] + mid
                gx = (cr[cof[ln]][2] + cr[cof[rn]][0]) // 2  # midpoint of the gutter
                mark((left[2] + 1, ya), (gx, ya), k)
                mark((gx, ya), (gx, yb), k)
                mark((gx, yb), (right[0] - 1, yb), k)
            else:
                (un, up), (dn, dn_rect) = sorted(
                    [(a, ra), (b, rb)], key=lambda t: t[1][1])
                ux, dx = up[0] + self.BOX_W // 2, dn_rect[0] + self.BOX_W // 2
                # Give each crossing its own channel row so they don't merge.
                gtop = cr[cof[un]][3] + 1
                off = vtrack.get(gtop, 0)
                vtrack[gtop] = off + 1
                gy = min(gtop + 1 + off, cr[cof[dn]][1] - 2)
                mark((ux, up[3] + 1), (ux, gy), k)
                mark((ux, gy), (dx, gy), k)
                mark((dx, gy), (dx, dn_rect[1] - 1), k)

        return mask, kind

    # --- Interaction ----------------------------------------------------------
    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None:
            return
        x, y = offset.x, offset.y
        for name, (x0, y0, x1, y1) in self._rects.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.selected = None if self.selected == name else name
                self.refresh()
                return


class LegendPanel(Widget):
    """A small color key for the topology, sitting beside the command history."""

    def __init__(self) -> None:
        super().__init__(id="legend_panel")
        self.border_title = "Legend"

    def render(self) -> Text:
        theme = self.app.current_theme
        t = Text(no_wrap=True)
        for mark, swatch, label in (
            ("── ", TopologyView.STYLE_CABLE,    "cable"),
            ("── ", theme.success,               "iBGP"),
            ("── ", theme.warning,               "iBGP multi-hop"),
            ("══ ", f"bold {theme.success}",     "eBGP"),
        ):
            t.append(mark, style=swatch)
            t.append(f"{label}\n")
        t.append("\n")

        seen: list[int] = []
        for _, asn in TopologyView.ROUTERS:
            if asn not in seen:
                seen.append(asn)
        for i, asn in enumerate(seen):
            t.append("██ ", style=TopologyView.AS_PALETTE[i % len(TopologyView.AS_PALETTE)])
            t.append(f"AS{asn}\n")
        return t


class BGPSimApp(App):
    """BGP Simulator app in Textual. Textual is cool!"""

    TITLE = "ChatBGP"
    SUB_TITLE = "The BGP Simulator you didn't ask for"

    CSS_PATH = "style.tcss"
    BINDINGS = [
        Binding("t", "timeline_toggle", "Toggle Timeline panel"),
        Binding("i", "inspector_toggle", "Toggle Inspector panel"),
        Binding("h", "history_toggle", "Toggle Command history"),
        Binding("l", "legend_toggle", "Toggle Legend"),
        Binding("q", "quit", "Quit"),
    ]

    PANEL_FULL_WIDTH = 41   # full side-panel footprint: width 40 + 1 border
    PANEL_NARROW_WIDTH = 25 # narrow side-panel footprint: width 24 + 1 border
    MIN_CENTER_WIDTH = 50   # narrow the panels once the center drops below this
    MIN_CENTER_FLOOR = 30   # below this center width, even shrunk, show popup
    SHORT_HEIGHT = 24       # threshold to use small command bar
    MIN_HEIGHT = 18         # ew, smol

    def on_mount(self):
        self.theme = "rose-pine"
        self._apply_responsive(self.size.width, self.size.height)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()

        with Horizontal(id="body"):
            yield TimelinePanel()
            with Vertical(id="center"):
                yield TopologyView()
                with Horizontal(id="history_row"):
                    yield CommandHistory()
                    yield LegendPanel()
            yield InspectorPanel()

        with Container(id="bottom_bar"):
            with Horizontal(id="command_row"):
                yield Button("🧹", id="clear_history")
                yield CommandBar()
            yield Footer()

        yield TooSmallOverlay()

    @on(Input.Submitted)
    async def append_msg(self, event: Input.Submitted) -> None:
        """When user sends a message, create a new tab holding it."""
        if not event.value:
            return

        await self.query_one(CommandHistory).add_command(event.value)
        event.input.clear()

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

    _legend_on: bool = True

    async def action_history_toggle(self) -> None:
        """Toggle the Command history, handing its space to the topology view"""
        self.query_one(CommandHistory).toggle_class("-hidden")
        await self._sync_legend()

    async def action_legend_toggle(self) -> None:
        """Toggle the Legend on/off"""
        self._legend_on = not self._legend_on
        await self._sync_legend()

    async def _sync_legend(self) -> None:
        """Place the legend: inline beside history, or floating when history is
        hidden (so the topology reclaims the full center)."""
        for widget in list(self.query(LegendPanel)):
            await widget.remove()
        if not self._legend_on:
            return
        panel = LegendPanel()
        if self.query_one(CommandHistory).has_class("-hidden"):
            panel.add_class("-floating")
            await self.screen.mount(panel)
            self._position_floating_legend()
        else:
            await self.query_one("#history_row").mount(panel)

    def _position_floating_legend(self) -> None:
        """Pin the floating legend to the bottom-right, above the command bar."""
        bar = self.query_one("#bottom_bar").size.height
        for panel in self.query(LegendPanel):
            if panel.has_class("-floating"):
                panel.styles.offset = (
                    max(self.size.width - 24, 0),
                    max(self.size.height - 12 - bar, 0),
                )

    def on_resize(self, event: events.Resize) -> None:
        """Re-evaluate the responsive layout whenever the terminal resizes."""
        self._apply_responsive(event.size.width, event.size.height)
        self._position_floating_legend()

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
        self.query_one(TooSmallOverlay).set_class(too_small, "-show")
        if too_small:
            need_width = open_panels * self.PANEL_NARROW_WIDTH + self.MIN_CENTER_FLOOR
            hint = (
                "Close a side panel (press t or i) or resize the window."
                if open_panels
                else "Please resize the window."
            )
            self.query_one("#too_small_dialog", Static).update(
                "Window too small\n\n"
                f"Current size: {width} x {height}\n"
                f"Need at least: {need_width} x {self.MIN_HEIGHT}\n\n"
                f"{hint}"
            )


if __name__ == "__main__":
    app = BGPSimApp()
    app.run()
