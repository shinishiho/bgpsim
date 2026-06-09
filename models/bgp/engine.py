import copy
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network

from .session import BGPSession, BGPPeerInfo
from .route import BGPRoute, BGPRouteSource, BGPRouteSourceType
from ..routing_table import Route, RouteType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..router import Router, Interface
    from ..world import WorldClock


# Cisco defaults
DEFAULT_LOCAL_PREF = 100
LOCAL_ORIGIN_WEIGHT = 32768


def _path_attrs(next_hop, as_path, local_pref=None, med=None) -> str:
    """Print path attributes. For example:

    >>> _path_attrs("192.168.0.1", [65002, 65003])
    'via `192.168.0.1`, as-path `65002 65003`'
    >>> _path_attrs("0.0.0.0", [], local_pref=100, med=50)
    'via `0.0.0.0`, as-path `i`, local-pref 100, MED 50'
    """
    path = " ".join(str(a) for a in as_path) or "i"
    parts = [f"via `{next_hop}`", f"as-path `{path}`"]
    if local_pref is not None:
        parts.append(f"local-pref {local_pref}")
    if med is not None:
        parts.append(f"MED {med}")
    return ", ".join(parts)


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
    # Rejected routes, for logging purposes
    # Key: (router name, prefix)
    # Value: (peer ip, as-path)
    rejected_in: dict[tuple[str, IPv4Network], tuple[IPv4Address, tuple[int, ...]]] = field(
        default_factory=dict
    )
    # Routes in RIB but not installed to FIB, due to unreachability
    # Key: prefix.
    # Value: (next_hop, advertiser name or None)
    fib_unreachable: dict[IPv4Network, tuple[IPv4Address, str | None]] = field(
        default_factory=dict
    )
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

        MED is only comparable between paths learned from the *same* neighboring
        AS (the default; `bgp always-compare-med` is off). We model this with
        deterministic MED: group candidates by neighbor AS, pick the best of each
        group (MED counts there), then compare the group winners with MED ignored.
        """
        groups: dict[int | None, list[BGPRoute]] = {}
        for route in candidates:
            groups.setdefault(self._neighbor_as(route), []).append(route)

        winners = [
            max(group, key=lambda r: self._decision_key(r, with_med=True))
            for group in groups.values()
        ]
        return max(winners, key=lambda r: self._decision_key(r, with_med=False))

    def _neighbor_as(self, route: BGPRoute) -> int | None:
        """The AS this route was learned from.

        Because ASN is appended at the beginning, so we get the first one in as_path.
        Or None if it's internal.
        """
        return route.as_path[0] if route.as_path else None

    def _decision_key(self, route: BGPRoute, *, with_med: bool) -> tuple:
        """Best-path sort key (larger is better). MED is only included within a
        neighbor-AS group, so keys built with differing `with_med` must not be mixed.
        """
        key: list = [
            route.weight,
            route.local_pref,
            route.source.type == BGPRouteSourceType.LOCAL,
            -len(route.as_path),
            # origin: no IGP in this sim, so every route is IGP-origin
        ]
        if with_med:
            key.append(-(route.med if route.med is not None else 0))  # lowest MED (missing = 0)
        key += [
            route.source.type == BGPRouteSourceType.EBGP,    # eBGP over iBGP
            # IGP metric to next-hop: # TODO: use link cost?
            -int(self._advertiser_router_id(route)),         # lowest router-id
        ]
        return tuple(key)

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
            source=BGPRouteSource(type=BGPRouteSourceType.LOCAL),
            weight=LOCAL_ORIGIN_WEIGHT,
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

    def compute(
        self,
        clock: WorldClock | None = None,
    ) -> None:
        """Phase 1: compute loc_rib + FIB and prepare a list of routes to advertise to all peers

        When a clock is supplied, loc_rib changes are recorded onto the timeline.
        """
        old_loc_rib = self.loc_rib
        old_rejected = self.rejected_in
        new_rejected: dict[tuple[str, IPv4Network], tuple[IPv4Address, tuple[int, ...]]] = {}

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
                    # Loop prevention
                    new_rejected[(side.peer.name, route.prefix)] = (
                        side.peer_ip, tuple(route.as_path)
                    )
                    continue

                if route.prefix not in processing:
                    processing[route.prefix] = []

                route.source = BGPRouteSource(
                    type=BGPRouteSourceType.EBGP if session.is_ebgp else BGPRouteSourceType.IBGP,
                    session=session,
                    router=side.peer,
                )

                # Cisco proprietary, local only
                route.weight = 0
                if session.is_ebgp:
                    # Drop local_pref if the route is being sent via eBGP
                    route.local_pref = DEFAULT_LOCAL_PREF

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
            self._record_rejections(old_rejected, new_rejected, clock)
        self.rejected_in = new_rejected

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
                        # MED only propagate one hop, so if as_path has something
                        # (it has gone through one hop), drop MED
                        if out_route.as_path:
                            out_route.med = None
                        out_route.as_path = [self.asn] + out_route.as_path
                        out_route.next_hop = side.self_ip
                    elif side.next_hop_self or route.source.type is BGPRouteSourceType.LOCAL:
                        out_route.next_hop = side.self_ip

                    out_routes.append(out_route)

            side.pending_out = out_routes

        # After picking the best ones, let's install to the FIB
        self.refresh_fib(clock)

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
                next_hop, as_path = attributes[0], attributes[1]
                clock.record(
                    "upd",
                    f"{name}→{peer.name} {prefix}",
                    f"`{name}` advertised `{prefix}` to `{peer.name}` — "
                    f"{_path_attrs(next_hop, as_path)}",
                    f"%BGP-6-UPDATE: neighbor {peer_ip} sent prefix {prefix}",
                )
        for prefix in old_rib:
            if prefix not in new_rib:
                clock.record(
                    "upd",
                    f"{name}→{peer.name} withdraw {prefix}",
                    f"`{name}` withdrew `{prefix}` from `{peer.name}`",
                    f"%BGP-6-UPDATE: neighbor {peer_ip} withdrew prefix {prefix}",
                )

    def _record_rib_changes(
        self,
        old_loc_rib: dict[IPv4Network, BGPRoute],
        new_loc_rib: dict[IPv4Network, BGPRoute],
        clock: WorldClock,
    ) -> None:
        """Narrate how this pass changed loc_rib onto the timeline.

        Routes are compared by best-path identity, so a re-selected path with the
        same attributes is (correctly) not reported as a change.
        """
        def identity(route: BGPRoute):
            return (route.next_hop, tuple(route.as_path), route.local_pref, route.med, route.weight)

        def detail(route: BGPRoute) -> str:
            return (f"next_hop={route.next_hop} as_path={route.as_path} "
                    f"local_pref={route.local_pref} med={route.med} weight={route.weight}")

        name = self.router.name
        for prefix, route in new_loc_rib.items():
            attrs = _path_attrs(route.next_hop, route.as_path, route.local_pref, route.med)
            if prefix not in old_loc_rib:
                clock.record(
                    "rib",
                    f"{name} best {prefix}",
                    f"`{name}` selected a best path to `{prefix}` — {attrs}",
                    f"loc_rib[{prefix}] += {detail(route)}",
                )
            elif identity(route) != identity(old_loc_rib[prefix]):
                clock.record(
                    "rib",
                    f"{name} new-best {prefix}",
                    f"`{name}` changed its best path to `{prefix}` — {attrs}",
                    f"loc_rib[{prefix}] = {detail(route)}",
                )

        for prefix in old_loc_rib:
            if prefix not in new_loc_rib:
                clock.record(
                    "rib",
                    f"{name} lost {prefix}",
                    f"`{name}` lost its route to `{prefix}`",
                    f"loc_rib[{prefix}] removed",
                )

    def _record_rejections(
        self,
        old_rejected: dict[tuple[str, IPv4Network], tuple[IPv4Address, tuple[int, ...]]],
        new_rejected: dict[tuple[str, IPv4Network], tuple[IPv4Address, tuple[int, ...]]],
        clock: WorldClock,
    ) -> None:
        """Narrate inbound routes newly dropped by loop prevention.

        Only routes rejected this pass but not the previous one are recorded, so a
        persistently-looped route is reported once rather than every tick.
        """
        name = self.router.name
        for (peer_name, prefix), (peer_ip, as_path) in new_rejected.items():
            if (peer_name, prefix) in old_rejected:
                continue
            path = " ".join(str(a) for a in as_path) or "i"
            clock.record(
                "rej",
                f"{name} loop {prefix}",
                f"`{name}` rejected `{prefix}` from `{peer_name}` — AS-path loop "
                f"(AS{self.asn} already in `{path}`)",
                f"%BGP-6-UPDATE: neighbor {peer_ip} denied {prefix} (AS-PATH loop)",
            )

    def _find_egress_interface(self, next_hop: IPv4Address) -> Interface | None:
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

    def refresh_fib(self, clock: WorldClock | None = None) -> None:
        """Refresh the FIB according to the current loc_rib.

        A path received in loc_rib may not be installed into the fib,
        if the next-hop is unreachable. This is a common case with iBGP,
        where next-hop-self is required to rewrite next-hop to the border
        BGP router. Another approach is to advertise the network that
        next-hop belongs to, but I guess nobody would do that.
        """
        self.router.routing_table.remove_by_type(RouteType.BGP)

        old_unreachable = self.fib_unreachable
        new_unreachable: dict[IPv4Network, tuple[IPv4Address, str | None]] = {}

        for prefix, route in self.loc_rib.items():
            if route.source.type is BGPRouteSourceType.LOCAL:
                continue
            iface = self._find_egress_interface(route.next_hop)
            if iface is None: # Unreachable next-hop
                advertiser = route.source.router.name if route.source.router else None
                new_unreachable[prefix] = (route.next_hop, advertiser)
                continue
            self.router.routing_table.add(
                Route(
                    network=prefix,
                    interface=iface,
                    next_hop=route.next_hop,
                    route_type=RouteType.BGP,
                )
            )

        if clock is not None:
            self._record_fib_failures(old_unreachable, new_unreachable, clock)
        self.fib_unreachable = new_unreachable

    def _record_fib_failures(
        self,
        old_unreachable: dict[IPv4Network, tuple[IPv4Address, str | None]],
        new_unreachable: dict[IPv4Network, tuple[IPv4Address, str | None]],
        clock: WorldClock,
    ) -> None:
        """Record this incident of not installing a route in loc_rib into fib.

        Again, some checks is required for it to not echo the event continuously.
        """
        name = self.router.name
        for prefix, (next_hop, advertiser) in new_unreachable.items():
            if old_unreachable.get(prefix, (None, None))[0] == next_hop:
                continue
            hint = (
                f"set `next-hop-self {advertiser} {name}`" if advertiser
                else "make its next-hop reachable"
            )
            clock.record(
                "fib",
                f"{name} next-hop unreachable {prefix}",
                f"`{name}` selected `{prefix}` but can't install it — next-hop "
                f"`{next_hop}` is unreachable. Hint: {hint}, or advertise "
                f"`{next_hop}`'s subnet (or add a static route to it).",
                f"%BGP-6-NEXTHOP: {prefix} next-hop {next_hop} inaccessible "
                f"(RIB-failure, route not installed)",
            )
