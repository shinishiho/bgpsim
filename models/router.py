from ipaddress import IPv4Address, IPv4Network
from dataclasses import dataclass, field
from .bgp_config import BGPConfig
from .routing_table import Route, RoutingTable
from .packet import Packet
from typing import TYPE_CHECKING

if TYPE_CHECKING is True:
    from .link import Link


@dataclass
class Router:
    """Router class

    It is a router. It has a name, some interfaces(?), and configurations.
    """

    name: str
    interfaces: dict[Link, IPv4Address] = field(default_factory=dict)
    routing_table: RoutingTable = field(default_factory=RoutingTable)
    bgp_config: BGPConfig = field(default_factory=BGPConfig)

    def add_interface(self, link: Link) -> None:
        ip = link.get_ip(self)
        self.interfaces[link] = ip
        self.routing_table.add(
            Route(
                network=link.network,
                link=link,
                next_hop=None,
            )
        )

    def remove_interface(self, link: Link) -> None:
        _ = self.interfaces.pop(link)
        self.routing_table.remove(link.network)

    def add_static_route(self, network: IPv4Network, next_hop: IPv4Address) -> None:
        """Add a static route to the routing table

        Keyword arguments:
        network: destination network
        next_hop: next hop IP address
        """
        # That said, there should be only one link. It's a lazy way to find the link
        for link in self.interfaces:
            if next_hop in link.network:
                self.routing_table.add(
                    Route(
                        network=network,
                        link=link,
                        next_hop=next_hop,
                    )
                )
                return

        raise ValueError(f"What is {next_hop} even")

    def forward(self, packet: Packet) -> str:
        """Send the packet to the next hop"""
        packet.hops.append(self.name)
        packet.ttl -= 1

        if packet.dst in self.interfaces.values():
            self.process_packet(packet)
            return f"Finally arrived at {self.name}"

        if packet.ttl <= 0:
            return "No time to live, shi ne!"

        entry = self.routing_table.lookup(packet.dst)
        if entry is None:
            return f"{self.name} doesn't know how to route to {packet.dst}"

        peer = entry.link.get_peer(self)
        if entry.link.state is False:
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
