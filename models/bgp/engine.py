import copy
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network

from .session import BGPSession, BGPPeerInfo
from .route import BGPRoute, BGPRouteSource, BGPRouteSourceType
from ..routing_table import Route, RouteType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..router import Router
    from ..world import WorldClock

@dataclass
class BGPEngine:
    """Each router holds a BGP engine

    It holds basic configuration, sessions, handles logic, execution
    """

    router: Router # Back reference
    asn: int = 1
    sessions: list[BGPSession] = field(default_factory=list)
    loc_rib: dict[IPv4Network, BGPRoute] = field(default_factory=dict)
    # Routes advertised manually
    manual_rib: dict[IPv4Network, BGPRoute] = field(default_factory=dict)
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
                # origin: no IGP in this sim, so every route is IGP-origin
                -(route.med if route.med is not None else 0),    # lowest MED (missing = 0)
                route.source.type == BGPRouteSourceType.EBGP,    # eBGP over iBGP
                # IGP metric to next-hop: # TODO: use link cost?
                -int(self._advertiser_router_id(route)),         # lowest router-id
            ),
            reverse=True
        )

        return candidates[0]

    def _advertiser_router_id(self, route: BGPRoute) -> IPv4Address:
        """Router-id of the peer that advertised `route` (self for LOCAL routes)."""
        router = route.source.router or self.router
        return router.router_id

    def advertise_route(
        self,
        network: IPv4Network
    ) -> None:
        """Inject a network into BGP, globally
        
        Equivalent Cisco command:
        `network <network> mask <mask>`
        """
        route = BGPRoute(
            prefix=network,
            next_hop=IPv4Address("0.0.0.0"),
            source=BGPRouteSource(type=BGPRouteSourceType.LOCAL)
        )
        self.loc_rib[network] = route
        self.manual_rib[network] = copy.deepcopy(route)

    def withdraw_route(
        self,
        network: IPv4Network
    ) -> None:
        """Withdraw a network from BGP"""
        self.manual_rib.pop(network, None)
        self.loc_rib.pop(network, None)

    def update(
        self,
        clock: WorldClock | None = None,
    ) -> None:
        """TODO: check and clean up
        """
        self.compute(clock)
        self.commit(clock)

    def compute(
        self,
        clock: WorldClock | None = None,
    ) -> None:
        """Phase 1: compute loc_rib + FIB and prepare a list of routes to advertise to all peers

        When a clock is supplied, loc_rib changes are recorded onto the timeline.
        """
        old_loc_rib = self.loc_rib

        # Receive routes
        processing: dict[IPv4Network, list[BGPRoute]] = {}

        for session in self.sessions:
            if not session.is_up:
                continue # Skip down session

            side = session.view(self.router)
            for route in side.adj_rib_in:
                route = copy.deepcopy(route)
                # Get some filtering and policies
                if self.asn in route.as_path:
                    continue # Loop prevention

                if route.prefix not in processing:
                    processing[route.prefix] = []

                route.source = BGPRouteSource(
                    type=BGPRouteSourceType.EBGP if session.is_ebgp else BGPRouteSourceType.IBGP,
                    session=session,
                    router=side.peer,
                )

                processing[route.prefix].append(route)

        # Rebuild loc_rib
        new_loc_rib: dict[IPv4Network, BGPRoute] = {}
        for prefix, candidates in processing.items():
            # Manually advertised routes might get beaten by learned routes (which then vanish),
            # so we need to consider them again
            if prefix in self.manual_rib:
                candidates.append(copy.deepcopy(self.manual_rib[prefix]))
            new_loc_rib[prefix] = self.calculate_best_route(candidates)

        # And then we add manually advertised routes again
        for prefix, route in self.manual_rib.items():
            if prefix not in new_loc_rib:
                new_loc_rib[prefix] = copy.deepcopy(route)

        self.loc_rib = new_loc_rib

        if clock is not None:
            self._record_rib_changes(old_loc_rib, new_loc_rib, clock)

        for session in self.sessions:
            side = session.view(self.router)

            out_routes: list[BGPRoute] = []
            if session.is_up:
                for route in self.loc_rib.values():
                    # iBGP split-horizon
                    if route.source.type == BGPRouteSourceType.IBGP and not session.is_ebgp:
                        continue

                    # Bruh python...
                    out_route = copy.deepcopy(route)

                    if session.is_ebgp:
                        out_route.as_path = [self.asn] + out_route.as_path
                        out_route.next_hop = side.self_ip
                    elif side.next_hop_self or route.source.type is BGPRouteSourceType.LOCAL:
                        out_route.next_hop = side.self_ip

                    out_routes.append(out_route)

            side.pending_out = out_routes

        # After picking the best ones, let's install to the FIB
        self.refresh_fib()

    def commit(
        self,
        clock: WorldClock | None = None,
    ) -> None:
        """Phase 2: publish the staged advertisements to peers

        Replaces each peer's adj_rib_out contents in place (preserving the list
        object the peer reads as its adj_rib_in). When a clock is supplied, every
        change to what we advertise is recorded as a BGP UPDATE event.
        """
        for session in self.sessions:
            side = session.view(self.router)
            if clock is not None:
                self._record_bgp_update(side, side.pending_out, clock)
            side.adj_rib_out[:] = side.pending_out

    def _record_bgp_update(
        self,
        side: BGPPeerInfo,
        new_rib_out: list[BGPRoute],
        clock: WorldClock,
    ) -> None:
        """Narrate the BGP UPDATE this router sends a peer when adj_rib_out changes.

        Check every prefix attribute, and only record the change if there's a difference.
        """
        def by_prefix(routes: list[BGPRoute]) -> dict:
            return {r.prefix: (r.next_hop, tuple(r.as_path), r.local_pref, r.med, r.weight)
                    for r in routes}

        old_rib = by_prefix(side.adj_rib_out)
        new_rib = by_prefix(new_rib_out)

        peer = side.peer
        peer_ip = side.peer_ip
        name = self.router.name

        for prefix, attributes in new_rib.items():
            if prefix not in old_rib or old_rib[prefix] != attributes:
                clock.record(
                    f"{name} sent an UPDATE to {peer.name} ({prefix})",
                    f"%BGP-6-UPDATE: neighbor {peer_ip} sent prefix {prefix}",
                )
        for prefix in old_rib:
            if prefix not in new_rib:
                clock.record(
                    f"{name} withdrew {prefix} from {peer.name}",
                    f"%BGP-6-UPDATE: neighbor {peer_ip} withdrew prefix {prefix}",
                )

    def _record_rib_changes(
        self,
        old_loc_rib: dict[IPv4Network, BGPRoute],
        new_loc_rib: dict[IPv4Network, BGPRoute],
        clock: "WorldClock",
    ) -> None:
        """Narrate how this pass changed loc_rib onto the timeline.

        Routes are compared by best-path identity, so a re-selected path with the
        same attributes is (correctly) not reported as a change.
        """
        def identity(route: BGPRoute):
            return (route.next_hop, tuple(route.as_path), route.local_pref, route.weight)

        def detail(route: BGPRoute) -> str:
            return (f"next_hop={route.next_hop} as_path={route.as_path} "
                    f"local_pref={route.local_pref} weight={route.weight}")

        name = self.router.name
        for prefix, route in new_loc_rib.items():
            if prefix not in old_loc_rib:
                clock.record(
                    f"{name} learned a route to {prefix}",
                    f"loc_rib[{prefix}] += {detail(route)}",
                )
            elif identity(route) != identity(old_loc_rib[prefix]):
                clock.record(
                    f"{name} changed its best path to {prefix}",
                    f"loc_rib[{prefix}] = {detail(route)}",
                )

        for prefix in old_loc_rib:
            if prefix not in new_loc_rib:
                clock.record(
                    f"{name} lost its route to {prefix}",
                    f"loc_rib[{prefix}] removed",
                )

    def _find_egress_interface(self, next_hop: IPv4Address):
        """Find the egress interface to reach the next hop

        Recursively look up the next hop in the routing table, until a directly
        connected interface is found. Return None if not found or the route is not
        valid (e.g. the next hop is not reachable).

        Keyword arguments:
        next_hop: the IP address of the next hop
        """
        addr = next_hop
        seen: set[IPv4Address] = set()
        while addr not in seen:
            seen.add(addr)
            # Base case: directly connected
            for iface in self.router.interfaces.values():
                if iface.link is not None and addr in iface.link.network:
                    return iface
            # Find the next hop in the underlay routes
            entry = self.router.routing_table.lookup(addr, exclude_route_type=RouteType.BGP)
            if entry is None or entry.next_hop is None:
                return None
            addr = entry.next_hop
        return None # Round and round we go

    def refresh_fib(self) -> None:
        """Refresh the FIB according to the current loc_rib"""
        self.router.routing_table.remove_by_type(RouteType.BGP)

        for prefix, route in self.loc_rib.items():
            if route.source.type is BGPRouteSourceType.LOCAL:
                continue
            iface = self._find_egress_interface(route.next_hop)
            if iface is None: # Unreachable
                continue
            self.router.routing_table.add(
                Route(
                    network=prefix,
                    interface=iface,
                    next_hop=route.next_hop,
                    route_type=RouteType.BGP,
                )
            )
