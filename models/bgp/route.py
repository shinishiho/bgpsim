from enum import Enum
from dataclasses import dataclass, field
from ipaddress import IPv4Network, IPv4Address
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session import BGPSession
    from ..router import Router


class BGPRouteSourceType(Enum):
    LOCAL = "local"
    IBGP  = "ibgp"
    EBGP  = "ebgp"


@dataclass
class BGPRouteSource:
    type:    BGPRouteSourceType
    session: BGPSession | None = None  # None when LOCAL
    router:  Router     | None = None  # None when LOCAL

    def __deepcopy__(self, memo):
        """Deep copy would clone every session and router..."""
        return BGPRouteSource(
            type=self.type,
            session=self.session,
            router=self.router,
        )


@dataclass
class BGPRoute:
    """This class represents a route advertised/to be advertised via BGP

    It has some attributes: prefix, next_hop, as_path, origin(?),
    local_pref, med, weight, source
    """

    prefix: IPv4Network
    next_hop: IPv4Address
    source: BGPRouteSource
    as_path: list[int] = field(default_factory=list)
    # origin: Any
    local_pref: int = 100
    med: int | None = None
    weight: int = 0