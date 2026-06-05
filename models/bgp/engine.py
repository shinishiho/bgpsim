import copy
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network

from .session import BGPSession
from .route import BGPRoute, BGPRouteSource, BGPRouteSourceType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..router import Router

@dataclass
class BGPEngine:
    """Each router holds a BGP engine

    It holds basic configuration, sessions, handles logic, execution
    """

    router: Router # Back reference
    asn: int = 1
    sessions: list[BGPSession] = field(default_factory=list)
    loc_rib: dict[IPv4Network, BGPRoute] = field(default_factory=dict)
    # hold_time: int = 180 # Timer is not implemented yet
    # import/export policy?

    def calculate_best_route(
        self,
        candidates: list[BGPRoute]
    ) -> BGPRoute:
        """Returns the best route from a list of routes

        Using standard, Cisco-flavored decision-making process:
        highest weight
        → highest local_pref
        → locally originated
        → shortest AS path
        → lowest origin
        → lowest MED
        → eBGP over iBGP
        → lowest IGP metric
        → lowest BGP router-id
        """

        candidates.sort(
            key=lambda route: (
                route.weight,
                route.local_pref,
                route.source.type == BGPRouteSourceType.LOCAL,
                -len(route.as_path),
                # lowest origin?
                -route.med if route.med is not None else 0,
                # igp metric?
                # lowest bgp router-id?
            ),
            reverse=True
        )

        return candidates[0]
    
    def advertise_route(
        self,
        network: IPv4Network
    ) -> None:
        """Inject a network into BGP, globally
        
        Equivalent Cisco command:
        `network <network> mask <mask>`
        """
        self.loc_rib[network] = BGPRoute(
            prefix=network,
            next_hop=IPv4Address("0.0.0.0"), # TODO: advertise network learned somewhere else
            source=BGPRouteSource(type=BGPRouteSourceType.LOCAL)
        )

    def withdraw_route(
        self,
        network: IPv4Network
    ) -> None:
        """Withdraw a network from BGP"""
        self.loc_rib.pop(network, None)

    def update(
        self
    ) -> None:
        """Run one calculation pass"""
        # Receive routes
        processing: dict[IPv4Network, list[BGPRoute]] = {}
        for session in self.sessions:
            peer_info = session.peer_info_a if self.router is session.router_a else session.peer_info_b
            for route in peer_info.adj_rib_in:
                route = copy.deepcopy(route)
                # Get some filtering and policies
                if self.asn in route.as_path:
                    continue # Loop prevention
                if route.prefix not in processing:
                    processing[route.prefix] = []
                if session.is_ebgp:
                    route.source = BGPRouteSource(
                        type=BGPRouteSourceType.EBGP,
                        session=session,
                        router=session.router_b if self.router is session.router_a else session.router_a
                    )
                else:
                    route.source = BGPRouteSource(
                        type=BGPRouteSourceType.IBGP,
                        session=session,
                        router=session.router_b if self.router is session.router_a else session.router_a
                    )
                processing[route.prefix].append(route)

        # Pick the best route for each prefix
        for prefix, candidates in processing.items():
            if prefix in self.loc_rib:
                candidates.append(self.loc_rib[prefix])
            best_route = copy.deepcopy(self.calculate_best_route(candidates))
            self.loc_rib[prefix] = best_route

        # Advertise best routes to peers
        for session in self.sessions:
            peer_info = session.peer_info_a if self.router is session.router_a else session.peer_info_b
            peer_info.adj_rib_out.clear()
            for route in self.loc_rib.values():
                # iBGP split-horizon: don't re-advertise iBGP-learned routes to other iBGP peers
                if route.source.type == BGPRouteSourceType.IBGP and not session.is_ebgp:
                    continue
                
                # Bruh python...
                out_route = copy.deepcopy(route)
                
                if session.is_ebgp:
                    out_route.as_path = [self.asn] + out_route.as_path
                    out_route.next_hop = session.link.get_ip(self.router)
                elif peer_info.next_hop_self:
                    # iBGP with next-hop-self
                    out_route.next_hop = session.link.get_ip(self.router)
                
                peer_info.adj_rib_out.append(out_route)