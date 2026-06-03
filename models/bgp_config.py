class BGPConfig:
    """BGP Config class

    BGP configuration set in each router.
    """

    def __init__(
        self,
        asn: int,
        bgpId: str,
        holdTime: int = 180,
        keepaliveInterval: int = 60
    ):
        self.asn:               int = asn
        self.holdTime:          int = holdTime
        self.keepaliveInterval: int = keepaliveInterval


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
        remoteAs: int,
        nextHopSelf: bool = False
    ):
        self.remoteAs: int = remoteAs
        self.nextHopSelf = nextHopSelf
