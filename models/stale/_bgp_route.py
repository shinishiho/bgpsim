from typing import List
from ipaddress import IPv4Network, IPv4Address


class BGPRoute:
    """BGP Route class

    This is a route to be advertised using BGP.
    Relative to one router's perspective
    Contains Cisco flavor attributes.
    """

    def __init__(
        self,
        prefix: str,
    ):
        self.prefix:   IPv4Network = IPv4Network(prefix)
        self.next_hop: IPv4Address = IPv4Address("0.0.0.0")
        self.as_path:  List[int]   = []
        self.local_pref: int       = 0
        self.source
