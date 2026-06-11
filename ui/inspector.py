from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import (
    Static,
    TabbedContent,
    TabPane,
)

from models.bgp.route import BGPRouteSourceType
from models.routing_table import Route, RouteType
from models.router import Router


class InspectorPanel(Vertical):
    """Inspector panel, docked to the right of the screen.

    It contains a TabbedContent with two tabs: "Routing Table" and
    "BGP Loc RIB", showing the inspected router's state.
    """

    def __init__(self) -> None:
        super().__init__(id="inspector_panel", classes="-hidden")

    def compose(self) -> ComposeResult:
        top = Vertical(id="inspector_top")
        top.border_title = "Inspector"
        with top:
            with TabbedContent(id="inspector_tabs"):
                with TabPane("Routing Table", id="routing_table"):
                    yield VerticalScroll(Static("", id="rt_body"))
                with TabPane("BGP Loc RIB", id="bgp_loc_rib"):
                    yield VerticalScroll(Static("", id="rib_body"))
        ifaces_section = Vertical(id="inspector_ifaces")
        ifaces_section.border_title = "Interfaces"
        with ifaces_section:
            yield VerticalScroll(Static("", id="iface_body"))

    def show(self, router: Router | None) -> None:
        """Render the selected router's RIB + BGP Loc-RIB (or a hint if None)."""
        top = self.query_one("#inspector_top", Vertical)
        rt = self.query_one("#rt_body", Static)
        rib = self.query_one("#rib_body", Static)
        ifaces = self.query_one("#iface_body", Static)

        if router is None:
            top.border_title = "Inspector"
            rt.update("(click a router in the topology)")
            rib.update("(click a router in the topology)")
            ifaces.update("(click a router in the topology)")
            return

        top.border_title = (
            f"{router.name}   id {router.router_id}   AS{router.bgp_engine.asn}"
        )
        rt.update(self._fmt_routing_table(router))
        rib.update(self._fmt_loc_rib(router))
        ifaces.update(self._fmt_interfaces(router))

    @classmethod
    def _route_kind(cls, r: Route) -> str:
        """Bucket a route into a legend category (its name is also its label)."""
        if r.route_type is RouteType.DIRECT:
            return "loopback" if r.interface.is_loopback else "connected"
        if r.route_type is RouteType.BGP:
            return "bgp"
        return "static"

    # Color coding for easy distinction between connected route's types
    _RT_COLOR = {
        "loopback":  "$accent",
        "connected": "$success",
        "static":    "$secondary",
        "bgp":       "$warning",
    }

    @classmethod
    def _fmt_routing_table(cls, router: Router) -> str:
        blocks = []
        for r in router.routing_table.routes:
            nh = "connected" if r.next_hop is None else str(r.next_hop)
            kind = cls._route_kind(r)
            color = cls._RT_COLOR[kind]
            blocks.append(
                "\n".join((
                    f"[{color}]{r.network}[/]",
                    f"  via {nh}",
                    f"  dev {r.interface.name}",
                    f"  [{color}]\\[{kind.upper()}][/]",
                ))
            )
        return "\n\n".join(blocks) or "(no routes)"

    # Color coding for different BGP route source types
    _RIB_COLOR = {
        BGPRouteSourceType.LOCAL: "$accent",
        BGPRouteSourceType.IBGP:  "$success",
        BGPRouteSourceType.EBGP:  "$warning",
    }

    @classmethod
    def _fmt_loc_rib(cls, router: Router) -> str:
        blocks = []
        for prefix, br in router.bgp_engine.loc_rib.items():
            path = " ".join(str(a) for a in br.as_path) or "i"
            color = cls._RIB_COLOR.get(br.source.type, "$text")
            blocks.append(
                "\n".join((
                    f"[{color}]{prefix}[/]",
                    f"  next-hop {br.next_hop}",
                    f"  as-path [{path}]",
                    f"  local-pref {br.local_pref}",
                    f"  [{color}]{br.source.type.value}[/]",
                ))
            )
        return "\n\n".join(blocks) or "(empty Loc-RIB)"

    @classmethod
    def _fmt_interfaces(cls, router: Router) -> str:
        """Per-interface block: name, IP/prefix, and the peer on the other end."""
        blocks = []
        for iface in router.interfaces.values():
            if iface.link is None:
                color, other = "$accent", "loopback"
            else:
                color = "$success"
                peer = iface.link.get_peer_of(router)
                other = f"→ {peer.name} ({iface.link.get_ip(peer)})"
            blocks.append(
                "\n".join((
                    f"[{color}]{iface.name}[/]",
                    f"  {iface.ip}/{iface.network.prefixlen}",
                    f"  {other}",
                ))
            )
        return "\n\n".join(blocks) or "(no interfaces)"
