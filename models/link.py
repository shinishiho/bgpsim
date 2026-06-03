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
        cost: int,
        state: bool = True

    ):
        self.routers    = frozenset([router_a, router_b])
        self.cost:  int = cost
        self.state: int = state

    def up(self):
        """Change link state to UP"""
        self.state = True

    def down(self):
        """Change link state to DOWN"""
        self.state = False


class LinkManager:
    """Link Manager class

    TODO: decide to put this here or in world.py
    Manages every link in the world.
    """

    def __init__(self):
        self.links = set([])

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
            if link.routers == frozenset([router_a, router_b]):
                return link

        return None


    def create(
        self,
        router_a: Router,
        router_b: Router,
        cost: int = 10,
    ) -> Link:
        """Create a physical link (connection) between two routers

        Keyword arguments:
        router_a: the first router
        router_b: the second router
        """

        if self._find(router_a, router_b) is not None:
            raise ValueError("Link already exists.")

        link = Link(
            router_a=router_a,
            router_b=router_b,
            cost=cost
        )
        self.links.add(link)

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
        if link is None:
            raise ValueError("Link does not exist.")

        self.links.remove(link)
        del link
