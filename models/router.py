from ipaddress import IPv4Address, IPv4Network

from .routing_table import Route, RouteType, RoutingTable
from .packet import Packet
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .link import Link


class Router:
    """Router class

    It is a router. It has a name, some interfaces(?), and configurations.
    """

    def __init__(
        self,
        name: str
    ):
        self.name = name
        self.interfaces: list[tuple[Link, IPv4Address]] = []
        self.routing_table: RoutingTable = RoutingTable()

    def add_interface(self, link: Link) -> None:
        ip = link.get_ip(self)
        if ip is None:
            raise ValueError(f"{self.name} is not connected to {link.network}")

        self.interfaces.append((link, ip))
        self.routing_table.add(
            Route(
                network=link.network,
                link=link,
                next_hop=None,
            )
        )

    def remove_interface(self, link: Link) -> None:
       self.interfaces = [(l, ip) for l, ip in self.interfaces if l is not link]
       self.routing_table.remove(link.network)

    def add_static_route(self, network: IPv4Network, next_hop: IPv4Address) -> None:
        """Add a static route to the routing table

        Keyword arguments:
        network: destination network
        next_hop: next hop IP address
        """
        # That said, there should be only one link. It's a lazy way to find the link
        for link, ip in self.interfaces:
            if next_hop in link.network:
                self.routing_table.add(
                    Route(
                        network=network,
                        link=link,
                        next_hop=next_hop,
                    )
                )
                return

        raise ValueError(f"{self.name} says: What is {next_hop} even")

    def get_link_to(self, router: Router) -> Link:
        """Find the link to a router"""
        for link, ip in self.interfaces:
            if link.get_peer_of(self) == router:
                return link
        raise ValueError(f"{self.name} has no link to {router.name}")

    def has_link_to(self, router: Router) -> bool:
        """Check if this router has a link to a router"""
        return any(link.get_peer_of(self) == router for link, ip in self.interfaces)

    def forward(self, packet: Packet) -> str:
        """Send the packet to the next hop"""
        packet.hops.append(self.name)
        packet.ttl -= 1

        if packet.dst in [ip for link, ip in self.interfaces]:
            self.process_packet(packet)
            return f"Finally arrived at {self.name}"

        if packet.ttl <= 0:
            return "No time to live, shi ne!"

        entry = self.routing_table.lookup(packet.dst)
        if entry is None:
            return f"{self.name} doesn't know how to route to {packet.dst}"

        peer = entry.link.get_peer_of(self)
        if not entry.link.state_is_up:
            return f"The link between {self.name} and {peer.name} is down"

        return peer.forward(packet)

    def process_packet(self, packet: Packet) -> str:
        """Do something with the received packet"""
        print(f"{self.name} received a packet: {packet}")
        pass

class RouterManager:
    """Router Manager class

    About the same as LinkManager class, it manages all routers in the world.
    """

    def __init__(self):
        self.routers: list[Router] = []

    def create(self):
        """Create a router with default name R{N}"""

        router = Router(name=f"R{len(self.routers) + 1}")
        self.routers.append(router)

        return router
