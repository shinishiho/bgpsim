from dataclasses import dataclass, field
from typing import List


@dataclass
class BGPConfig:
    """BGP Config class

    BGP configuration set in each router.
    Equivalent Cisco command:
    `router bgp <asn>`
    """

    asn:                 int = 1
    # bgp_id:              str
    hold_time:           int = 180
    keepalive_interval:  int = 60
    bgp_neighbor_config: List[BGPNeighborConfig] = field(default_factory=list)


@dataclass
class BGPNeighborConfig:
    """BGP Neighbor config class

    Note: this is not the session between two routers, but the configured session
    from one router's perspective.

    This includes single hop, multi hop, iBGP, eBGP.

    Equivalent Cisco command:
    `neighbor <ip_addr> remote-as <peer_asn>`
    """

    remote_as:     int
    next_hop_self: bool = False
    weight:        int = 0
