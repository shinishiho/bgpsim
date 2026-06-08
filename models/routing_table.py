from dataclasses import dataclass, field
from ipaddress import IPv4Network, IPv4Address
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .router import Interface

class RouteType(Enum):
    DIRECT = 0
    STATIC = 1
    BGP = 20


@dataclass
class Route:
    """An entry in a router's routing table"""
    network: IPv4Network
    interface: Interface
    route_type: RouteType
    next_hop: IPv4Address | None = None


@dataclass
class RoutingTable:
    routes: list[Route] = field(default_factory=list)

    def add(self, entry: Route) -> None:
        """Install a route to the routing table

        Taking Administrative Distance into consideration. The lowest AD wins;
        an equal-or-better AD replaces the existing route for that network.
        """
        existing = [r for r in self.routes if r.network == entry.network]
        if existing:
            best_ad = min(r.route_type.value for r in existing)
            if best_ad < entry.route_type.value:
                print(f"Route for {entry.network} already exists with better AD, so not installing")
                return
            print(f"Route for {entry.network} already exists with same or worse AD, so it will be replaced")
            self.routes = [r for r in self.routes if r.network != entry.network]

        self.routes.append(entry)

    def remove(self, network: IPv4Network) -> None:
       self.routes = [r for r in self.routes if r.network != network]

    def remove_by_type(self, route_type: RouteType) -> None:
       """Currently used to remove BGP routes, to refresh the routes after each BGP update"""
       self.routes = [r for r in self.routes if r.route_type is not route_type]

    def lookup(
        self,
        dst: IPv4Address,
        exclude_route_type: RouteType | None = None
    ) -> Route | None:
        """Find all entries in the routing table and return the one with longest matching prefix"""
        matches: list[Route] = [route for route in self.routes if dst in route.network]

        if exclude_route_type is not None:
            matches = [route for route in matches if route.route_type is not exclude_route_type]

        if not matches:
            return None

        return max(matches, key=lambda route: route.network.prefixlen)

