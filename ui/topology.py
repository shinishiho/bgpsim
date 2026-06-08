from rich.text import Text

from textual import events
from textual.geometry import Size
from textual.message import Message
from textual.widget import Widget

from models.world import World


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

    # double-line variant, used for iBGP (cabled + multi-hop) so peerings stand
    # out from bare cables
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

    # heavy-line variant, used for eBGP so inter-AS links stand out as the
    # heaviest weight (Unicode has no triple-line set with corners)
    _GLYPH3 = {
        frozenset({"L", "R"}):           "━",
        frozenset({"U", "D"}):           "┃",
        frozenset({"D", "R"}):           "┏",
        frozenset({"D", "L"}):           "┓",
        frozenset({"U", "R"}):           "┗",
        frozenset({"U", "L"}):           "┛",
        frozenset({"U", "D", "R"}):      "┣",
        frozenset({"U", "D", "L"}):      "┫",
        frozenset({"L", "R", "D"}):      "┳",
        frozenset({"L", "R", "U"}):      "┻",
        frozenset({"U", "D", "L", "R"}): "╋",
        frozenset({"L"}):                "━",
        frozenset({"R"}):                "━",
        frozenset({"U"}):                "┃",
        frozenset({"D"}):                "┃",
    }

    STYLE_CABLE = "grey42"
    # paint priority when routes share a cell (higher wins)
    _RANK = {"cable": 1, "peered": 2, "ebgp": 3, "session": 4}
    # per-AS accent colors (cycled), chosen to avoid the cable/peered/session hues
    AS_PALETTE = ["#c4a7e7", "#eb6f92", "#3e8fb0", "#ea9a97", "#a6da95", "#f5c2e7"]
    # per-inter-AS-pair accent colors (cycled): the two endpoints of one inter-AS
    # connection share a hue, so several sessions meeting at a border stay legible.
    INTER_PALETTE = ["#9ccfd8", "#f6c177", "#ebbcba", "#56949f", "#907aa9", "#b4637a"]

    class RouterSelected(Message):
        """Posted when the user clicks a router box (name is None on deselect)."""

        def __init__(self, name: str | None) -> None:
            super().__init__()
            self.name = name

    def __init__(self) -> None:
        super().__init__(id="topology")
        self.selected: str | None = None
        self._rects: dict[str, tuple[int, int, int, int]] = {}  # name -> (x0,y0,x1,y1)
        self._topo_layout: dict | None = None

    def _get_layout(self) -> dict:
        """Cached `_layout()`; invalidated by `sync()` when the topology changes."""
        if self._topo_layout is None:
            self._topo_layout = self._layout()
        return self._topo_layout

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return self._get_layout()["width"]

    def get_content_height(
        self, container: Size, viewport: Size, width: int
    ) -> int:
        return self._get_layout()["height"]

    def sync(self, world: World) -> None:
        """Derive the topology from the live world and repaint.

        Sets ROUTERS/LINKS/SESSIONS as *instance* attrs, shadowing the class-level
        demo data the renderer reads through `self.`.
        """
        self.ROUTERS = [(r.name, r.bgp_engine.asn) for r in world.routers.routers]
        self.LINKS = [
            tuple(iface.router.name for iface in lk.interfaces)
            for lk in world.links.links
        ]
        self.SESSIONS = [
            tuple(side.router.name for side in sess.sides.values())
            for sess in world.bgp_sessions.sessions
        ]
        if self.selected not in dict(self.ROUTERS):
            self.selected = None
        self._topo_layout = None
        self.refresh(layout=True)

    # --- Classification -------------------------------------------------------
    def _kind(
        self,
        pair: frozenset[str],
        links: set[frozenset[str]],
        sessions: set[frozenset[str]],
    ) -> str:
        """cable (link only) / peered (link + session) / session (session only)."""
        if pair in links and pair in sessions:
            return "peered"
        return "cable" if pair in links else "session"

    def _assign_lanes(
        self, arcs: list[tuple[int, int, str]]
    ) -> tuple[list[tuple[str, int] | None], int, int]:
        """Balance non-adjacent intra-AS arcs above/below the row to limit stacking.

        arcs: list of (lo, hi, kind). Returns (placements, up_count, down_count)
        where placements[i] = (side, level): side in {"up","down"}, level >= 1.
        Each arc is placed on whichever side gives it the lower lane.
        """
        up: list[list[tuple[int, int]]] = []
        down: list[list[tuple[int, int]]] = []
        placements: list[tuple[str, int] | None] = [None] * len(arcs)

        def fit(lanes: list[list[tuple[int, int]]], lo: int, hi: int) -> int:
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
        L = self._get_layout()
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
            "ebgp":    f"bold {theme.success}",  # eBGP between ASes (heavy line)
        }
        sel_style = f"bold {theme.background} on {theme.accent}"
        amap = dict(self.ROUTERS)

        as_index = {asn: i for i, (asn, _) in enumerate(L["clusters"])}
        def as_color(asn: int) -> str:
            return self.AS_PALETTE[as_index[asn] % len(self.AS_PALETTE)]

        # One stable hue per inter-AS connection, shared by its two endpoint marks
        # and its revealed connector. Sorted so the assignment doesn't depend on
        # the (set-derived) order of L["inter"].
        inter_color = {
            frozenset((a, b)): self.INTER_PALETTE[i % len(self.INTER_PALETTE)]
            for i, (a, b, _) in enumerate(
                sorted(L["inter"], key=lambda t: tuple(sorted(t[:2])))
            )
        }
        def edge_style(k: str, col: str) -> str:
            return f"bold {col}" if k == "ebgp" else col

        # 1. Region borders + titles, 2. router boxes -- both before connectors,
        # each tinted by its AS so the clusters are easy to tell apart.
        for (asn, _), rect in zip(L["clusters"], L["crects"]):
            self._draw_region(buf, style, rect, f"AS{asn}", as_color(asn))
        for name, (x0, y0, _, _) in L["rrects"].items():
            s = sel_style if name == self.selected else as_color(amap[name])
            self._draw_box(buf, style, x0, y0, name, s)

        # 2b. Inter-AS edge hints: a small mark on each border router's edge,
        # pointing at its external peer -- a hint of something to reveal on click.
        # eBGP gets a directional arrow (a session); a bare inter-AS cable gets a
        # neutral node (a physical link, no peering). Each connection has its own
        # hue (shared by both ends) so several meeting at a border stay matchable.
        # Both draw their full connector through the gutter only when selected.
        cof, cols = L["cluster_of"], L["cols"]
        for a, b, k in L["inter"]:
            col = inter_color[frozenset((a, b))]
            for r, peer in ((a, b), (b, a)):
                x0, y0, x1, y1 = L["rrects"][r]
                cx, cy = x0 + self.BOX_W // 2, y0 + self.BOX_H // 2
                if cof[r] // cols == cof[peer] // cols:        # peer is left/right
                    gx, gy, arrow = ((x1, cy, "▸") if cof[peer] % cols > cof[r] % cols
                                     else (x0, cy, "◂"))
                else:                                          # peer is above/below
                    gx, gy, arrow = ((cx, y1, "▾") if cof[peer] // cols > cof[r] // cols
                                     else (cx, y0, "▴"))
                buf[gy][gx] = arrow if k == "ebgp" else "○"
                style[gy][gx] = edge_style(k, col)

        # 3. Connectors fill the remaining empty cells, abutting boxes/borders.
        # Box interiors are spaces, so guard them explicitly or a line would run
        # straight through a router box.
        boxes = list(L["rrects"].values())
        def in_box(x: int, y: int) -> bool:
            return any(bx0 <= x <= bx1 and by0 <= y <= by1 for bx0, by0, bx1, by1 in boxes)

        mask, kind, color = self._routes(L, inter_color)
        # line weight carries the link type: light cable, double iBGP, heavy eBGP
        glyph_set = {
            "cable":   self._GLYPH,
            "peered":  self._GLYPH2,
            "session": self._GLYPH2,
            "ebgp":    self._GLYPH3,
        }
        for (x, y), dirs in mask.items():
            if 0 <= y < height and 0 <= x < width and buf[y][x] == " " and not in_box(x, y):
                k = kind[(x, y)]
                glyphs = glyph_set[k]
                buf[y][x] = glyphs.get(frozenset(dirs), "·")
                col = color.get((x, y))
                style[y][x] = edge_style(k, col) if col else kind_style[k]

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

    def _draw_box(
        self,
        buf: list[list[str]],
        style: list[list[str]],
        x0: int,
        y0: int,
        name: str,
        s: str,
    ) -> None:
        inner = self.BOX_W - 2
        # str.center doesn't truncate, so a long name would overrun the box and
        # clobber the border/neighbours -- clip it with an ellipsis instead.
        label = name if len(name) <= inner else name[: inner - 1] + "…"
        rows = [
            "┌" + "─" * inner + "┐",
            "│" + label.center(inner) + "│",
            "└" + "─" * inner + "┘",
        ]
        for dy, line in enumerate(rows):
            for dx, ch in enumerate(line):
                buf[y0 + dy][x0 + dx] = ch
                style[y0 + dy][x0 + dx] = s

    def _draw_region(
        self,
        buf: list[list[str]],
        style: list[list[str]],
        rect: tuple[int, int, int, int],
        title: str,
        s: str,
    ) -> None:
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
    def _routes(
        self, L: dict, inter_color: dict | None = None
    ) -> tuple[dict, dict, dict]:
        """Per-cell (directions, kind, color) for intra-/inter-AS connectors.

        `color` only carries the per-pair hue for inter-AS cells; intra-AS cells
        are absent from it and fall back to the kind palette.
        """
        inter_color = inter_color or {}
        mask: dict[tuple[int, int], set[str]] = {}
        kind: dict[tuple[int, int], str] = {}
        color: dict[tuple[int, int], str] = {}

        def mark(
            p: tuple[int, int],
            q: tuple[int, int],
            k: str,
            col: str | None = None,
        ) -> None:
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
                    if col is not None:
                        color[cell] = col
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
        vtrack: dict[int, int] = {}  # dest row -> next free channel offset
        for a, b, k in L["inter"]:
            # Interactive reveal: only the selected router's eBGP is drawn, so the
            # canvas stays clean and we never route many crossings at once.
            if self.selected not in (a, b):
                continue
            col = inter_color.get(frozenset((a, b)))
            ra, rb = rr[a], rr[b]
            if cof[a] // cols == cof[b] // cols:
                (ln, left), (rn, right) = sorted(
                    [(a, ra), (b, rb)], key=lambda t: t[1][0])
                ya, yb = left[1] + mid, right[1] + mid
                gx = (cr[cof[ln]][2] + cr[cof[rn]][0]) // 2  # midpoint of the gutter
                mark((left[2] + 1, ya), (gx, ya), k, col)
                mark((gx, ya), (gx, yb), k, col)
                mark((gx, yb), (right[0] - 1, yb), k, col)
            else:
                (un, up), (dn, dn_rect) = sorted(
                    [(a, ra), (b, rb)], key=lambda t: t[1][1])
                ux, dx = up[0] + self.BOX_W // 2, dn_rect[0] + self.BOX_W // 2
                # Route the horizontal jog through the inter-row gutter, below the
                # *whole* upper row. Anchoring to the source cluster's own bottom
                # would tuck it under a short AS (e.g. AS2) and run it straight
                # through a taller neighbour (e.g. AS1) in the same row, which then
                # paints over it. The gutter starts CLUS_VGUT rows above the
                # destination cluster. Give each crossing into the same gutter its
                # own channel so they don't merge.
                grow = cof[dn] // cols
                gtop = cr[cof[dn]][1] - self.CLUS_VGUT
                off = vtrack.get(grow, 0)
                vtrack[grow] = off + 1
                gy = min(gtop + off, cr[cof[dn]][1] - 2)
                mark((ux, up[3] + 1), (ux, gy), k, col)
                mark((ux, gy), (dx, gy), k, col)
                mark((dx, gy), (dx, dn_rect[1] - 1), k, col)

        return mask, kind, color

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
                self.post_message(self.RouterSelected(self.selected))
                return
