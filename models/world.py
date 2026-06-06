from typing import TYPE_CHECKING
from ipaddress import IPv4Network

from .router import RouterManager
from .link import LinkManager
from .bgp.session import BGPSessionManager

if TYPE_CHECKING:
    from ipaddress import IPv4Address
    from .router import Router
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

        Advance the world clock, then update BGP sessions and engines.
 .      Returns the list of events that occurred during this tick.
        # TODO: Hop propagation delay
        """
        self.clock.now += 1
        before = len(self.clock.events)

        self.bgp_sessions.update_sessions_state(clock=self.clock)

        for router in self.routers.routers:
            router.bgp_engine.update(clock=self.clock)

        return self.clock.events[before:]

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
        """Connect two routers with a cable (or add a loopback when a is b)"""
        link = self.links.create(router_a, router_b, cost)
        endpoints = [router_a] if router_a is router_b else [router_a, router_b]
        for router in endpoints:
            ifname = router.interface_name(link)
            ip = link.get_ip(router)
            self.clock.record(
                f"{router.name}'s {ifname} came up at {ip}/{link.network.prefixlen}",
                f"interface {ifname}\n ip address {ip} {link.network.netmask}\n no shutdown",
            )
        return link

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

    def destroy_link(self, router_a: Router, router_b: Router) -> None:
        """Take a link down and record the interface shutdowns.

        # TODO: Interface-centric
        """
        link = router_a.get_link_to(router_b)
        endpoints = [router_a] if router_a is router_b else [router_a, router_b]
        shutdowns = [(router, router.interface_name(link)) for router in endpoints]

        self.links.destroy(router_a, router_b)

        for router, ifname in shutdowns:
            self.clock.record(
                f"{router.name} shut down {ifname}",
                f"interface {ifname}\n shutdown",
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
