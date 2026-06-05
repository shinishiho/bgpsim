from dataclasses import dataclass, field
from ipaddress import IPv4Network
from ..bgp.route import BGPRoute


@dataclass
class AdjRibIn:
    """The routes received from a peer, one per BGP session"""
    routes: list[BGPRoute] = field(default_factory=list)


@dataclass
class LocRib:
    """The best route for every single prefix"""
    routes: dict[IPv4Network, BGPRoute] = field(default_factory=dict)


@dataclass
class AdjRibOut:
    """The routes to advertise to peer, one per BGP session"""
    routes: list[BGPRoute] = field(default_factory=list)

