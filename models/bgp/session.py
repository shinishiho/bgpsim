from dataclasses import dataclass
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
    """BGP session, or BGP adjacency between two routers"""

    def __init__(
        self,
        router_a: Router,
        router_b: Router,
    ):
        self.router_a = router_a
        self.router_b = router_b
        self.link = router_a.get_link_to(router_b) # No multihop BGP
        self.adj_rib_a = list[BGPRoute]() # Router A outgoing routes
        self.adj_rib_b = list[BGPRoute]() # Router B outgoing routes
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
        # self.state = "ESTABLISHED"  # Maybe for FSM if we still have time
        # self.hold_time: int = 180 # Timer is not implemented yet


class BGPSessionManager:
    """Manages BGP sessions of the world"""
    
    def __init__(self):
        self.sessions: list[BGPSession] = []

    def create(
        self,
        router_a: Router,
        router_b: Router
    ) -> BGPSession:
        """Create a BGP session between two routers"""

        # TODO: any pre-check?
        session = BGPSession(router_a, router_b)
        self.sessions.append(session)

        router_a.bgp_engine.sessions.append(session)
        router_b.bgp_engine.sessions.append(session)
        return session

    def destroy(self, session: BGPSession) -> None:
        """Destroy a BGP session"""

        self.sessions.remove(session)
        session.router_a.bgp_engine.sessions.remove(session)
        session.router_b.bgp_engine.sessions.remove(session)

        # Clean up routes learned from this session
        for router in (session.router_a, session.router_b):
            stale = [
                prefix for prefix, route in router.bgp_engine.loc_rib.items()
                if route.source.session is session
            ]
            for prefix in stale:
                del router.bgp_engine.loc_rib[prefix]