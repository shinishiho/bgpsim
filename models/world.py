from .router import RouterManager
from .link import LinkManager
from .bgp.session import BGPSessionManager


class World:
    """The simulator world

    It holds everything: routers, links, BGP sessions, timers.
    I'm considering if prefixes and policies should be handled by the world or localized.
    """

    def __init__(self):
        self.routers:      RouterManager     = RouterManager()
        self.links:        LinkManager       = LinkManager()
        self.bgp_sessions: BGPSessionManager = BGPSessionManager()
        # self.clock:   WorldClock    = WorldClock() # Not interested currently


class WorldClock:
    """The world's time keeper

    A history book, and prophecy(?) of the world.
    """

    def __init__(self):
        self.current_tick: int              = 0
        self.current_event_idx: int         = 0
        self.events:       list[WorldEvent] = []

    def add_event(self, event: WorldEvent):
        self.events.append(event)

    def advance(self):
        # If there is no event
        if len(self.events) == 0:
            return

        # If we are at the end of the world
        # Usually it means something went wrong
        if self.current_event_idx == len(self.events) - 1:
            print("This is the end...\nHold your breath and count... to ten...")
            return

        self.current_event_idx += 1
        self.current_tick = self.events[self.current_event_idx].tick

    def rewind(self):
        """Look at past events, read-only"""

        # If there is no event
        if len(self.events) == 0:
            return

        # If we are the beginning
        if self.current_event_idx == 0:
            return

        self.current_event_idx -= 1
        self.current_tick = self.events[self.current_event_idx].tick


class WorldEvent:
    """Events occur in the world

    It can be a link state change, BGP message exchange, etc.
    """

    def __init__(
        self,
        english_message: str,
        technical_message: str,
        tick: int = 0,
    ):
        self.english_message:   str = english_message
        self.technical_message: str = technical_message
        self.tick:              int = tick
