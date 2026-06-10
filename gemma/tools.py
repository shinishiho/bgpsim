from typing import Callable

MODEL_ID = "google/functiongemma-270m-it"
DEVELOPER_PROMPT = (
    "You are a model that can do function calling with the following functions"
)


def add_router(name: str, asn: int = 1) -> str:
    """Create a new router and put it in an autonomous system.

    Args:
        name: Hostname for the new router, e.g. "R1".
        asn: Autonomous-system number the router belongs to. Defaults to 1.
    """
    return f"router {name} as {asn}"


def remove_router(name: str) -> str:
    """Delete a router and everything attached to it (links, sessions, loopbacks).

    Args:
        name: Hostname of the router to remove.
    """
    return f"no router {name}"


def connect_routers(router_a: str, router_b: str, cost: int = 10) -> str:
    """Connect two routers with a cable, auto-addressing both ends of the link.

    Args:
        router_a: Hostname of the first router.
        router_b: Hostname of the second router.
        cost: IGP metric for the link. Defaults to 10.
    """
    return f"link {router_a} {router_b} cost {cost}"


def disconnect_routers(router_a: str, router_b: str) -> str:
    """Remove the link between two routers permanently (use cut/repair for an outage).

    Args:
        router_a: Hostname of the first router.
        router_b: Hostname of the second router.
    """
    return f"no link {router_a} {router_b}"


def add_loopback(router: str) -> str:
    """Give a router a new loopback interface with a fresh /32 address.

    Args:
        router: Hostname of the router to add the loopback to.
    """
    return f"loopback {router}"


def remove_loopback(router: str) -> str:
    """Remove a router's loopback interface and free its /32 address.

    Args:
        router: Hostname of the router whose loopback should be removed.
    """
    return f"no loopback {router}"


def open_bgp_session(router_a: str, router_b: str) -> str:
    """Open a BGP peering between two routers. 

    Args:
        router_a: Hostname of the first router.
        router_b: Hostname of the second router.
    """
    return f"peer {router_a} {router_b}"


def close_bgp_session(router_a: str, router_b: str) -> str:
    """Destroy the BGP peering session between two routers.

    Args:
        router_a: Hostname of the first router.
        router_b: Hostname of the second router.
    """
    return f"no peer {router_a} {router_b}"


def advertise_prefix(router: str, prefix: str) -> str:
    """Originate a network prefix into BGP from a router.

    Args:
        router: Hostname of the originating router.
        prefix: The network to advertise in CIDR notation, e.g. "10.0.0.0/24".
    """
    return f"advertise {router} {prefix}"


def withdraw_prefix(router: str, prefix: str) -> str:
    """Stop originating a network prefix from a router.

    Args:
        router: Hostname of the router that is announcing the prefix.
        prefix: The network to withdraw in CIDR notation, e.g. "10.0.0.0/24".
    """
    return f"no advertise {router} {prefix}"


def add_static_route(router: str, prefix: str, next_hop: str) -> str:
    """Install a static route on a router toward a network via a next-hop IP.

    Args:
        router: Hostname of the router to install the route on.
        prefix: Destination network in CIDR notation, e.g. "10.9.0.0/24".
        next_hop: Next-hop IP address, e.g. "10.0.0.2".
    """
    return f"static {router} {prefix} {next_hop}"


def remove_static_route(router: str, prefix: str) -> str:
    """Remove a static route from a router.

    Args:
        router: Hostname of the router the route lives on.
        prefix: Destination network in CIDR notation, e.g. "10.9.0.0/24".
    """
    return f"no static {router} {prefix}"


def build_ibgp_mesh(asn: int) -> str:
    """Open a full mesh of iBGP sessions between every router in an AS.

    Args:
        asn: The autonomous system to mesh.
    """
    return f"ibgp-mesh as {asn}"


def remove_ibgp_mesh(asn: int) -> str:
    """Close the iBGP full-mesh sessions inside an AS (eBGP is unaffected).

    Args:
        asn: The autonomous system whose iBGP mesh should be removed.
    """
    return f"no ibgp-mesh as {asn}"


def set_neighbor_policy(
    router: str, neighbor: str, attribute: str, value: int = 0
) -> str:
    """Set one per-neighbor BGP policy.

    A policy is owned by a single router and applies to a single BGP neighbor.

    Args:
        router: Hostname of the router whose policy is being set.
        neighbor: Hostname of the peer the policy applies to.
        attribute: One of "weight", "local-pref", "med", "prepend", or
            "next-hop-self".
        value: Integer value for the knob. Ignored for "next-hop-self".
    """
    if attribute in ("next-hop-self", "nhs"):
        return f"neighbor {router} {neighbor} next-hop-self"
    return f"neighbor {router} {neighbor} {attribute} {value}"


def clear_neighbor_policy(router: str, neighbor: str, attribute: str) -> str:
    """Revert a per-neighbor BGP policy to its default.

    A policy is owned by a single router and applies to a single BGP neighbor.

    Args:
        router: Hostname of the router whose policy is being cleared.
        neighbor: Hostname of the peer the policy applies to.
        attribute: One of "weight", "local-pref", "med", "prepend", or
            "next-hop-self".
    """
    return f"no neighbor {router} {neighbor} {attribute}"


def shutdown_interface(router: str, peer: str) -> str:
    """Administratively disable the interface on one router towards a neighbor.

    Args:
        router: Hostname of the router whose interface goes down.
        peer: Hostname of the neighbor on the other end of the interface.
    """
    return f"shutdown {router} {peer}"


def start_interface(router: str, peer: str) -> str:
    """Bring a shut-down interface back up.

    Args:
        router: Hostname of the router whose interface comes back up.
        peer: Hostname of the neighbor on the other end of the interface.
    """
    return f"no shutdown {router} {peer}"


def cut_link(router_a: str, router_b: str) -> str:
    """Temporarily disrupt the link between two routers without deleting it, simulating a cable fault.

    Args:
        router_a: Hostname of one end of the link.
        router_b: Hostname of the other end of the link.
    """
    return f"cut {router_a} {router_b}"


def repair_link(router_a: str, router_b: str) -> str:
    """Repair a link that was previously cut, bringing it back up.

    Args:
        router_a: Hostname of one end of the link.
        router_b: Hostname of the other end of the link.
    """
    return f"repair {router_a} {router_b}"


def send_packet(router: str, destination: str) -> str:
    """Send a test packet from a router toward a destination host IP.

    Args:
        router: Hostname of the originating router.
        destination: Destination host IP address, e.g. "10.0.0.2".
    """
    return f"send {router} {destination}"


def show_help(command: str = "") -> str:
    """Show the command list, or detailed help for one command.

    Args:
        command: A command name to get detailed help for, e.g. "router", "neighbor". Leave
            empty for the full command list.
    """
    return f"help {command}".strip()


# The ordered tool surface. `get_json_schema(fn)` (built in runner/trainer, where
# transformers is available) turns each into the schema the model is shown.
TOOLS: tuple[Callable[..., str], ...] = (
    add_router,
    remove_router,
    connect_routers,
    disconnect_routers,
    add_loopback,
    remove_loopback,
    open_bgp_session,
    close_bgp_session,
    advertise_prefix,
    withdraw_prefix,
    add_static_route,
    remove_static_route,
    build_ibgp_mesh,
    remove_ibgp_mesh,
    set_neighbor_policy,
    clear_neighbor_policy,
    shutdown_interface,
    start_interface,
    cut_link,
    repair_link,
    send_packet,
    show_help,
)

TOOLS_BY_NAME: dict[str, Callable[..., str]] = {fn.__name__: fn for fn in TOOLS}


def to_command_line(name: str, arguments: dict) -> str:
    """Render a model-issued tool call back into a flat-grammar command line.

    Raises KeyError for an unknown tool name and TypeError when the model
    omitted a required argument -- both are caught by the caller, which falls
    back to showing the raw text.
    """
    fn = TOOLS_BY_NAME[name]
    return fn(**arguments)
