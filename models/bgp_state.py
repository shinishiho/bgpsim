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


