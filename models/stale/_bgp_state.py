from enum import Enum


CONNECTION_RETRY_TIME = 60

class BGPState(Enum):
    Idle = 1
    Connect = 2
    Active = 3
    OpenSent = 4
    OpenConfirm = 5
    Established = 6


class BGPFsm:
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

    - "OpenSent": Waits for Open message after having sent one.
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

    def __init__(self):
        self.state            = BGPState.Idle
        self.connection_retry = CONNECTION_RETRY_TIME

    def start(self):
        self.state = BGPState.Connect
        return self.state

    def connect(self):
        """We are not doing TCP stuff here, just assume they connect magically."""
        self.state = BGPState.OpenSent
        return self.state

    def active(self):
        """We are not doing TCP stuff here, just assume they connect magically."""
        self.state = BGPState.OpenSent
        return self.state

    def open_sent(self):
        """Wait, how can they send packets?"""
        pass
