from bgp_config import BGPConfig, BGPNeighborConfig


class Router:
    """Router class

    It is a router. It has a name.
    """

    def __init__(
        self,
        name: str,
        bgpConfig: BGPConfig,
        bgpSessionConfig: BGPNeighborConfig
    ):
        self.name: str = name

        self.bgpConfig:        BGPConfig         = bgpConfig
        self.bgpSessionConfig: BGPNeighborConfig = bgpSessionConfig
