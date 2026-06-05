from collections import deque
from typing import Iterator, List
from ipaddress import IPv4Network, IPv4Address

from .router import Router


class Link:
    """Link class

    This class represents the link, or connection between 2 routers.
    They are connected via a physical interface.
    """

    def __init__(
        self,
        router_a: Router,
        router_b: Router,
        network: IPv4Network,
        cost: int,

    ):
        self.network     = network
        self.cost:  int  = cost
        self.state_is_up: bool = True

        hosts = list(network.hosts())
        if router_a is router_b:
            self._if: dict[IPv4Address, Router] = {
                hosts[0]: router_a,
            }
        else:
            self._if: dict[IPv4Address, Router] = {
                hosts[0]: router_a,
                hosts[1]: router_b,
            }

    def up(self) -> None:
        """Change link state to UP"""
        self.state_is_up = True

    def down(self) -> None:
        """Change link state to DOWN"""
        self.state_is_up = False

    def update_cost(
        self,
        new_cost: int
    ) -> None:
        """Update link cost"""
        self.cost = new_cost

    def get_ip(self, router: Router) -> IPv4Address:
        """Find the IP address of a router on this link"""
        ip = next((ip for ip, r in self._if.items() if r is router), None)
        if ip is None:
            raise ValueError(f"{router.name} is not connected to {self.network}")
        return ip

    def get_peer_of(self, router: Router) -> Router:
        """Find the other router on this link.

        For a loopback link, return itself
        """
        if len(self._if) == 1:
            return router

        if not any(r is router for r in self._if.values()):
            raise ValueError(f"{router.name} is not connected to {self.network}")
        return next((r for r in self._if.values() if r is not router))

    def get_peer_ip(self, router: Router) -> IPv4Address:
        """The two above functions combined"""
        peer = self.get_peer_of(router)
        return self.get_ip(peer)


class LinkManager:
    """Link Manager class

    Manages every link in the world.
    """

    def __init__(self):
        self.links: List[Link] = []
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

    def create(
        self,
        router_a: Router,
        router_b: Router,
        cost: int = 10,
    ) -> Link:
        """Create a physical link (connection) between two routers
        Get a /24 network from the available pool 192.168.0.0/16,
        or /32 from the loopback pool 10.0.0.0/24 if it's loopback.

        Keyword arguments:
        router_a: the first router
        router_b: the second router
        """

        if router_a.has_link_to(router_b):
            raise ValueError("So I don't want to allow multiple links between two routers")

        is_loopback = router_a is router_b
        if is_loopback:
            link = Link(
                router_a=router_a,
                router_b=router_b,
                network=self._alloc(self.loopback_pool, self.loopback_pool_freed),
                cost=0
            )
        else:
            link = Link(
                router_a=router_a,
                router_b=router_b,
                network=self._alloc(self.local_pool, self.local_pool_freed),
                cost=cost
            )

        self.links.append(link)
        router_a.add_interface(link)

        if not is_loopback:
            router_b.add_interface(link)

        return link


    def destroy(
        self,
        router_a: Router,
        router_b: Router,
    ) -> None:
        """Delete a link between two routers (disconnect)

        Keyword arguments:
        router_a: the first router
        router_b: the second router (can be the same as router_a for loopback)
        """

        if router_a.has_link_to(router_b):
            link = router_a.get_link_to(router_b)
            self.links.remove(link)

            is_loopback = router_a is router_b
            if is_loopback:
                self._free(link.network, self.loopback_pool_freed)
            else:
                self._free(link.network, self.local_pool_freed)

            router_a.remove_interface(link)
            if not is_loopback:
                router_b.remove_interface(link)
        else:
            raise ValueError("Link does not exist.")
