from typing import List
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
        state: bool = True

    ):
        self.network     = network
        self.cost:  int  = cost
        self.state: bool = state

        hosts = list(network.hosts())
        self._if: dict[IPv4Address, Router] = {
            hosts[0]: router_a,
            hosts[1]: router_b,
        }

    def up(self):
        """Change link state to UP"""
        self.state = True

    def down(self):
        """Change link state to DOWN"""
        self.state = False

    def update_cost(
        self,
        new_cost: int
    ):
        """Update link cost

        Keyword arguments:
        new_cost: new link cost
        """
        self.cost = new_cost

    def get_ip(self, router: Router) -> IPv4Address | None:
        return next((ip for ip, r in self._if.items() if r is router), None)

    def get_peer(self, router: Router) -> Router | None:
        return next((r for r in self._if.values() if r is not router), None)

    def get_peer_ip(self, router: Router) -> IPv4Address | None:
        peer = self.get_peer(router)
        return self.get_ip(peer) if peer else None


class LinkManager:
    """Link Manager class

    TODO: decide to put this here or in world.py
    Manages every link in the world.
    """

    def __init__(self):
        self.links: List[Link] = []
        self.local_pool        = sorted(
            IPv4Network("192.168.0.0/16").subnets(prefixlen_diff=8),
            reverse=True,
        )
        self.loopback_pool     = sorted(
            IPv4Network("127.0.0.0/8").subnets(prefixlen_diff=16),
            reverse=True,
        )

    def _find(
        self,
        router_a: Router,
        router_b: Router
    ) -> Link | None:
        """Find an established connection between two routers

        Keyword arguments:
        router_a: the first router
        router_b: the second router
        """

        for link in self.links:
            if list(link._if.values()) == [router_a, router_b]:
                return link

        return None


    def create(
        self,
        router_a: Router,
        router_b: Router,
        cost: int = 10,
    ) -> Link:
        """Create a physical link (connection) between two routers
        Get a /24 network from the available pool (192.168.0.0/16 or 127.0.0.0/8 for loopback)

        Keyword arguments:
        router_a: the first router
        router_b: the second router
        """

        if self._find(router_a, router_b) is not None:
            raise ValueError("Link already exists.")

        loopback = router_a is router_b
        if loopback is True:
            if not self.loopback_pool:
                raise ValueError("Loopback address pool exhausted.")
            link = Link(
                router_a=router_a,
                router_b=router_b,
                network=self.loopback_pool.pop(),
                cost=0
            )
        else:
            if not self.local_pool:
                raise ValueError("Local address pool exhausted.")
            link = Link(
                router_a=router_a,
                router_b=router_b,
                network=self.local_pool.pop(),
                cost=cost
            )

        self.links.append(link)
        router_a.add_interface(link)

        if loopback is False:
            router_b.add_interface(link)

        return link


    def delete(
        self,
        router_a: Router,
        router_b: Router,
    ):
        """Delete a link between two routers (disconnect)

        Keyword arguments:
        router_a: the first router
        router_b: the second router
        """

        link = self._find(router_a, router_b)
        if link is not None:
            if link.network.is_loopback:
                self.loopback_pool.append(link.network) # TODO: Re-sort? Lazy
            else:
                self.local_pool.append(link.network)    # TODO: Re-sort? Lazy
            self.links.remove(link)
        else:
            raise ValueError("Link does not exist.")
