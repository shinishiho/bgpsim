from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import Any

@dataclass
class Packet:
    src: IPv4Address
    dst: IPv4Address
    payload: Any
    ttl: int = 64
    hops: list[str] = field(default_factory=list)
