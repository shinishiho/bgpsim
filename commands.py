"""Flat-grammar command parser that drives the `World` backend.

Kept free of any Textual import so it can be unit-tested headless. The front-end
calls `apply_command(world, line)` and renders the returned `CommandResult`:
the produced `WorldEvent`s carry the Cisco echo, `note` carries a human aside.

Time is *not* a command verb here -- stepping and converging live on the
Timeline buttons in the UI.
"""

from __future__ import annotations

import copy
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
    _need(args, 1, "advertise <router> [prefix]")
    r = _router(world, args[0])
    if len(args) >= 2:
        net = _prefix(args[1])
    else:  # convenience: spin up a fresh loopback and originate it
        net = world.create_loopback(r).network
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
    _need(args, 2, "no-static <router> <prefix>")
    world.remove_static_route(_router(world, args[0]), _prefix(args[1]))
    return ""


def _cmd_withdraw(world: World, args: list[str]) -> str:
    _need(args, 2, "withdraw <router> <prefix>")
    world.withdraw(_router(world, args[0]), _prefix(args[1]))
    return ""


def _cmd_mesh(world: World, args: list[str]) -> str:
    asn = _asn(args, "ibgp-mesh as <asn>")
    sessions = world.build_ibgp_mesh(asn)
    return f"meshed {len(sessions)} new iBGP session(s) in AS{asn}"


def _cmd_shutdown(world: World, args: list[str]) -> str:
    world.shutdown(*_two(world, args, "shutdown <A> <B>"))
    return ""


def _cmd_no_shutdown(world: World, args: list[str]) -> str:
    world.no_shutdown(*_two(world, args, "no-shutdown <A> <B>"))
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
        dst = _prefix(args[1]).network_address  # tolerate a prefix, take its address
    src = next(iter(r.interfaces.values())).ip
    pkt = Packet(src=src, dst=dst, payload="ping")
    outcome = r.forward(pkt)
    return f"{outcome}\npath: {' -> '.join(pkt.hops)}"


_HELP = """\
**Commands** — routers are addressed by name (e.g. `R1`).

- `router <name> [as <asn>]` — add a router (AS 1 by default)
- `link <A> <B> [cost <n>]` — cable two routers
- `loopback <R>` — add a loopback interface
- `peer <A> <B>` — open a BGP session (needs a link)
- `advertise <R> [prefix]` — originate a prefix (auto-loopback if omitted)
- `withdraw <R> <prefix>` — stop originating a prefix
- `static <R> <prefix> <next-hop>` — install a static route
- `no-static <R> <prefix>` — remove a static route
- `ibgp-mesh as <asn>` — full iBGP mesh across an AS
- `shutdown` / `no-shutdown <A> <B>` — admin-down / up the link
- `cut` / `repair` / `destroy <A> <B>` — cable faults
- `send <R> <dst>` — data-plane reachability test
- `help` — this list

Time lives on the Timeline buttons: `>` step, `>>` converge, `<` `<<` rewind.
"""


def _cmd_help(world: World, args: list[str]) -> str:
    return _HELP


_DISPATCH: dict[str, Callable[[World, list[str]], str]] = {
    "router": _cmd_router,       "add-router": _cmd_router,
    "link": _cmd_link,           "connect": _cmd_link,
    "loopback": _cmd_loopback,   "lo": _cmd_loopback,
    "peer": _cmd_peer,           "bgp": _cmd_peer,
    "advertise": _cmd_advertise, "adv": _cmd_advertise,
    "withdraw": _cmd_withdraw,
    "static": _cmd_static_route, "static-route": _cmd_static_route,
    "no-static": _cmd_no_static_route,
    "ibgp-mesh": _cmd_mesh,      "mesh": _cmd_mesh,
    "shutdown": _cmd_shutdown,
    "no-shutdown": _cmd_no_shutdown,
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


def seed_demo(world: World) -> None:
    """A best-path "decision ladder" demo: one prefix, many competing paths.

    Every external network below originates the *same* destination,
    `203.0.113.0/24`, but each path is crafted to lose at exactly one rung of
    the BGP best-path tie-break. Converge the sim (`>>`) and inspect `CORE`:
    its Loc-RIB best path is the one that survives every rung, and the Timeline
    narrates each selection. The ladder (see `BGPEngine.calculate_best_route`):

        weight → local-pref → (locally-originated) → AS-path length
               → MED → eBGP-over-iBGP → lowest router-id

        rung that drops it    origin / path            crafted attribute
        ───────────────────   ──────────────────────   ──────────────────────
        weight                WGT0   (AS65007)          weight 0  (others 10)
        local-pref            LP100  (AS65007)          local-pref 100 (< 200)
        AS-path length        O3 via T3 (AS65013)       as-path length 2 (> 1)
        MED                   MED100 (AS65004)          MED 100   (best: 50)
        eBGP-over-iBGP        O5 via R2 (iBGP)          reaches CORE over iBGP
        router-id             RIDHI  (AS65006)          router-id 10.6.6.6
        ── WINNER ──          BEST   (AS65007)          router-id 10.1.1.1

    `locally-originated` is the only rung not exercised (CORE originates
    nothing of its own), so it stays a tie and the decision falls through it.

    Topology — every link is single-hop eBGP except inside AS65000:

                WGT0  LP100  MED100  RIDHI  BEST     T3 -- O3
                   .    .      |      /     /        /
                    '----'---- CORE ------''--------'     (AS65000)
                                | iBGP
                                R2 ---- O5

    The engine has no command to set weight / local-pref / MED, so `originate()`
    stamps them straight onto each originator's route. They then propagate
    unchanged — this sim does not scrub weight/local-pref/MED across eBGP.

    That propagation has a sharp edge: CORE re-advertises its chosen path to
    every eBGP peer, and a looped-back winner (weight 10 / local-pref 200) would
    beat a weight-0 or local-pref-100 *origin's own* route, so those origins
    would stop advertising and never reach CORE. WGT0 and LP100 are therefore
    placed in BEST's AS (65007): CORE's re-advertised winner carries 65007 in
    its AS-path and is dropped at them by loop prevention, leaving their own
    (deliberately worse) route intact so the weight / local-pref rungs can fire.
    """
    DEST = IPv4Network("203.0.113.0/24")

    def originate(router: Router, *, weight: int = 0,
                  local_pref: int = 100, med: int | None = None) -> None:
        """Originate DEST from `router`, then stamp the crafted attributes onto
        its origin (manual) route so they persist across recompute and ride
        outbound to peers."""
        world.advertise(router, DEST)
        route = router.bgp_engine.manual_rib[DEST]
        route.weight, route.local_pref, route.med = weight, local_pref, med
        router.bgp_engine.loc_rib[DEST] = copy.deepcopy(route)

    def peer_ebgp(origin: Router, *, weight: int = 10,
                  local_pref: int = 200, med: int | None = 50) -> None:
        """Cable `origin` straight to CORE, open eBGP, and originate DEST."""
        world.create_link(core, origin)
        world.create_bgp_session(core, origin)
        originate(origin, weight=weight, local_pref=local_pref, med=med)

    # --- our AS: the decision maker + one iBGP peer --------------------------
    core = world.create_router(name="CORE", asn=65000)
    r2 = world.create_router(name="R2", asn=65000)
    world.create_link(core, r2)
    world.create_bgp_session(core, r2)                       # iBGP

    # --- direct eBGP candidates into CORE, each sunk at one rung -------------
    # weight: a great path (lp 200, med 50) thrown out purely for weight 0.
    # In AS65007 (BEST's AS) so loop prevention shields its own route -- see the
    # docstring; otherwise it re-imports CORE's winner and never advertises.
    peer_ebgp(world.create_router(name="WGT0", asn=65007), weight=0)
    # local-pref: ties on weight, loses on local-pref 100 < 200. Also in 65007.
    peer_ebgp(world.create_router(name="LP100", asn=65007), local_pref=100)
    # MED: ties through AS-path, loses on MED 100 > 50.
    peer_ebgp(world.create_router(name="MED100", asn=65004), med=100)

    # router-id: ties the winner on every attribute, loses on a higher id.
    ridhi = world.create_router(name="RIDHI", asn=65006)
    ridhi.add_loopback(IPv4Network("10.6.6.6/32"))           # high router-id
    peer_ebgp(ridhi)
    # the winner: identical attributes to RIDHI, but the lowest router-id.
    best = world.create_router(name="BEST", asn=65007)
    best.add_loopback(IPv4Network("10.1.1.1/32"))            # low router-id
    peer_ebgp(best)

    # --- AS-path: O3 sits one AS behind transit T3, so its path is length 2 --
    t3 = world.create_router(name="T3", asn=65003)
    o3 = world.create_router(name="O3", asn=65013)
    world.create_link(core, t3)
    world.create_link(t3, o3)
    world.create_bgp_session(core, t3)                       # eBGP transit
    world.create_bgp_session(t3, o3)                         # eBGP origin
    originate(o3, weight=10, local_pref=200, med=50)

    # --- eBGP-over-iBGP: O5 is eBGP to R2, so it reaches CORE only via iBGP --
    o5 = world.create_router(name="O5", asn=65005)
    world.create_link(r2, o5)
    world.create_bgp_session(r2, o5)                         # eBGP
    originate(o5, weight=10, local_pref=200, med=50)
