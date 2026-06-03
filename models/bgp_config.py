class BGPConfig:
    """BGP Config class

    BGP configuration set in each router.
    """

    def __init__(
        self,
        asn: int,
        bgp_id: str,
        hold_time: int = 180,
        keepalive_interval: int = 60
    ):
        self.asn:                int = asn
        self.hold_time:          int = hold_time
        self.keepalive_interval: int = keepalive_interval


class BGPNeighborConfig:
    """BGP Neighbor config class

    Note: this is not the session between two routers, but the configured session
    from one router's perspective.

    This includes single hop, multi hop, iBGP, eBGP.

    Equivalent Cisco command:
    `neighbor <ipAddr> remote-as <peerAsn> (next-hop-self)`
    """

    def __init__(
        self,
        remote_as: int,
        next_hop_self: bool = False
    ):
        self.remote_as:     int  = remote_as
        self.next_hop_self: bool = next_hop_self
