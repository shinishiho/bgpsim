from typing import TYPE_CHECKING
from ipaddress import IPv4Network

from .router import RouterManager
from .link import LinkManager
from .bgp.session import BGPSessionManager

if TYPE_CHECKING:
    from ipaddress import IPv4Address
    from .router import Router, Interface
    from .link import Link
    from .bgp.session import BGPSession


class World:
    """The simulator world

    It holds everything: routers, links, BGP sessions, timers.
    I'm considering if prefixes and policies should be handled by the world or localized.
    """

    def __init__(self):
        self.routers:      RouterManager     = RouterManager()
        self.links:        LinkManager       = LinkManager()
        self.bgp_sessions: BGPSessionManager = BGPSessionManager()
        self.clock:        WorldClock        = WorldClock()

    def tick(self) -> list[WorldEvent]:
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

        return self.clock.events[before:]

    def converge(self, max_ticks: int = 256) -> int:
        """Tick continuously until no more updates. Return the number of ticks elapsed."""
        for ticks in range(1, max_ticks + 1):
            if not self.tick():
                return ticks - 1  # the last (empty) tick did no work
        raise RuntimeError(f"No convergence within {max_ticks} ticks")

    def build_ibgp_mesh(self, asn: int = 1) -> list:
        """Create a full iBGP mesh for every router in `asn`.

        Get all routers in `asn` and send them to the BGPSessionManager
        """
        routers = [r for r in self.routers.routers if r.bgp_engine.asn == asn]
        sessions = self.bgp_sessions.build_ibgp_mesh(routers)
        for session in sessions:
            self._record_session(session)
        return sessions

    def add_router(self, asn: int = 1) -> Router:
        """Add a router to the world"""
        router = self.routers.create()
        router.bgp_engine.asn = asn
        self.clock.record(
            f"{router.name} came online in AS{asn}",
            f"hostname {router.name}\n!\nrouter bgp {asn}",
        )
        return router

    def connect(self, router_a: Router, router_b: Router, cost: int = 10) -> Link:
        """Connect two routers with a cable"""
        link = self.links.create(router_a, router_b, cost)
        for router in (router_a, router_b):
            ifname = router.interface_name(link)
            ip = link.get_ip(router)
            self.clock.record(
                f"{router.name}'s {ifname} came up at {ip}/{link.network.prefixlen}",
                f"interface {ifname}\n ip address {ip} {link.network.netmask}\n no shutdown",
            )
        return link

    def add_loopback(self, router: Router) -> "Interface":
        """Add a loopback interface to `router` (a /32 from the loopback pool)"""
        network = self.links.alloc_loopback()
        iface = router.add_loopback(network)
        self.clock.record(
            f"{router.name}'s {iface.name} came up at {iface.ip}/32",
            f"interface {iface.name}\n ip address {iface.ip} {network.netmask}\n no shutdown",
        )
        return iface

    def shutdown(self, router: Router, peer: Router) -> None:
        """Router's interface admin-down

        Note: by Cisco, it should take the interface name as argument, but here we
        specify two routers for convenience, the function will find the interface between them.
        """
        link = router.get_link_to(peer)
        ifname = router.interface_name(link)
        router.interfaces[ifname].shutdown()
        self.clock.record(
            f"{router.name} shut down {ifname}",
            f"interface {ifname}\n shutdown",
        )

    def no_shutdown(self, router: Router, peer: Router) -> None:
        """Router's interface admin-up"""
        link = router.get_link_to(peer)
        ifname = router.interface_name(link)
        router.interfaces[ifname].no_shutdown()
        self.clock.record(
            f"{router.name} brought up {ifname}",
            f"interface {ifname}\n no shutdown",
        )

    def peer(
        self,
        router_a: Router,
        router_b: Router,
        source_addr_a: IPv4Address | None = None,
        source_addr_b: IPv4Address | None = None,
    ) -> BGPSession:
        """Create a BGP session between two routers"""
        session = self.bgp_sessions.create(router_a, router_b, source_addr_a, source_addr_b)
        self._record_session(session)
        return session

    def advertise(self, router: Router, network: IPv4Network) -> None:
        """Originate a prefix into BGP from `router`"""
        router.bgp_engine.advertise_route(network)
        self.clock.record(
            f"{router.name} advertised {network} into BGP",
            f"router bgp {router.bgp_engine.asn}\n "
            f"network {network.network_address} mask {network.netmask}",
        )

    def withdraw(self, router: Router, network: IPv4Network) -> None:
        """Stop originating a prefix from `router`"""
        router.bgp_engine.withdraw_route(network)
        self.clock.record(
            f"{router.name} withdrew {network} from BGP",
            f"router bgp {router.bgp_engine.asn}\n "
            f"no network {network.network_address} mask {network.netmask}",
        )

    def cut_link(self, router_a: Router, router_b: Router) -> None:
        """Shark eats cable"""
        link = router_a.get_link_to(router_b)
        link.down()
        for router in (router_a, router_b):
            ifname = router.interface_name(link)
            self.clock.record(
                f"{router.name}'s {ifname} went down (cable cut)",
                f"%LINK-3-UPDOWN: Interface {ifname}, changed state to down",
            )

    def repair_link(self, router_a: Router, router_b: Router) -> None:
        """Human fixes cable"""
        link = router_a.get_link_to(router_b)
        link.up()
        for router in (router_a, router_b):
            ifname = router.interface_name(link)
            self.clock.record(
                f"{router.name}'s {ifname} came back up (cable repaired)",
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
                f"{router.name} lost {ifname} (cable pulled)",
                f"%LINK-3-UPDOWN: Interface {ifname}, changed state to down",
            )

    def _record_session(self, session: BGPSession) -> None:
        """Record a world event for BGP session creation"""
        for router in (session.router_a, session.router_b):
            peer = session.router_b if router is session.router_a else session.router_a
            remote_as = peer.bgp_engine.asn
            peer_ip = session.remote_endpoint(router)
            kind = "eBGP" if session.is_ebgp else "iBGP"
            self.clock.record(
                f"{router.name} opened an {kind} session to {peer.name} ({peer_ip}, AS{remote_as})",
                f"router bgp {router.bgp_engine.asn}\n neighbor {peer_ip} remote-as {remote_as}",
            )


class WorldClock:
    """The world's time keeper

    A history book, and prophecy(?) of the world.
    """

    def __init__(self):
        self.now:    int              = 0   # recording head: tick that new events get
        self.events: list[WorldEvent] = []  # append-only timeline
        self.cursor: int              = -1  # playback position; -1 = before first event

    def record(self, english_message: str, technical_message: str) -> "WorldEvent":
        """Record a world event
        
        Keyword arguments:
        english_message: a natural lanaguage description of the event, for narration purposes
        technical_message: a Cisco-style syslog message, or configuration command
        """
        event = WorldEvent(english_message, technical_message, tick=self.now)
        self.events.append(event)
        return event

    @property
    def current(self) -> WorldEvent | None:
        """The event under the playback cursor, or None"""
        return self.events[self.cursor] if 0 <= self.cursor < len(self.events) else None

    def advance(self) -> WorldEvent | None:
        """Return the next event in the timeline and advance the cursor"""
        if self.cursor >= len(self.events) - 1:
            # If we are at the end of the world
            print("This is the end...\nHold your breath and count... to ten...")
            return None

        self.cursor += 1
        return self.events[self.cursor]

    def rewind(self) -> WorldEvent | None:
        """Return an event in the past, not touching the timeline"""
        if self.cursor <= 0:
            return None

        self.cursor -= 1
        return self.events[self.cursor]


class WorldEvent:
    """Events occur in the world

    It can be a link state change, BGP message exchange, etc.
    """

    def __init__(
        self,
        english_message: str,
        technical_message: str,
        tick: int = 0,
    ):
        self.english_message:   str = english_message
        self.technical_message: str = technical_message
        self.tick:              int = tick
