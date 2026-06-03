from dataclasses import dataclass, field
from ipaddress import IPv4Network, IPv4Address
from typing import TYPE_CHECKING

if TYPE_CHECKING is True:
    from .link import Link


@dataclass
class Route:
    """An entry in a router's routing table"""
    network: IPv4Network
    link: Link
    next_hop: IPv4Address | None = None


@dataclass
class RoutingTable:
    routes: list[Route] = field(default_factory=list)

    def add(self, entry: Route) -> None:
        self.routes.append(entry)

    def remove(self, network: IPv4Network) -> None:
       self.routes = [r for r in self.routes if r.network != network]

    def lookup(self, dst: IPv4Address) -> Route | None:
        """Find all entries in the routing table and return the one with longest matching prefix"""
        matches: list[Route] = [route for route in self.routes if dst in route.network]

        if len(matches) == 0:
            return None

        return max(matches, key=lambda route: route.network.prefixlen)

