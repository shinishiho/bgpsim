from dataclasses import dataclass
from ipaddress import IPv4Address
from itertools import combinations
from typing import TYPE_CHECKING

from .route import BGPRoute

if TYPE_CHECKING:
    from ..router import Router


@dataclass
class BGPPeerInfo:
    """BGP peer information perceived by a router in a BGP session"""

    remote_as: int
    # This router's adj_rib_in is the other's adj_rib_out...
    adj_rib_in: list[BGPRoute]
    adj_rib_out: list[BGPRoute]
    next_hop_self: bool = False


class BGPSession:
    """BGP session, or BGP adjacency between two routers

    A session is identified by the two peer *endpoint* addresses (the BGP
    update-source each router advertises as next-hop), not by a link. This is
    uniform for single-hop and multihop sessions: whether the peers are directly
    adjacent or reached over a routed path is a topology fact, not session state.
    Liveness is driven by reachability of the peer endpoint (see
    BGPSessionManager.update_sessions_state), not by link existence.
    """

    def __init__(
        self,
        router_a: Router,
        router_b: Router,
        source_addr_a: IPv4Address,
        source_addr_b: IPv4Address,
    ):
        self.router_a = router_a
        self.router_b = router_b
        self.endpoint_a = source_addr_a  # address router_a advertises / is reached at
        self.endpoint_b = source_addr_b  # address router_b advertises / is reached at
        self.adj_rib_a: list[BGPRoute] = []  # Router A outgoing routes
        self.adj_rib_b: list[BGPRoute] = []  # Router B outgoing routes
        self.peer_info_a = BGPPeerInfo(
            remote_as=router_b.bgp_engine.asn,
            adj_rib_in=self.adj_rib_b,
            adj_rib_out=self.adj_rib_a
        )
        self.peer_info_b = BGPPeerInfo(
            remote_as=router_a.bgp_engine.asn,
            adj_rib_in=self.adj_rib_a,
            adj_rib_out=self.adj_rib_b
        )
        self.is_ebgp = router_a.bgp_engine.asn != router_b.bgp_engine.asn
        self.is_up: bool = True  # liveness; recomputed via BGPSessionManager.update_sessions_state()
        # self.state = "ESTABLISHED"  # Maybe for FSM if we still have time
        # self.hold_time: int = 180 # Timer is not implemented yet

    def local_endpoint(self, router: Router) -> IPv4Address:
        """The address `router` advertises as next-hop on this session"""
        return self.endpoint_a if router is self.router_a else self.endpoint_b

    def remote_endpoint(self, router: Router) -> IPv4Address:
        """The peer address `router` reaches across this session"""
        return self.endpoint_b if router is self.router_a else self.endpoint_a


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
        session.router_a.bgp_engine.sessions.remove(session)
        session.router_b.bgp_engine.sessions.remove(session)

    def build_ibgp_mesh(self, routers: list[Router]) -> list[BGPSession]:
        """Create iBGP sessions for every pair in `routers` that doesn't have one yet"""
        sessions = []
        for a, b in combinations(routers, 2):
            if not any({s.router_a, s.router_b} == {a, b} for s in self.sessions):
                sessions.append(self.create(a, b))
        return sessions

    def update_sessions_state(self) -> None:
        """Check if sessions are still up, and update

        The state may change due to link state changes
        """
        for session in self.sessions:
            is_still_up = (
                session.router_a.can_reach(session.endpoint_b)
                and session.router_b.can_reach(session.endpoint_a)
            )
            if session.is_up and not is_still_up:
                session.adj_rib_a.clear()
                session.adj_rib_b.clear()
            session.is_up = is_still_up
