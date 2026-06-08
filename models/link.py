from collections import deque
from collections.abc import Iterator
from ipaddress import IPv4Network, IPv4Address

from .router import Router, Interface


class Link:
    """Physical cable between two routers.

    Keeps track of its two endpoints
    """

    def __init__(
        self,
        network: IPv4Network,
        cost: int,
    ):
        self.network = network
        self.cost: int = cost
        self.line_is_up: bool = True # physical line state (e.g. cable cut or not)
        self.interfaces: list[Interface] = []

    @property
    def is_up(self) -> bool:
        """Check the link state

        A link is up if the line is not cut and both interfaces are not shutdown.
        """
        return self.line_is_up and all(iface.is_admin_up for iface in self.interfaces)

    def up(self) -> None:
        """Connect the cable"""
        self.line_is_up = True

    def down(self) -> None:
        """Shark eats the cable"""
        self.line_is_up = False

    def update_cost(
        self,
        new_cost: int
    ) -> None:
        """Update link cost"""
        self.cost = new_cost

    def get_ip(self, router: Router) -> IPv4Address:
        """Find the IP address of a router on this link"""
        for iface in self.interfaces:
            if iface.router is router:
                return iface.ip
        raise ValueError(f"{router.name} is not connected to {self.network}")

    def get_peer_of(self, router: Router) -> Router:
        """Find the other router on this link"""
        if not any(iface.router is router for iface in self.interfaces):
            raise ValueError(f"{router.name} is not connected to {self.network}")
        return next(iface.router for iface in self.interfaces if iface.router is not router)

    def get_peer_ip(self, router: Router) -> IPv4Address:
        """The two above functions combined"""
        peer = self.get_peer_of(router)
        return self.get_ip(peer)


class LinkManager:
    """Link Manager class

    Manages every link in the world, plus the address pools
    (the /24 network address pool and the /32 loopback address pool).
    """

    def __init__(self):
        self.links: list[Link] = []
        self.local_pool: Iterator[IPv4Network] = IPv4Network("192.168.0.0/16").subnets(prefixlen_diff=8)
        self.local_pool_freed: deque[IPv4Network] = deque()
        self.loopback_pool: Iterator[IPv4Network] = IPv4Network("10.0.0.0/24").subnets(prefixlen_diff=8)
        self.loopback_pool_freed: deque[IPv4Network] = deque()

    def _alloc(
        self,
        pool: Iterator[IPv4Network],
        freed: deque[IPv4Network]
    ) -> IPv4Network:
        """Get one from the pool, or reuse a freed one"""
        if freed:
            return freed.popleft()
        else:
            try:
                return next(pool)
            except StopIteration:
                raise ValueError("Address pool exhausted.")

    def _free(
        self,
        network: IPv4Network,
        freed: deque[IPv4Network]
    ) -> None:
        """Free a network, add it back to the pool"""
        freed.append(network)

    def alloc_loopback(self) -> IPv4Network:
        """Reserve a /32 from the loopback pool (for a router's loopback interface)"""
        return self._alloc(self.loopback_pool, self.loopback_pool_freed)

    def free_loopback(self, network: IPv4Network) -> None:
        """Return a loopback /32 to the pool"""
        self._free(network, self.loopback_pool_freed)

    def create(
        self,
        router_a: Router,
        router_b: Router,
        cost: int = 10,
    ) -> Link:
        """Create a physical link (cable) between two distinct routers.

        Gets a /24 network from the available pool 192.168.0.0/16. A loopback is
        not a link -- use World.create_loopback / Router.add_loopback for those.

        Keyword arguments:
        router_a: the first router
        router_b: the second router
        """
        if router_a is router_b:
            raise ValueError("a loopback is not a link; use World.create_loopback")

        if router_a.has_link_to(router_b):
            raise ValueError("So I don't want to allow multiple links between two routers")

        link = Link(
            network=self._alloc(self.local_pool, self.local_pool_freed),
            cost=cost
        )

        self.links.append(link)
        router_a.add_interface(link)  # attaches first  -> takes .1
        router_b.add_interface(link)  # attaches second -> takes .2

        return link


    def destroy(
        self,
        router_a: Router,
        router_b: Router,
    ) -> None:
        """Delete a link between two routers (disconnect)

        Keyword arguments:
        router_a: the first router
        router_b: the second router
        """
        if router_a.has_link_to(router_b):
            link = router_a.get_link_to(router_b)
            self.links.remove(link)
            self._free(link.network, self.local_pool_freed)

            router_a.remove_interface(link)
            router_b.remove_interface(link)
        else:
            raise ValueError("Link does not exist.")
