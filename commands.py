"""Flat-grammar command parser that drives the `World` backend.

Kept free of any Textual import so it can be unit-tested headless. The front-end
calls `apply_command(world, line)` and renders the returned `CommandResult`:
the produced `WorldEvent`s carry the Cisco echo, `note` carries a human aside.

Time is *not* a command verb here -- stepping and converging live on the
Timeline buttons in the UI.
"""

from __future__ import annotations

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
    _need(args, 3, "static <router> <prefix> <next-hop>")
    r = _router(world, args[0])
    net = _prefix(args[1])
    try:
        next_hop = IPv4Address(args[2])
    except ValueError:
        raise CommandError(f"invalid next-hop {args[2]!r}")
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


def _cmd_next_hop_self(world: World, args: list[str]) -> str:
    a, b = _two(world, args, "next-hop-self <R> <neighbor>")
    world.set_next_hop_self(a, b, enabled=True)
    return ""


def _cmd_no_next_hop_self(world: World, args: list[str]) -> str:
    a, b = _two(world, args, "no next-hop-self <R> <neighbor>")
    world.set_next_hop_self(a, b, enabled=False)
    return ""


def _cmd_cut(world: World, args: list[str]) -> str:
    world.cut_link(*_two(world, args, "cut <A> <B>"))
    return ""


def _cmd_repair(world: World, args: list[str]) -> str:
    world.repair_link(*_two(world, args, "repair <A> <B>"))
    return ""


def _cmd_destroy(world: World, args: list[str]) -> str:
    world.destroy_link(*_two(world, args, "destroy <A> <B>"))
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
    src = next(iter(r.interfaces.values())).ip
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
**Commands** — routers are addressed by name (e.g. `R1`). Prefix any
config command with `no` to undo it (Cisco-style).

- `router <name> [as <asn>]` — add a router (AS 1 by default)
- `link <A> <B> [cost <n>]` — cable two routers
- `loopback <R>` — add a loopback interface
- `peer <A> <B>` — open a BGP session (needs a link)
- `advertise <R> <prefix>/<mask>` — originate a network the router has; mask required, e.g. `10.0.0.0/24` or `10.0.0.0 255.255.255.0`
- `static <R> <prefix> <next-hop>` — install a static route
- `ibgp-mesh as <asn>` — full iBGP mesh across an AS
- `next-hop-self <R> <neighbor>` — on `R`'s session toward `neighbor`, rewrite the BGP next-hop to `R` (alias `nhs`)
- `shutdown <A> <B>` — admin-down the link
- `no <command> …` — undo it, e.g. `no router R1`, `no link A B`, `no loopback R1 [ip]`, `no peer A B`, `no advertise R1 <prefix>`, `no static R1 <prefix>`, `no ibgp-mesh as <asn>`, `no next-hop-self R neighbor`, `no shutdown A B`
- `cut` / `repair` / `destroy <A> <B>` — cable faults
- `send <R> <dst>` — data-plane reachability test
- `help` — this list

Time lives on the Timeline buttons: `>` step, `>>` converge, `<` `<<` rewind.
"""


def _cmd_help(world: World, args: list[str]) -> str:
    return _HELP


# Inverse handlers reached via the `no` prefix: `no advertise R1 …` undoes
# `advertise R1 …`. Aliases mirror the constructive verbs in `_DISPATCH`.
_NO_DISPATCH: dict[str, Callable[[World, list[str]], str]] = {
    "router": _cmd_no_router,        "add-router": _cmd_no_router,
    "link": _cmd_destroy,            "connect": _cmd_destroy,
    "loopback": _cmd_no_loopback,    "lo": _cmd_no_loopback,
    "peer": _cmd_no_peer,            "bgp": _cmd_no_peer,
    "advertise": _cmd_no_advertise,  "adv": _cmd_no_advertise,
    "static": _cmd_no_static_route,  "static-route": _cmd_no_static_route,
    "ibgp-mesh": _cmd_no_mesh,       "mesh": _cmd_no_mesh,
    "next-hop-self": _cmd_no_next_hop_self, "nhs": _cmd_no_next_hop_self,
    "shutdown": _cmd_no_shutdown,
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
    "next-hop-self": _cmd_next_hop_self, "nhs": _cmd_next_hop_self,
    "shutdown": _cmd_shutdown,
    "no": _cmd_no,
    "cut": _cmd_cut,
    "repair": _cmd_repair,
    "destroy": _cmd_destroy,
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
