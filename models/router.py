from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network

from .routing_table import Route, RouteType, RoutingTable
from .packet import Packet
from .bgp.engine import BGPEngine
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .link import Link


@dataclass
class Interface:
    """A router's interface
    
    It is attached to a Link (or None, for a loopback), contains an IP address and a network
    """

    router: Router # Back reference
    name: str
    ip: IPv4Address
    link: Link | None = None
    is_admin_up: bool = True # Cisco `shutdown`/`no shutdown`

    @property
    def network(self) -> IPv4Network:
        """The subnet on this interface.

        Derived, not stored: a physical interface shares its link's subnet (the
        Link owns it), and a loopback is always a /32 around its own address.
        """
        if self.link is not None:
            return self.link.network
        return IPv4Network(f"{self.ip}/32")

    @property
    def is_loopback(self) -> bool:
        return self.link is None

    @property
    def is_up_up(self) -> bool:
        """Depends on link state (if not loopback) and admin state
        Operational (up/up) if link is not broken and interface is not shutdown
        """
        if self.link is None:
            return self.is_admin_up
        return self.link.is_up # Which is both `is_admin_up` and `line_is_up` for the link

    def shutdown(self) -> None:
        """Admin-down this interface (`shutdown`)"""
        self.is_admin_up = False

    def no_shutdown(self) -> None:
        """Admin-up this interface (`no shutdown`)"""
        self.is_admin_up = True


class Router:
    """Router class

    It is a router. It has a name, some interfaces(?), and configurations.
    """

    def __init__(
        self,
        name: str
    ):
        self.name = name
        self.interfaces: dict[str, Interface] = {}
        self._eth_next: int = 0
        self._lo_next:  int = 0
        self.routing_table: RoutingTable = RoutingTable()
        self.bgp_engine: BGPEngine = BGPEngine(router=self)

    @property
    def router_id(self) -> IPv4Address:
        """Cisco-style BGP router-id: highest loopback IP, else highest physical IP.

        Falls back to 0.0.0.0 when the router has no interfaces yet, so the
        best-path router-id tiebreaker never crashes on a bare router.
        """
        loopbacks = [iface.ip for iface in self.interfaces.values() if iface.is_loopback]
        physical  = [iface.ip for iface in self.interfaces.values() if not iface.is_loopback]
        pool = loopbacks or physical
        return max(pool) if pool else IPv4Address("0.0.0.0")

    def add_interface(self, link: Link) -> Interface:
        """Attach a physical interface on `link` and install its connected route
        
        Note: it should be called in pair with the other router
        """
        # The first interface on a link takes .1, the second takes .2
        ip = list(link.network.hosts())[len(link.interfaces)]

        # TODO: reuse destroyed interfaces name?
        name = f"GigabitEthernet0/{self._eth_next}"
        self._eth_next += 1

        iface = Interface(router=self, name=name, ip=ip, link=link)
        self.interfaces[name] = iface
        link.interfaces.append(iface)
        self.routing_table.add(
            Route(
                network=link.network,
                interface=iface,
                next_hop=None,
                route_type=RouteType.DIRECT
            )
        )
        return iface

    def add_loopback(self, network: IPv4Network) -> Interface:
        """Add a virtual loopback interface (no cable) and its connected /32 route"""
        ip = list(network.hosts())[0]
        # TODO: reuse destroyed interfaces name?
        name = f"Loopback{self._lo_next}"
        self._lo_next += 1

        iface = Interface(router=self, name=name, ip=ip, link=None)
        self.interfaces[name] = iface
        self.routing_table.add(
            Route(
                network=network,
                interface=iface,
                next_hop=None,
                route_type=RouteType.DIRECT
            )
        )
        return iface

    def remove_interface(self, link: Link) -> None:
        """Detach the interface on `link` and remove its connected route
        
        Note: it should be called in pair with the other router
        """
        name = next((n for n, iface in self.interfaces.items() if iface.link is link), None)
        if name is not None:
            iface = self.interfaces.pop(name)
            if iface in link.interfaces:
                link.interfaces.remove(iface)
        self.routing_table.remove(link.network)

    def add_static_route(self, network: IPv4Network, next_hop: IPv4Address) -> None:
        """Add a static route to the routing table

        Keyword arguments:
        network: destination network
        next_hop: next hop IP address
        """
        # That said, there should be only one link. It's a lazy way to find the link
        for iface in self.interfaces.values():
            if iface.link is not None and next_hop in iface.network:
                self.routing_table.add(
                    Route(
                        network=network,
                        interface=iface,
                        next_hop=next_hop,
                        route_type=RouteType.STATIC
                    )
                )
                return

        raise ValueError(f"{self.name} says: What is {next_hop} even")

    def interface_name(self, link: Link) -> str:
        """Cisco-style name of this router's interface on `link` (assigned at attach time)"""
        for iface in self.interfaces.values():
            if iface.link is link:
                return iface.name
        raise ValueError(f"{self.name} has no interface on {link.network}")

    def get_link_to(self, router: Router) -> Link:
        """Find the link to a router"""
        for iface in self.interfaces.values():
            if iface.link is not None and iface.link.get_peer_of(self) == router:
                return iface.link
        raise ValueError(f"{self.name} has no link to {router.name}")

    def has_link_to(self, router: Router) -> bool:
        """Check if this router has a link to a router"""
        return any(
            iface.link is not None and iface.link.get_peer_of(self) == router
            for iface in self.interfaces.values()
        )

    def can_reach(self, addr: IPv4Address) -> bool:
        """Is it just as simple as looking up the routing table?"""
        entry = self.routing_table.lookup(addr)
        return entry is not None and entry.interface.is_up_up

    def forward(self, packet: Packet) -> str:
        """Send the packet to the next hop"""
        packet.hops.append(self.name)
        packet.ttl -= 1

        if packet.dst in [iface.ip for iface in self.interfaces.values()]:
            self.process_packet(packet)
            return f"Finally arrived at {self.name}"

        if packet.ttl <= 0:
            return "No time to live, shi ne!"

        entry = self.routing_table.lookup(packet.dst)
        if entry is None:
            return f"{self.name} doesn't know how to route to {packet.dst}"

        if not entry.interface.is_up_up:
            return f"{self.name}'s {entry.interface.name} is down"

        peer = entry.interface.link.get_peer_of(self) # type: ignore (pkt to loopback should have arrived)
        return peer.forward(packet)

    def process_packet(self, packet: Packet) -> None:
        """Do something with the received packet"""
        print(f"{self.name} says: Hey, I received a packet from {packet.src}: {packet.payload}")

class RouterManager:
    """Router Manager class

    About the same as LinkManager class, it manages all routers in the world.
    """

    def __init__(self):
        self.routers: list[Router] = []

    def create(self) -> Router:
        """Create a router with default name R{N}"""

        router = Router(name=f"R{len(self.routers) + 1}")
        self.routers.append(router)

        return router
