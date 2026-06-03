from bgp_config import BGPConfig, BGPNeighborConfig


class Router:
    """Router class

    It is a router. It has a name.
    """

    def __init__(
        self,
        name: str,
        bgp_config: BGPConfig,
        bgp_session_config: BGPNeighborConfig
    ):
        self.name: str = name

        self.bgp_config:         BGPConfig         = bgp_config
        self.bgp_session_config: BGPNeighborConfig = bgp_session_config
