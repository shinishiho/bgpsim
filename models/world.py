from typing import NamedTuple, TYPE_CHECKING
from ipaddress import IPv4Network

from .router import RouterManager
from .link import LinkManager
from .bgp.session import BGPSessionManager

if TYPE_CHECKING:
    from ipaddress import IPv4Address
    from .router import Router, Interface
    from .link import Link
    from .bgp.session import BGPSession


_IFACE_ABBR = {
    "GigabitEthernet": "Gi",
    "Loopback": "Lo",
}


def _short_iface(name: str) -> str:
    """Get short interface name for titles"""
    for long, short in _IFACE_ABBR.items():
        if name.startswith(long):
            return short + name[len(long):]
    return name


class ConvergeResult(NamedTuple):
    """Outcome of a `World.converge()` run: whether it reached a fixed point
    and how many ticks it took."""

    converged: bool
    ticks: int


class World:
    """The simulator world

    It holds everything: routers, links, BGP sessions, timers.
    """

    def __init__(self):
        self.routers:      RouterManager     = RouterManager()
        self.links:        LinkManager       = LinkManager()
        self.bgp_sessions: BGPSessionManager = BGPSessionManager()
        self.clock:        WorldClock        = WorldClock()

    def step(self) -> list[WorldEvent]:
        """Advance the simulation one round.

        Check for BGP session state changes, then run BGP engines in two phases.
        All routers will prepare their outgoing updates, then send at once, so that the order of
        execution doesn't matter. This also makes the updates propagate once (by one hop) per tick.
        """
        self.clock.now += 1
        before = len(self.clock.events)

        self.bgp_sessions.update_sessions_state(clock=self.clock)

        for router in self.routers.routers:
            router.bgp_engine.compute(clock=self.clock)
        for router in self.routers.routers:
            router.bgp_engine.commit(clock=self.clock)

        events = self.clock.events[before:]
        if not events:
            self.clock.now -= 1  # There is no event, the clock doesn't move.
        return events

    def converge(self, max_ticks: int = 256) -> ConvergeResult:
        """Tick until no more updates, capped at `max_ticks`.

        Returns whether a fixed point was reached and how many ticks it took. A
        network with no fixed point (a BGP route oscillation / policy dispute)
        keeps producing events forever, so we stop at the cap and report
        `converged=False` instead of spinning or raising.
        """
        for ticks in range(1, max_ticks + 1):
            if not self.step():
                return ConvergeResult(True, ticks - 1)  # the last tick is empty, nothing happened
        return ConvergeResult(False, max_ticks)

    def build_ibgp_mesh(self, asn: int = 1) -> list[BGPSession]:
        """Create a full iBGP mesh for every router in `asn`.

        Get all routers in `asn` and send them to the BGPSessionManager
        """
        routers = [r for r in self.routers.routers if r.bgp_engine.asn == asn]
        sessions = self.bgp_sessions.build_ibgp_mesh(routers)
        for session in sessions:
            self._record_session_open(session)
        return sessions

    def destroy_ibgp_mesh(self, asn: int = 1) -> list[BGPSession]:
        """Tear down the iBGP sessions inside `asn`.

        Closes every iBGP session whose both endpoints live in `asn` (whether
        it came from a mesh or a hand-rolled `peer`); leaves eBGP untouched.
        """
        members = {r for r in self.routers.routers if r.bgp_engine.asn == asn}
        sessions = [
            s for s in self.bgp_sessions.sessions
            if not s.is_ebgp and set(s.sides) <= members
        ]
        for session in sessions:
            self.destroy_bgp_session(*session.sides)
        return sessions

    def create_router(self, name: str | None = None, asn: int = 1) -> Router:
        """Add a router to the world (auto-named R{N} when name is None)"""
        router = self.routers.create(name=name)
        router.bgp_engine.asn = asn
        self.clock.record(
            "sys",
            f"{router.name} online · AS{asn}",
            f"`{router.name}` came online in **AS{asn}**",
            f"hostname {router.name}\n!\nrouter bgp {asn}",
        )
        return router

    def destroy_router(self, router: Router) -> None:
        """Remove a router and everything attached to it.

        Tears down its BGP sessions, pulls every cable to it (freeing the link
        networks and the peer interfaces), and returns its loopback /32s to the
        pool before dropping it from the world.
        """
        for session in [s for s in self.bgp_sessions.sessions if router in s.sides]:
            self.bgp_sessions.destroy(session)
        peers = [
            r for r in self.routers.routers
            if r is not router and r.has_link_to(router)
        ]
        for peer in peers:
            self.links.destroy(router, peer)
        for iface in [i for i in router.interfaces.values() if i.is_loopback]:
            self.links.free_loopback(iface.network)
        self.routers.destroy(router)
        self.clock.record(
            "sys",
            f"{router.name} removed",
            f"`{router.name}` was removed from the topology",
            f"no hostname {router.name}",
        )

    def create_loopback(self, router: Router) -> Interface:
        """Add a loopback interface to `router` (a /32 from the loopback pool)"""
        network = self.links.alloc_loopback()
        iface = router.add_loopback(network)
        self.clock.record(
            "link",
            f"{router.name} {_short_iface(iface.name)} up",
            f"`{router.name}` `{_short_iface(iface.name)}` came up at `{iface.ip}/32`",
            f"interface {iface.name}\n ip address {iface.ip} {network.netmask}\n no shutdown",
        )
        return iface

    def destroy_loopback(self, router: Router, ip: IPv4Address | None = None) -> Interface:
        """Remove a loopback interface from `router` and free its /32.

        With a single loopback the address is optional; with several, the
        operator must name which one by its IP.
        """
        loopbacks = [i for i in router.interfaces.values() if i.is_loopback]
        if not loopbacks:
            raise ValueError(f"{router.name} has no loopback to remove")
        if ip is None:
            if len(loopbacks) > 1:
                addrs = ", ".join(str(i.ip) for i in loopbacks)
                raise ValueError(
                    f"{router.name} has several loopbacks ({addrs}); say which one"
                )
            iface = loopbacks[0]
        else:
            iface = next((i for i in loopbacks if i.ip == ip), None)
            if iface is None:
                raise ValueError(f"{router.name} has no loopback at {ip}")
        network = iface.network
        router.remove_loopback(iface)
        self.links.free_loopback(network)
        self.clock.record(
            "link",
            f"{router.name} {_short_iface(iface.name)} removed",
            f"`{router.name}` removed loopback `{_short_iface(iface.name)}` "
            f"(`{iface.ip}/32`)",
            f"no interface {iface.name}",
        )
        return iface

    def shutdown(self, router: Router, peer: Router) -> None:
        """Router's interface admin-down

        It will shutdown this router's interface towards the peer router.

        Note: by Cisco, it should take the interface name as argument, but here we
        specify two routers for convenience, the function will find the interface between them.
        """
        link = router.get_link_to(peer)
        ifname = router.interface_name(link)
        router.interfaces[ifname].shutdown()
        self.clock.record(
            "link",
            f"{router.name} {_short_iface(ifname)} down",
            f"`{router.name}` administratively shut `{_short_iface(ifname)}`",
            f"interface {ifname}\n shutdown",
        )

    def no_shutdown(self, router: Router, peer: Router) -> None:
        """Router's interface admin-up

        It will active this router's interface towards the peer router.
        """
        link = router.get_link_to(peer)
        ifname = router.interface_name(link)
        router.interfaces[ifname].no_shutdown()
        self.clock.record(
            "link",
            f"{router.name} {_short_iface(ifname)} up",
            f"`{router.name}` brought `{_short_iface(ifname)}` back up",
            f"interface {ifname}\n no shutdown",
        )

    def create_bgp_session(
        self,
        router_a: Router,
        router_b: Router,
        source_addr_a: IPv4Address | None = None,
        source_addr_b: IPv4Address | None = None,
    ) -> BGPSession:
        """Create a BGP session between two routers"""
        session = self.bgp_sessions.create(router_a, router_b, source_addr_a, source_addr_b)
        self._record_session_open(session)
        return session

    def _record_session_open(self, session: BGPSession) -> None:
        """Record new BGP peering session establishment (one for each side)."""
        for side in session.sides.values():
            router, peer, peer_ip, remote_as = side.router, side.peer, side.peer_ip, side.remote_as
            kind = "eBGP" if session.is_ebgp else "iBGP"
            multihop = not router.has_link_to(peer)
            src_if = next(
                (i.name for i in router.interfaces.values() if i.ip == side.self_ip), None
            )
            cisco = f"router bgp {router.bgp_engine.asn}\n neighbor {peer_ip} remote-as {remote_as}"
            if multihop and src_if is not None:
                # iBGP already rides TTL 255; only eBGP needs ebgp-multihop to clear it.
                cisco += f"\n neighbor {peer_ip} update-source {src_if}"
                if session.is_ebgp:
                    cisco += f"\n neighbor {peer_ip} ebgp-multihop 255"
            self.clock.record(
                "bgp",
                f"{router.name}→{peer.name} {kind}" + (" (multihop)" if multihop else ""),
                f"`{router.name}` opened an **{kind}** session to "
                f"`{peer.name}` (`{peer_ip}`, AS{remote_as})"
                + (f", multihop via `{src_if}`" if multihop and src_if else ""),
                cisco,
            )

    def _find_session(self, router_a: Router, router_b: Router) -> BGPSession:
        """The BGP session between two routers, or raise if there isn't one."""
        session = next(
            (s for s in self.bgp_sessions.sessions
             if set(s.sides) == {router_a, router_b}),
            None,
        )
        if session is None:
            raise ValueError(
                f"{router_a.name} has no BGP session with {router_b.name}"
            )
        return session

    def destroy_bgp_session(self, router_a: Router, router_b: Router) -> None:
        """Close the BGP session between two routers (`no neighbor`)."""
        session = self._find_session(router_a, router_b)
        self.bgp_sessions.destroy(session)
        for side in session.sides.values():
            self.clock.record(
                "bgp",
                f"{side.router.name}✗{side.peer.name} closed",
                f"`{side.router.name}` closed its BGP session to `{side.peer.name}`",
                f"router bgp {side.router.bgp_engine.asn}\n no neighbor {side.peer_ip}",
            )

    def set_next_hop_self(
        self,
        router: Router,
        neighbor: Router,
        enabled: bool = True,
    ) -> None:
        """Toggle `next-hop-self` on `router`'s session toward `neighbor`.

        This is one-sided only, unlike most of the world commands that
        do the job for both sides.
        """
        side = self._find_session(router, neighbor).view(router)
        side.next_hop_self = enabled
        verb = "set" if enabled else "cleared"
        self.clock.record(
            "bgp",
            f"{router.name}→{neighbor.name} next-hop-self {'on' if enabled else 'off'}",
            f"`{router.name}` {verb} next-hop-self toward `{neighbor.name}`",
            f"router bgp {router.bgp_engine.asn}\n "
            f"{'' if enabled else 'no '}neighbor {side.peer_ip} next-hop-self",
        )

    def set_weight(self, router: Router, neighbor: Router, weight: int | None) -> None:
        """Set or clear the inbound weight on routes from `neighbor`."""
        side = self._find_session(router, neighbor).view(router)
        side.weight_in = weight
        if weight is None:
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} weight cleared",
                f"`{router.name}` cleared the weight on routes from `{neighbor.name}`",
                f"router bgp {router.bgp_engine.asn}\n "
                f"no neighbor {side.peer_ip} weight",
            )
        else:
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} weight {weight}",
                f"`{router.name}` set weight `{weight}` on routes from `{neighbor.name}`",
                f"router bgp {router.bgp_engine.asn}\n "
                f"neighbor {side.peer_ip} weight {weight}",
            )

    def set_local_pref(self, router: Router, neighbor: Router, local_pref: int | None) -> None:
        """Set or clear the inbound local-preference on routes from `neighbor`."""
        side = self._find_session(router, neighbor).view(router)
        side.local_pref_in = local_pref
        rm = f"LP-{neighbor.name}-IN"
        if local_pref is None:
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} local-pref cleared",
                f"`{router.name}` cleared local-pref on routes from `{neighbor.name}`",
                f"router bgp {router.bgp_engine.asn}\n "
                f"no neighbor {side.peer_ip} route-map {rm} in",
            )
        else:
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} local-pref {local_pref}",
                f"`{router.name}` set local-pref `{local_pref}` on routes from `{neighbor.name}`",
                f"route-map {rm} permit 10\n set local-preference {local_pref}\n"
                f"router bgp {router.bgp_engine.asn}\n "
                f"neighbor {side.peer_ip} route-map {rm} in",
            )

    def set_med(self, router: Router, neighbor: Router, med: int | None) -> None:
        """Set or clear the MED `router` advertises to `neighbor`."""
        side = self._find_session(router, neighbor).view(router)
        side.med_out = med
        rm = f"MED-{neighbor.name}-OUT"
        if med is None:
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} MED cleared",
                f"`{router.name}` cleared the MED advertised to `{neighbor.name}`",
                f"router bgp {router.bgp_engine.asn}\n "
                f"no neighbor {side.peer_ip} route-map {rm} out",
            )
        else:
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} MED {med}",
                f"`{router.name}` set MED `{med}` on routes advertised to `{neighbor.name}`",
                f"route-map {rm} permit 10\n set metric {med}\n"
                f"router bgp {router.bgp_engine.asn}\n "
                f"neighbor {side.peer_ip} route-map {rm} out",
            )

    def set_prepend(self, router: Router, neighbor: Router, times: int) -> None:
        """Prepend our own ASN some extra times on routes advertised to `neighbor`.

        Make the as-path look longer, thus less attractive.
        """
        side = self._find_session(router, neighbor).view(router)
        side.prepend_out = times
        asn = router.bgp_engine.asn
        rm = f"PREP-{neighbor.name}-OUT"
        if times <= 0:
            side.prepend_out = 0
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} prepend cleared",
                f"`{router.name}` cleared AS-path prepend toward `{neighbor.name}`",
                f"router bgp {asn}\n no neighbor {side.peer_ip} route-map {rm} out",
            )
        else:
            chain = " ".join([str(asn)] * times)
            self.clock.record(
                "bgp",
                f"{router.name}→{neighbor.name} prepend {times}",
                f"`{router.name}` prepends AS{asn} ×{times} on routes to `{neighbor.name}`",
                f"route-map {rm} permit 10\n set as-path prepend {chain}\n"
                f"router bgp {asn}\n neighbor {side.peer_ip} route-map {rm} out",
            )

    def advertise(self, router: Router, network: IPv4Network) -> None:
        """Originate a prefix into BGP from `router`.

        Only networks that the router knows of are allowed to be advertised.
        """
        if not any(route.network == network for route in router.routing_table.routes):
            raise ValueError(
                f"{router.name} doesn't know this {network}; only advertise a network "
                f"it actually has (a connected, loopback, or static route)"
            )
        router.bgp_engine.advertise_route(network)
        self.clock.record(
            "bgp",
            f"{router.name} originates {network}",
            f"`{router.name}` originated `{network}` into BGP",
            f"router bgp {router.bgp_engine.asn}\n "
            f"network {network.network_address} mask {network.netmask}",
        )

    def withdraw(self, router: Router, network: IPv4Network) -> None:
        """Stop originating a prefix from `router`"""
        router.bgp_engine.withdraw_route(network)
        self.clock.record(
            "bgp",
            f"{router.name} withdraws {network}",
            f"`{router.name}` stopped originating `{network}`",
            f"router bgp {router.bgp_engine.asn}\n "
            f"no network {network.network_address} mask {network.netmask}",
        )

    def add_static_route(self, router: Router, network: IPv4Network, next_hop: IPv4Address) -> None:
        """Install a static route to `network` via `next_hop` on `router`"""
        router.add_static_route(network, next_hop)
        self.clock.record(
            "rib",
            f"{router.name} static {network}",
            f"`{router.name}` installed a static route to `{network}` via `{next_hop}`",
            f"ip route {network.network_address} {network.netmask} {next_hop}",
        )

    def remove_static_route(self, router: Router, network: IPv4Network) -> None:
        """Remove the static route to `network` from `router`"""
        router.remove_static_route(network)
        self.clock.record(
            "rib",
            f"{router.name} no static {network}",
            f"`{router.name}` removed the static route to `{network}`",
            f"no ip route {network.network_address} {network.netmask}",
        )

    def create_link(self, router_a: Router, router_b: Router, cost: int = 10) -> Link:
        """Connect two routers with a cable"""
        link = self.links.create(router_a, router_b, cost)
        for router in (router_a, router_b):
            ifname = router.interface_name(link)
            ip = link.get_ip(router)
            self.clock.record(
                "link",
                f"{router.name} {_short_iface(ifname)} up",
                f"`{router.name}` `{_short_iface(ifname)}` came up at "
                f"`{ip}/{link.network.prefixlen}`",
                f"interface {ifname}\n ip address {ip} {link.network.netmask}\n no shutdown",
            )
        return link

    def cut_link(self, router_a: Router, router_b: Router) -> None:
        """Shark eats cable"""
        link = router_a.get_link_to(router_b)
        link.down()
        for router in (router_a, router_b):
            ifname = router.interface_name(link)
            self.clock.record(
                "link",
                f"{router.name} {_short_iface(ifname)} cut",
                f"`{router.name}` `{_short_iface(ifname)}` went down (cable cut)",
                f"%LINK-3-UPDOWN: Interface {ifname}, changed state to down",
            )

    def repair_link(self, router_a: Router, router_b: Router) -> None:
        """Human fixes cable"""
        link = router_a.get_link_to(router_b)
        link.up()
        for router in (router_a, router_b):
            ifname = router.interface_name(link)
            self.clock.record(
                "link",
                f"{router.name} {_short_iface(ifname)} up",
                f"`{router.name}` `{_short_iface(ifname)}` came back up (cable repaired)",
                f"%LINK-3-UPDOWN: Interface {ifname}, changed state to up",
            )

    def destroy_link(self, router_a: Router, router_b: Router) -> None:
        """Pull the cable between two routers and disconnect them for good

        Different from shutdown(), a soft state change,
        or down() on the link, a broken cable that can be repaired.
        """
        link = router_a.get_link_to(router_b)
        removed = [(router, router.interface_name(link)) for router in (router_a, router_b)]

        self.links.destroy(router_a, router_b)

        for router, ifname in removed:
            self.clock.record(
                "link",
                f"{router.name} {_short_iface(ifname)} removed",
                f"`{router.name}` lost `{_short_iface(ifname)}` (cable pulled)",
                f"%LINK-3-UPDOWN: Interface {ifname}, changed state to down",
            )


class WorldClock:
    """The world's time keeper

    A history book, and prophecy(?) of the world.
    A "tick" is a list of events that occurred in a single timestep.
    """

    def __init__(self):
        self.now:    int              = 0   # recording head: tick that new events get
        self.events: list[WorldEvent] = []  # append-only timeline
        self.cursor: int              = 0   # playback tick, in [0, last_tick]

    def record(
        self,
        category: str,
        title: str,
        summary: str,
        detail: str,
    ) -> WorldEvent:
        """Record a world event.

        Keyword arguments:
        category: one of "sys"/"link"/"bgp"/"upd"/"rib".
        title: a short, plain headline for the narrow timeline header.
        summary: markdown narration, rendered on both surfaces.
        detail: a Cisco-style syslog message or configuration command, using code block.
        """
        event = WorldEvent(category, title, summary, detail, tick=self.now)
        self.events.append(event)
        return event

    @property
    def last_tick(self) -> int:
        """Get the latest event's tick"""
        return self.events[-1].tick if self.events else 0

    @property
    def current_events(self) -> list[WorldEvent]:
        """Events under the current tick (empty when nothing happened this tick)."""
        return [e for e in self.events if e.tick == self.cursor]

    @property
    def cursor_position(self) -> int:
        """The tick the playback cursor is sitting on."""
        return self.cursor

    @property
    def can_rewind(self) -> bool:
        """Is there an earlier tick to rewind to?"""
        return self.cursor > 0

    @property
    def can_advance(self) -> bool:
        """Is there a later tick to advance to?"""
        return self.cursor < self.last_tick

    def advance(self) -> None:
        """Move the cursor to the next tick."""
        if self.cursor < self.last_tick:
            self.cursor += 1

    def rewind(self) -> None:
        """Move the cursor to the previous tick."""
        if self.cursor > 0:
            self.cursor -= 1

    def to_first(self) -> None:
        """Jump the cursor to the earliest tick."""
        self.cursor = 0

    def to_last(self) -> None:
        """Jump the cursor to the latest tick that has events."""
        self.cursor = self.last_tick


class WorldEvent:
    """Events occur in the world

    It can be a link state change, BGP message exchange, etc.

    The two display surfaces (the narrow timeline, the command history) read
    different fields, so an event carries a few structured pieces rather than one
    blob of prose:

    category: a coarse kind ("sys"/"link"/"bgp"/"upd"/"rib") the UI turns into a
        short text tag and uses for grouping.
    title:    a short, plain headline that fits the narrow timeline header.
    summary:  a rich (markdown) one-line narration, rendered on both surfaces.
    detail:   the Cisco-style config/syslog line, rendered inside a code block.
    """

    def __init__(
        self,
        category: str,
        title: str,
        summary: str,
        detail: str,
        tick: int = 0,
    ):
        self.category: str = category
        self.title:    str = title
        self.summary:  str = summary
        self.detail:   str = detail
        self.tick:     int = tick
