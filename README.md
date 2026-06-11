# BGP Simulator

A simple BGP Simulator written in Python. Targets networking beginners and enthusiasts, and me.

## Screenshots

![Topology view with command history](assets/topology.svg)

![Inspector showing R3's routing table, BGP table, and interfaces](assets/inspector.svg)

## Features

- Built with [Textual](https://github.com/textualize/textual) -- fully functional TUI,
  terminal-like experience.
- Beginner-friendly, flat-grammar commands. Routers are called by name (`R1`),
  each command comes with a `no` prefix to reverse the action
  (`no advertise R1 10.0.0.0/24`) and short aliases to type quickly.
- **Time travel.** Events are recorded per tick (timestep), and in the Timeline
  panel you can scrub through them: `>` advances by one step, `>>` runs to
  convergence, `<` / `<<` to go back one step and to the starting point.
  Note: the rewind is read-only, and only shows the past events. The Inspector
  panel doesn't display past states, only the current/computed tables. You can
  observe how the routes are advertised and propagated in each timestep. The
  simulator can handle up to 256 ticks.
- **Inspector panel.** Select a router to inspect its Routing Table and BGP Loc
  RIB (tabbed), plus an Interfaces table showing each interface, its IP, and the
  peer on the other end of the link.
- **Cisco echo.** For each command or event, a related Cisco configuration step
  or syslog is printed for reference. For example: `neighbor <ip> remote-as <asn>`
  `network <prefix> mask <mask>`, `%LINK-3-UPDOWN`, `%BGP-6-UPDATE`, etc.
- **Responsive layout.** UI elements adapt their size to the terminal size,
  and you will be notified if there's not enough space to efficiently display
  the elements.
- **Simplified BGP behavior.** The BGP implementation is simplified to accomodate
  students and new learners, but it still follows the core BGP mechanisms.
  It doesn't use the BGP Finite State Machine, and it assumes that all BGP sessions
  are always up and established unless the link is cut or shutdown. Available BGP
  messages are UPDATE and WITHDRAW, to highlight the route advertisement and
  information propagation process. BGP attributes include AS_PATH, NEXT_HOP,
  LOCAL_PREF, and MED, and the best path selection process follows the standard
  BGP decision algorithm.

## Commands

Call routers by name (`R1`). Aliases are listed beside each verb; any command
undoes with the `no` prefix shown in the last column.

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
| `send` · `ping` | `send <R> <dst>` | — |
| `help` · `?` | `help` | — |

Type `help` in-app for the same table, or
`help <command>` (e.g. `help advertise`) for detailed syntax, arguments,
and examples.

## Quick start

### Requirements

- `uv` installed. [Install `uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Clone repo or download as ZIP and extract.
- Run `uv run main.py`.

### Recommended reading

- BGP course from NetworkLessons. [Link](https://networklessons.com/bgp/)
- BGP Fundamentals from Cisco Press. [Link](https://www.ciscopress.com/articles/article.asp?p=2756480)

## Simulation models

- `Router`: a router, have some network interfaces, routing table, and BGP data.
- `Link`: a physical connection, or a cable to connect two routers together.
- `Interface`: a physical interface, which a cable can connect to, or a virtual interface (loopback or null).
- `BGP peering session`: a two way configuration between two routers to do BGP (internal or external).

## Assumption/Abstraction

- All routers speak BGP (eBGP or iBGP full-mesh). By default, they are assigned to AS 1.
- One network consists of only two routers.
By default, creating a link will create a /24 network within `192.168.0.0/16`,
and routers will have `.1` and `.2` address.
- Loopback interfaces use `/32` subnets of `10.0.0.0/24`.
- Cisco attributes and defaults.

## Limitations

- Only IPv4 is supported.
- No redistribution since there is no other IGP.
- No import/export policies.
- No route reflectors, confederations.

## Acknowledgment

- Networklessons.com for beautifully laid out lectures about BGP basics, mechanisms, attributes, etc.
(that said, access is limited pass chapter 1, since I'm not a member).
- Claude contributes ~50% of the code, mainly including refactors, logic bug fix, realistic BGP compliance, UI, etc.
