class BGPMessages:
    """BGP Messages class

    BGP has several kinds of messages:

    - "Open": After 3-way TCP handshake.
    Contains "version", which is currently 4 (RFC 4271),
    MyAS: the router's ASN,
    HoldTime: if no Update or Keepalive, it will shut down the BGP session, defaults to 180 (Cisco), Keepalive interval 60 (Cisco),
    BGP ID: manually set via `bgp router-id`, highest IP addr in loopback or physical interface.

    - "Update": exchange routing information.
    Withdrawn Route: list all prefix to remove from BGP table,
    Path Attribute: BGP attribute in Type-Length-Value format.

    - Other messages waiting for membership :D
    """
