class BGPSession:
    """BGP Session class

    The established BGP session between two routers.
    """

    def __init__(
        self
    ):
        pass


class BGPStates:
    """BGP State class

    BGP session can be in one of the following states:

    - "Idle": Waits for a "start event".
    Resets ConnectionRetry timer.
    Moves to "Connect" when successful.

    - "Connect": Waits for TCP 3-way handshake.
    Moves to "OpenSent" when successful.
    Moves to "Active" if fails.
    Remains if ConnectionRetry expires.

    - "Active": Waits for TCP 3-way handshake (again).
    Moves to "OpenSent" when successful.
    Moves to "Connect" if ConnectionRetry expires.
    Remains if fails. => Stuck in "Active" = fail to initiate TCP

    - "OpenSent": Waits for Open message.
    Check for params (version, asn, etc.) in the Open message.
    Sends Notification message, moves to "Idle" if sth is wrong.
    Sends Keepalive messages and resets keepalive timer if OK.
    Hold time is negotiated (select the lower number).
    TCP issues -> Moves to "Active".
    Errors -> Sends Notification message and moves to "Idle".

    - "OpenConfirm": Waits for Keepalive message.
    When received, moves to "Established", resets hold timer.
    If receives Notification message -> Moves to "Idle".
    Keep sending Keepalive messages.

    - "Established": Done.
    Sends Update messages.
    On Update or Keepalive message receive, resets hold timer.
    If receives Notification message -> Moves to "Idle".
    """


class BGPMessages:
    """BGP Messages class

    BGP has several kinds of messages:

    - "Open": After 3-way TCP handshake.
    Contains "version", which is currently 4 (RFC 4271),
    MyAS: the router's ASN,
    HoldTime: if no Update or Keepalive, it will shut down the BGP session, defaults to 180 (Cisco), Keepalive interval 60 (Cisco),
    BGP ID: manually set via `bgp router-id`, highest IP addr in loopback or physical interface.

    - "Update": exchange routing information.
    Withdrawn Route Length: if zero, Withdrawn Route will not show up,
    Withdrawn Route: list all prefix to remove from BGP table,
    Total Path Attribute Length: total length of all Path Attribute fields,
    Path Attribute: BGP attribute in Type-Length-Value format.

    - Other messages waiting for membership :D
    """
