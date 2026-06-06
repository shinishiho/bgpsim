import copy
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network

from .session import BGPSession
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
        clock: "WorldClock | None" = None,
    ) -> None:
        """Run one calculation pass

        When a clock is supplied, every change to loc_rib (a route learned, its
        best path changed, or a route lost) is recorded onto the timeline. With no
        clock the pass is silent and behaves exactly as before.
        """
        old_loc_rib = self.loc_rib

        # Receive routes
        processing: dict[IPv4Network, list[BGPRoute]] = {}

        for session in self.sessions:
            if not session.is_up:
                continue # Skip down session

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

        # Advertise best routes to peers
        for session in self.sessions:
            peer_info = session.peer_info_a if self.router is session.router_a else session.peer_info_b
            peer_info.adj_rib_out.clear()

            if not session.is_up:
                continue # Skip down session

            for route in self.loc_rib.values():
                # iBGP split-horizon
                if route.source.type == BGPRouteSourceType.IBGP and not session.is_ebgp:
                    continue

                # Bruh python...
                out_route = copy.deepcopy(route)

                if session.is_ebgp:
                    out_route.as_path = [self.asn] + out_route.as_path
                    out_route.next_hop = session.local_endpoint(self.router)
                elif peer_info.next_hop_self or route.source.type is BGPRouteSourceType.LOCAL:
                    out_route.next_hop = session.local_endpoint(self.router)

                peer_info.adj_rib_out.append(out_route)

        # After picking the best ones, let's install to the FIB
        self.refresh_fib()

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
