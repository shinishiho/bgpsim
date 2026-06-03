from .router import Router


class Link:
    """Link class

    This class represents the link, or connection between 2 routers.
    They are connected via a physical interface.
    """

    def __init__(
        self,
        routerA: Router,
        routerB: Router,
        cost: int,
        state: bool = True

    ):
        self.routers = frozenset([routerA, routerB])
        self.cost = cost
        self.state = state

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
        routerA: Router,
        routerB: Router
    ) -> Link | None:
        """Find an established connection between two routers

        Keyword arguments:
        routerA: the first router
        routerB: the second router
        """

        for link in self.links:
            if link.routers == frozenset([routerA, routerB]):
                return link

        return None


    def create(
        self,
        routerA: Router,
        routerB: Router,
        cost: int = 10,
    ) -> Link:
        """Create a physical link (connection) between two routers

        Keyword arguments:
        routerA: the first router
        routerB: the second router
        """

        if self._find(routerA, routerB) is not None:
            raise ValueError("Link already exists.")

        link = Link(
            routerA=routerA,
            routerB=routerB,
            cost=cost
        )
        self.links.add(link)

        return link


    def delete(
        self,
        routerA: Router,
        routerB: Router,
    ):
        """Delete a link between two routers (disconnect)

        Keyword arguments:
        routerA: the first router
        routerB: the second router
        """

        link = self._find(routerA, routerB)
        if link is None:
            raise ValueError("Link does not exist.")

        self.links.remove(link)
        del link
