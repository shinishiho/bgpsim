from .bgp_state import BGPState, BGPFsm


class BGPSession:
    """BGP Session class

    The BGP session life-cycle of a router.
    """

    def __init__(
        self
    ):
        self.bgp_fsm: BGPFsm = BGPFsm()


