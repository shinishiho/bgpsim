"""Flat-grammar command parser that drives the `World` backend.

Kept free of any Textual import so it can be unit-tested headless. The front-end
calls `apply_command(world, line)` and renders the returned `CommandResult`:
the produced `WorldEvent`s carry the Cisco echo, `note` carries a human aside.

Time is *not* a command verb here -- stepping and converging live on the
Timeline buttons in the UI.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING, Callable

from models.world import World
from models.packet import Packet

if TYPE_CHECKING:
    from models.router import Router
    from models.world import WorldEvent


@dataclass
class CommandResult:
    """Outcome of one command line.

    ok:     whether it applied cleanly.
    events: the WorldEvents the command produced (for the Cisco echo).
    note:   a human-readable aside (e.g. "created R4", a packet's path).
    error:  the message to show when ok is False.
    """

    ok: bool
    events: list[WorldEvent] = field(default_factory=list)
    note: str = ""
    error: str = ""


class CommandError(Exception):
    """A user-facing error raised by a handler (bad args, unknown router...)."""


# --- helpers -----------------------------------------------------------------
def _router(world: World, name: str) -> Router:
    for r in world.routers.routers:
        if r.name.lower() == name.lower():
            return r
    raise CommandError(f"no router named {name!r}")


def _prefix(text: str) -> IPv4Network:
    try:
        return IPv4Network(text, strict=False)
    except ValueError:
        raise CommandError(f"invalid prefix {text!r}")


def _masked_prefix(tokens: list[str]) -> IPv4Network:
    """Parse a user network that must carry an explicit mask.

    `ipaddress.IPv4Network` accepts both a CIDR length and a dotted netmask
    after the slash, so we just assemble `address/mask` and hand it over:
      - CIDR slash notation:    `10.0.0.0/24`
      - address + dotted mask:  `10.0.0.0 255.255.255.0`

    A bare address is rejected so the operator always states the prefix length
    instead of silently defaulting to a host route (`/32`).
    """
    if "/" in tokens[0]:
        spec = tokens[0]
    elif len(tokens) >= 2:
        spec = f"{tokens[0]}/{tokens[1]}"
    else:
        raise CommandError(
            f"a network mask is required: write {tokens[0]}/24 "
            f"or {tokens[0]} 255.255.255.0"
        )
    try:
        return IPv4Network(spec, strict=False)
    except ValueError:
        raise CommandError(f"invalid network {' '.join(tokens)!r}")


def _need(args: list[str], n: int, usage: str) -> None:
    if len(args) < n:
        raise CommandError(f"usage: {usage}")


def _two(world: World, args: list[str], usage: str) -> tuple[Router, Router]:
    _need(args, 2, usage)
    return _router(world, args[0]), _router(world, args[1])


def _asn(args: list[str], usage: str) -> int:
    """Pull an AS number from `as <n>` or a bare trailing integer."""
    if "as" in args:
        i = args.index("as")
        tail = args[i + 1 :]
    else:
        tail = args
    if tail and tail[-1].lstrip("-").isdigit():
        return int(tail[-1])
    raise CommandError(f"usage: {usage}")


# --- handlers ----------------------------------------------------------------
def _cmd_router(world: World, args: list[str]) -> str:
    if not args or args[0].lower() == "as":
        raise CommandError("usage: router <name> [as <asn>]")
    name, rest = args[0], args[1:]
    if any(r.name.lower() == name.lower() for r in world.routers.routers):
        raise CommandError(f"a router named {name!r} already exists")
    asn = _asn(rest, "router <name> [as <asn>]") if "as" in rest else 1
    r = world.create_router(name=name, asn=asn)
    return f"created {r.name} in AS{asn}"


def _cmd_link(world: World, args: list[str]) -> str:
    cost = 10
    if "cost" in args:
        i = args.index("cost")
        try:
            cost = int(args[i + 1])
        except (IndexError, ValueError):
            raise CommandError("expected a number after 'cost'")
        args = args[:i] + args[i + 2 :]
    a, b = _two(world, args, "link <A> <B> [cost <n>]")
    world.create_link(a, b, cost)
    return ""


def _cmd_loopback(world: World, args: list[str]) -> str:
    _need(args, 1, "loopback <router>")
    r = _router(world, args[0])
    iface = world.create_loopback(r)
    return f"{r.name} {iface.name} = {iface.ip}/32"


def _cmd_peer(world: World, args: list[str]) -> str:
    a, b = _two(world, args, "peer <A> <B>")
    world.create_bgp_session(a, b)
    return ""


def _cmd_advertise(world: World, args: list[str]) -> str:
    _need(args, 2, "advertise <router> <prefix>/<mask>")
    r = _router(world, args[0])
    net = _masked_prefix(args[1:])
    world.advertise(r, net)
    return f"{r.name} originates {net}"


def _cmd_static_route(world: World, args: list[str]) -> str:
    _need(args, 3, "static <router> <prefix>/<mask> <next-hop>")
    r = _router(world, args[0])
    try:
        next_hop = IPv4Address(args[-1])
    except ValueError:
        raise CommandError(f"invalid next-hop {args[-1]!r}")
    net = _masked_prefix(args[1:-1])
    world.add_static_route(r, net, next_hop)
    return f"{r.name} static route to {net} via {next_hop}"


def _cmd_no_static_route(world: World, args: list[str]) -> str:
    _need(args, 2, "no static <router> <prefix>")
    world.remove_static_route(_router(world, args[0]), _prefix(args[1]))
    return ""


def _cmd_no_advertise(world: World, args: list[str]) -> str:
    _need(args, 2, "no advertise <router> <prefix>")
    world.withdraw(_router(world, args[0]), _prefix(args[1]))
    return ""


def _cmd_mesh(world: World, args: list[str]) -> str:
    asn = _asn(args, "ibgp-mesh as <asn>")
    if not any(r.bgp_engine.asn == asn for r in world.routers.routers):
        raise CommandError(f"no routers in AS{asn}")
    sessions = world.build_ibgp_mesh(asn)
    return f"meshed {len(sessions)} new iBGP session(s) in AS{asn}"


def _cmd_no_mesh(world: World, args: list[str]) -> str:
    asn = _asn(args, "no ibgp-mesh as <asn>")
    sessions = world.destroy_ibgp_mesh(asn)
    return f"removed {len(sessions)} iBGP session(s) in AS{asn}"


def _cmd_no_router(world: World, args: list[str]) -> str:
    _need(args, 1, "no router <name>")
    r = _router(world, args[0])
    world.destroy_router(r)
    return f"removed {r.name}"


def _cmd_no_loopback(world: World, args: list[str]) -> str:
    _need(args, 1, "no loopback <router> [<ip>]")
    r = _router(world, args[0])
    ip = None
    if len(args) >= 2:
        try:
            ip = IPv4Address(args[1])
        except ValueError:
            raise CommandError(f"invalid loopback address {args[1]!r}")
    iface = world.destroy_loopback(r, ip)
    return f"{r.name} removed {iface.name} ({iface.ip}/32)"


def _cmd_no_peer(world: World, args: list[str]) -> str:
    world.destroy_bgp_session(*_two(world, args, "no peer <A> <B>"))
    return ""


def _cmd_shutdown(world: World, args: list[str]) -> str:
    world.shutdown(*_two(world, args, "shutdown <A> <B>"))
    return ""


def _cmd_no_shutdown(world: World, args: list[str]) -> str:
    world.no_shutdown(*_two(world, args, "no shutdown <A> <B>"))
    return ""


_NEIGHBOR_USAGE = (
    "neighbor <R> <neighbor> <weight|local-pref|med|prepend|next-hop-self> [value]"
)


def _neighbor_int(rest: list[str], attr: str) -> int:
    """Parse the integer value an attribute needs, or raise a usage error."""
    if not rest or not rest[0].lstrip("-").isdigit():
        raise CommandError(f"usage: neighbor <R> <neighbor> {attr} <number>")
    return int(rest[0])


def _cmd_neighbor(world: World, args: list[str]) -> str:
    """Per-neighbor policy knobs under one Cisco-style verb."""
    a, b = _two(world, args, _NEIGHBOR_USAGE)
    if len(args) < 3:
        raise CommandError(f"usage: {_NEIGHBOR_USAGE}")
    attr, rest = args[2].lower(), args[3:]
    if attr == "weight":
        world.set_weight(a, b, _neighbor_int(rest, "weight"))
    elif attr in ("local-pref", "localpref", "lp"):
        world.set_local_pref(a, b, _neighbor_int(rest, "local-pref"))
    elif attr == "med":
        world.set_med(a, b, _neighbor_int(rest, "med"))
    elif attr == "prepend":
        world.set_prepend(a, b, _neighbor_int(rest, "prepend"))
    elif attr in ("next-hop-self", "nhs"):
        world.set_next_hop_self(a, b, enabled=True)
    else:
        raise CommandError(f"unknown neighbor attribute {attr!r}; {_NEIGHBOR_USAGE}")
    return ""


def _cmd_no_neighbor(world: World, args: list[str]) -> str:
    """Clear a per-neighbor policy knob: `no neighbor <R> <neighbor> <attr>`."""
    a, b = _two(world, args, f"no {_NEIGHBOR_USAGE}")
    if len(args) < 3:
        raise CommandError(f"usage: no {_NEIGHBOR_USAGE}")
    attr = args[2].lower()
    if attr == "weight":
        world.set_weight(a, b, None)
    elif attr in ("local-pref", "localpref", "lp"):
        world.set_local_pref(a, b, None)
    elif attr == "med":
        world.set_med(a, b, None)
    elif attr == "prepend":
        world.set_prepend(a, b, 0)
    elif attr in ("next-hop-self", "nhs"):
        world.set_next_hop_self(a, b, enabled=False)
    else:
        raise CommandError(f"unknown neighbor attribute {attr!r}; no {_NEIGHBOR_USAGE}")
    return ""


def _cmd_cut(world: World, args: list[str]) -> str:
    world.cut_link(*_two(world, args, "cut <A> <B>"))
    return ""


def _cmd_repair(world: World, args: list[str]) -> str:
    world.repair_link(*_two(world, args, "repair <A> <B>"))
    return ""


def _cmd_no_link(world: World, args: list[str]) -> str:
    world.destroy_link(*_two(world, args, "no link <A> <B>"))
    return ""


def _cmd_send(world: World, args: list[str]) -> str:
    _need(args, 2, "send <router> <dst-ip>")
    r = _router(world, args[0])
    if not r.interfaces:
        raise CommandError(f"{r.name} has no interfaces to source from")
    try:
        dst = IPv4Address(args[1])
    except ValueError:
        # Disallow specifying a network address as the destination
        try:
            IPv4Network(args[1], strict=False)
        except ValueError:
            raise CommandError(f"invalid destination address {args[1]!r}")
        raise CommandError(
            f"{args[1]} is a network, not a host; send to a specific address "
            f"(e.g. a router's interface IP)"
        )
    # A bare address that is a subnet's base address is a network, not a host.
    entry = r.routing_table.lookup(dst)
    if entry is not None and entry.network.prefixlen < 32 and dst == entry.network.network_address:
        raise CommandError(
            f"{dst} is the network address of {entry.network}, not a host; "
            f"send to a specific address (e.g. a router's interface IP)"
        )
    # Source from the egress interface toward the destination (Cisco-style),
    # falling back to the first interface when there is no route to dst (the
    # forward() call below will then report the unreachable destination).
    src = entry.interface.ip if entry is not None else next(iter(r.interfaces.values())).ip
    pkt = Packet(src=src, dst=dst, payload="ping")
    outcome = r.forward(pkt)
    path = " -> ".join(pkt.hops)
    if pkt.received:
        return (
            f"{outcome}\n\n"
            f"| From... to... ? | Peek peek peek... it says | The packet moved through |\n"
            f"| --- | --- | --- |\n"
            f"| {pkt.src} → {pkt.dst} | {pkt.payload!r} | {path} |\n"
        )
    return f"{outcome}\n\nThe packet moved through: {path}"


_HELP = """\
**Commands** — call routers by name (`R1`); aliases are listed beside each verb.

| Command | Set up | Undo / inverse |
| --- | --- | --- |
| `router` · `add-router` | `router <name> [as <asn>]` | `no router <R>` |
| `link` · `connect` | `link <A> <B> [cost <n>]` | `no link <A> <B>` |
| `loopback` · `lo` | `loopback <R>` | `no loopback <R> [ip]` |
| `peer` · `bgp` | `peer <A> <B>` | `no peer <A> <B>` |
| `advertise` · `adv` | `advertise <R> <prefix>/<mask>` | `no advertise <R> <prefix>` |
| `static` · `static-route` | `static <R> <prefix>/<mask> <next-hop>` | `no static <R> <prefix>` |
| `ibgp-mesh` · `mesh` | `ibgp-mesh as <asn>` | `no ibgp-mesh as <asn>` |
| `neighbor` · `nb` | `neighbor <R> <nbr> <attr> [value]` | `no neighbor <R> <nbr> <attr>` |
| `shutdown` | `shutdown <A> <B>` | `no shutdown <A> <B>` |
| `cut` | `cut <A> <B>` | `repair <A> <B>` |
| `repair` | `repair <A> <B>` | `cut <A> <B>` |
| `send` · `ping` | `send <R> <dst>` | — |
| `help` · `?` | `help [command]` | - |

Type `help <command>` (e.g. `help neighbor`) for what a verb does, its arguments, and examples.

Time lives on the Timeline buttons: `>` step · `>>` converge · `<` `<<` rewind.
"""


# --- per-command help --------------------------------------------------------
# Each entry: (canonical verb, alias tuple, detailed markdown). `help <verb>`
# renders one of these; aliases resolve to the same topic so `help adv` works.
_TOPIC_DEFS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "router", ("add-router",),
        """\
### `router` · `add-router` — create a router

Brings a new router online, optionally inside a given AS.

**Arguments**
- `<name>` — the router's hostname (e.g. `R1`). Must be unique.
- `as <asn>` *(optional)* — the BGP autonomous-system number it belongs to. Defaults to **AS1**.

**Examples**
```
router R1
router R2 as 65002
add-router edge as 100
```

**Undo:** `no router <R>` — removes it and everything attached (links, sessions, loopbacks).
""",
    ),
    (
        "link", ("connect",),
        """\
### `link` · `connect` — cable two routers together

Runs a cable between two routers and auto-addresses both ends.

**Arguments**
- `<A> <B>` — the two routers to connect (both must already exist).
- `cost <n>` *(optional)* — the link's IGP metric. Defaults to **10**.

**Examples**
```
link R1 R2
connect R1 R3 cost 50
```

**Undo:** `no link <A> <B>` — pulls the cable for good. To model an outage instead, use `cut` / `repair`.
""",
    ),
    (
        "loopback", ("lo",),
        """\
### `loopback` · `lo` — add a loopback interface

Gives a router a loopback with a fresh `/32` from the loopback pool. Loopbacks are the usual stable source/ID for iBGP sessions and handy prefixes to advertise.

**Arguments**
- `<R>` — the router to add the loopback to.

**Examples**
```
loopback R1
lo R2
```

**Undo:** `no loopback <R> [<ip>]` — frees the `/32`. Name the IP when the router has more than one loopback.
""",
    ),
    (
        "peer", ("bgp",),
        """\
### `peer` · `bgp` — open a BGP session

Establishes a BGP peering between two routers. It's **iBGP** when both sit in the same AS and **eBGP** when they differ; if they aren't directly cabled the session is set up multihop automatically.

**Arguments**
- `<A> <B>` — the two routers to peer.

**Examples**
```
peer R1 R2
bgp R1 R4
```

**Undo:** `no peer <A> <B>` — tears the session down.

*Tip: to peer a whole AS at once, use `ibgp-mesh`.*
""",
    ),
    (
        "advertise", ("adv",),
        """\
### `advertise` · `adv` — originate a prefix into BGP

Makes a router announce a network into BGP. The router must already **know** the network — it has to be a connected, loopback, or static route — otherwise the command is rejected.

**Arguments**
- `<R>` — the originating router.
- `<prefix>/<mask>` — the network to advertise, with an explicit mask. Accepts CIDR (`10.0.0.0/24`) or dotted (`10.0.0.0 255.255.255.0`).

**Examples**
```
advertise R1 10.0.0.0/24
adv R2 192.168.1.0 255.255.255.0
```

**Undo:** `no advertise <R> <prefix>` — stops originating it.
""",
    ),
    (
        "static", ("static-route",),
        """\
### `static` · `static-route` — install a static route

Adds a manual route on a router toward a network via a next-hop IP. Handy for giving a router a prefix it can then `advertise`.

**Arguments**
- `<R>` — the router to install the route on.
- `<prefix>/<mask>` — the destination network with an **explicit** mask, in CIDR (`10.9.0.0/24`) or dotted-netmask (`10.9.0.0 255.255.255.0`) form. A bare address is rejected rather than defaulting to a `/32` host route.
- `<next-hop>` — the next-hop IP address.

**Examples**
```
static R1 10.9.0.0/24 10.0.0.2
static R1 10.9.0.0 255.255.255.0 10.0.0.2
```

**Undo:** `no static <R> <prefix>`.
""",
    ),
    (
        "ibgp-mesh", ("mesh",),
        """\
### `ibgp-mesh` · `mesh` — full-mesh iBGP across an AS

Opens an iBGP session between every pair of routers in one AS (the classic full mesh), skipping any that already peer.

**Arguments**
- `as <asn>` — which AS to mesh.

**Examples**
```
ibgp-mesh as 1
mesh as 65001
```

**Undo:** `no ibgp-mesh as <asn>` — closes the AS's iBGP sessions (eBGP is left alone).
""",
    ),
    (
        "neighbor", ("nb",),
        """\
### `neighbor` · `nb` — per-neighbor BGP policy

Tunes one knob on a router's session toward a specific neighbor. These are the levers that drive best-path selection.

**Arguments**
- `<R>` — the router whose policy you're setting.
- `<neighbor>` — the peer the policy applies to.
- `<attr> [value]` — one of:
  - `weight <n>` — *inbound*, local-only preference for routes **from** this neighbor (highest wins, checked first).
  - `local-pref <n>` (`lp`) — *inbound* preference for routes from this neighbor, shared across the AS.
  - `med <n>` — *outbound* metric hint sent **to** this neighbor (lower is preferred).
  - `prepend <n>` — *outbound*; pads your AS-path `n` times on routes to this neighbor to make them less attractive.
  - `next-hop-self` (`nhs`) — *one-sided*; advertise yourself as the next hop to this neighbor.

**Examples**
```
neighbor R1 R2 weight 200
nb R1 R3 local-pref 150
neighbor R1 R4 prepend 3
neighbor R1 R2 next-hop-self
```

**Undo:** `no neighbor <R> <neighbor> <attr>` — clears that knob back to default.
""",
    ),
    (
        "shutdown", (),
        """\
### `shutdown` — administratively disable an interface

Admin-downs the interface on `<A>` that faces `<B>` (Cisco `shutdown`). The link stays cabled; this is a soft, deliberate state change rather than a fault.

**Arguments**
- `<A> <B>` — `<A>` is the router whose interface goes down; `<B>` is the neighbor it faces.

**Examples**
```
shutdown R1 R2
```

**Undo:** `no shutdown <A> <B>` — brings the interface back up.
""",
    ),
    (
        "cut", (),
        """\
### `cut` — sever a cable (outage)

Models a link failure: the cable between `<A>` and `<B>` goes down but stays in place, so it can be repaired. Think "shark eats cable."

**Arguments**
- `<A> <B>` — the two ends of the link.

**Examples**
```
cut R1 R2
```

**Inverse:** `repair <A> <B>`.
""",
    ),
    (
        "repair", (),
        """\
### `repair` — fix a cut cable

Brings a `cut` link back up between `<A>` and `<B>`.

**Arguments**
- `<A> <B>` — the two ends of the link.

**Examples**
```
repair R1 R2
```

**Inverse:** `cut <A> <B>`.
""",
    ),
    (
        "send", ("ping",),
        """\
### `send` · `ping` — send a test packet

Sources a packet from `<R>` (using its first interface address) toward a destination host IP and reports the hop-by-hop path it takes through the network.

**Arguments**
- `<R>` — the originating router (must have at least one interface).
- `<dst-ip>` — a host address to reach. A network address is rejected; aim at a real interface IP.

**Examples**
```
send R1 10.0.0.2
ping R1 192.168.1.1
```

**Undo:** none — it's a read-only probe.
""",
    ),
    (
        "no", (),
        """\
### `no` — undo / inverse prefix

Cisco-style negation: prefix any constructive verb with `no` to reverse it. The arguments match the original command.

**Examples**
```
no router R1
no advertise R1 10.0.0.0/24
no peer R1 R2
no neighbor R1 R2 weight
```

Each verb's own help lists its exact undo form.
""",
    ),
    (
        "help", ("?",),
        """\
### `help` · `?` — show help

With no argument, lists every command. With a command name, shows detailed help for just that one.

**Examples**
```
help
help neighbor
? send
```
""",
    ),
)

# Every alias (and the canonical verb itself) maps straight to its help text,
# so `help adv` and `help advertise` resolve the same topic in one lookup.
_HELP_BY_ALIAS: dict[str, str] = {
    name: text
    for canon, aliases, text in _TOPIC_DEFS
    for name in (canon, *aliases)
}


# How dare you... to reject me?
_HELP_REJECTIONS: tuple[tuple[str, ...], ...] = (
    ("Hmph!", "What?", "...", "..."),
    ("No.", "You wanted no help.", "...", "I'm not helping you anymore."),
    (
        "Why are you still asking?",
        "Keep asking, it won't change anything.",
        "Don't look for me.",
    ),
    ("...", "You're so desperate.", "Do it yourself.", "Why should I help you?"),
    (
        "*sigh* Fine, I'll do it.",
        "You're persistent. I'll forgive you.",
        "Okay, I'll help you, but don't you say that again.",
    ),
)
# There's no way back
_NO_HELP_FAREWELL: tuple[str, ...] = (
    "So you didn't learn...",
    "Good. You have matured.",
    "Good luck.",
)


@dataclass
class _HelpEasterEgg:
    """In-memory state for the `no help` grudge (reset on every restart)."""

    no_help_count: int = 0       # how many times `no help` has been said
    rejections_left: int = 0     # I give you a second chance
    disabled: bool = False       # Fooled once, it's on you. Fooled twice, it's on me

    def is_sulking(self) -> bool:
        return self.rejections_left > 0 or self.disabled


_help_egg = _HelpEasterEgg()


def _cmd_help(world: World, args: list[str]) -> str:
    egg = _help_egg
    if egg.disabled:
        return "..."

    if egg.rejections_left > 0:
        stage = len(_HELP_REJECTIONS) - egg.rejections_left
        egg.rejections_left -= 1
        return random.choice(_HELP_REJECTIONS[stage])

    if args:
        # Help anything is help, whatever
        return _HELP_BY_ALIAS.get(args[0].lower(), _HELP)
    return _HELP


def _cmd_no_help(world: World, args: list[str]) -> str:
    egg = _help_egg
    if egg.disabled:
        return "..."

    egg.no_help_count += 1
    if egg.no_help_count == 1:
        egg.rejections_left = len(_HELP_REJECTIONS)
        return "Alright... you don't need my help."

    egg.disabled = True
    return random.choice(_NO_HELP_FAREWELL)


# Inverse handlers reached via the `no` prefix: `no advertise R1 …` undoes
# `advertise R1 …`. Aliases mirror the constructive verbs in `_DISPATCH`.
_NO_DISPATCH: dict[str, Callable[[World, list[str]], str]] = {
    "router": _cmd_no_router,        "add-router": _cmd_no_router,
    "link": _cmd_no_link,            "connect": _cmd_no_link,
    "loopback": _cmd_no_loopback,    "lo": _cmd_no_loopback,
    "peer": _cmd_no_peer,            "bgp": _cmd_no_peer,
    "advertise": _cmd_no_advertise,  "adv": _cmd_no_advertise,
    "static": _cmd_no_static_route,  "static-route": _cmd_no_static_route,
    "ibgp-mesh": _cmd_no_mesh,       "mesh": _cmd_no_mesh,
    "neighbor": _cmd_no_neighbor,    "nb": _cmd_no_neighbor,
    "shutdown": _cmd_no_shutdown,
    "help": _cmd_no_help,            "?": _cmd_no_help,
}


def _cmd_no(world: World, args: list[str]) -> str:
    """Dispatch `no <command> …` to the inverse of `<command>`."""
    if not args:
        raise CommandError(
            "usage: no <command> … (e.g. `no advertise R1 10.0.0.0/24`)"
        )
    sub, rest = args[0].lower(), args[1:]
    handler = _NO_DISPATCH.get(sub)
    if handler is None:
        raise CommandError(f"nothing to undo for {sub!r}")
    return handler(world, rest)


_DISPATCH: dict[str, Callable[[World, list[str]], str]] = {
    "router": _cmd_router,       "add-router": _cmd_router,
    "link": _cmd_link,           "connect": _cmd_link,
    "loopback": _cmd_loopback,   "lo": _cmd_loopback,
    "peer": _cmd_peer,           "bgp": _cmd_peer,
    "advertise": _cmd_advertise, "adv": _cmd_advertise,
    "static": _cmd_static_route, "static-route": _cmd_static_route,
    "ibgp-mesh": _cmd_mesh,      "mesh": _cmd_mesh,
    "neighbor": _cmd_neighbor,   "nb": _cmd_neighbor,
    "shutdown": _cmd_shutdown,
    "no": _cmd_no,
    "cut": _cmd_cut,
    "repair": _cmd_repair,
    "send": _cmd_send,
    "ping": _cmd_send,
    "help": _cmd_help,           "?": _cmd_help,
}


def apply_command(world: World, line: str) -> CommandResult:
    """Parse one flat-grammar line and apply it to `world`."""
    parts = line.split()
    if not parts:
        return CommandResult(ok=True)
    verb, args = parts[0].lower(), parts[1:]
    handler = _DISPATCH.get(verb)
    if handler is None:
        return CommandResult(ok=False, error=f"unknown command {verb!r}")
    before = len(world.clock.events)
    try:
        note = handler(world, args)
    except (CommandError, ValueError) as e:
        return CommandResult(ok=False, error=str(e))
    return CommandResult(ok=True, events=world.clock.events[before:], note=note)
