from dataclasses import dataclass
from ipaddress import IPv4Address
from itertools import combinations
from typing import TYPE_CHECKING

from .route import BGPRoute

if TYPE_CHECKING:
    from ..router import Router
    from ..world import WorldClock


@dataclass
class BGPPeerInfo:
    """One router's view of its side of a BGP session"""

    router: Router
    peer: Router
    remote_as: int
    self_ip: IPv4Address  # the address this router advertises / is reached at
    peer_ip: IPv4Address  # the address of the peer
    adj_rib_in: list[BGPRoute]
    adj_rib_out: list[BGPRoute]
    next_hop_self: bool = False


class BGPSession:
    """BGP session, or BGP adjacency between two routers"""

    def __init__(
        self,
        router_a: Router,
        router_b: Router,
        source_addr_a: IPv4Address,
        source_addr_b: IPv4Address,
    ):
        self.adj_rib_a: list[BGPRoute] = []  # router_a outgoing routes
        self.adj_rib_b: list[BGPRoute] = []  # router_b outgoing routes
        self.sides: dict[Router, BGPPeerInfo] = {
            router_a: BGPPeerInfo(
                router=router_a,
                peer=router_b,
                remote_as=router_b.bgp_engine.asn,
                self_ip=source_addr_a,
                peer_ip=source_addr_b,
                adj_rib_in=self.adj_rib_b,
                adj_rib_out=self.adj_rib_a,
            ),
            router_b: BGPPeerInfo(
                router=router_b,
                peer=router_a,
                remote_as=router_a.bgp_engine.asn,
                self_ip=source_addr_b,
                peer_ip=source_addr_a,
                adj_rib_in=self.adj_rib_a,
                adj_rib_out=self.adj_rib_b,
            ),
        }
        self.is_ebgp = router_a.bgp_engine.asn != router_b.bgp_engine.asn
        self.is_up: bool = True
        # self.state = "ESTABLISHED"  # Maybe for FSM if we still have time
        # self.hold_time: int = 180 # Timer is not implemented yet

    def view(self, router: Router) -> BGPPeerInfo:
        """This router's side of the session (its view of the peer)"""
        try:
            return self.sides[router]
        except KeyError:
            raise ValueError(f"{router.name} is not in this session")


class BGPSessionManager:
    """Manages BGP sessions of the world"""
    
    def __init__(self):
        self.sessions: list[BGPSession] = []

    def create(
        self,
        router_a: Router,
        router_b: Router,
        source_addr_a: IPv4Address | None = None,
        source_addr_b: IPv4Address | None = None,
    ) -> BGPSession:
        """Create a BGP session between two routers

        In single-hop BGP sessions, the endpoints are the direct link addresses.
        In multihop BGP sessions, the endpoints must be specified explicitly.
        """

        if (source_addr_a is None) != (source_addr_b is None):
            raise ValueError("provide both source_a and source_b, or neither")

        if source_addr_a is None: # Which means source_b is also None
            # Get address from the direct link
            link = router_a.get_link_to(router_b)
            source_addr_a = link.get_ip(router_a)
            source_addr_b = link.get_ip(router_b)

        session = BGPSession(router_a, router_b, source_addr_a=source_addr_a, source_addr_b=source_addr_b) # type: ignore
        self.sessions.append(session)

        router_a.bgp_engine.sessions.append(session)
        router_b.bgp_engine.sessions.append(session)
        return session

    def destroy(self, session: BGPSession) -> None:
        """Destroy a BGP session"""
        self.sessions.remove(session)
        for router in session.sides:
            router.bgp_engine.sessions.remove(session)

    def build_ibgp_mesh(self, routers: list[Router]) -> list[BGPSession]:
        """Create iBGP sessions for every pair in `routers` that doesn't have one yet"""
        sessions = []
        for a, b in combinations(routers, 2):
            if not any(set(s.sides) == {a, b} for s in self.sessions):
                sessions.append(self.create(a, b))
        return sessions

    def update_sessions_state(self, clock: WorldClock | None = None) -> None:
        """Evaluate if peers are reachable

        A session is up if the peer endpoint is reachable.
        If a clock is supplied, record session up/down transitions to the timeline.
        """
        for session in self.sessions:
            is_still_up = all(
                side.router.can_reach(side.peer_ip) for side in session.sides.values()
            )
            was_up = session.is_up
            if was_up and not is_still_up:
                session.adj_rib_a.clear()
                session.adj_rib_b.clear()
            session.is_up = is_still_up

            if clock is not None and was_up != is_still_up:
                self._record_adjacency(session, is_up=is_still_up, clock=clock)

    def _record_adjacency(self, session: BGPSession, is_up: bool, clock: WorldClock) -> None:
        """Emits world events for session up/down transitions"""
        for side in session.sides.values():
            router, peer, peer_ip = side.router, side.peer, side.peer_ip
            if is_up:
                clock.record(
                    f"{router.name}'s session with {peer.name} came up",
                    f"%BGP-5-ADJCHANGE: neighbor {peer_ip} Up",
                )
            else:
                clock.record(
                    f"{router.name}'s session with {peer.name} went down",
                    f"%BGP-5-ADJCHANGE: neighbor {peer_ip} Down Peer unreachable",
                )
