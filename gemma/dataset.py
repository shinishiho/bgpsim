import math
import random
from itertools import product
from string import Formatter
from gemma.tools import DEVELOPER_PROMPT, TOOLS

# Dataset size presets. `size` scales the whole set; each command's share is
# then weighted by its template-space size (see _per_tool_targets), so richer
# commands appear more often and tiny ones don't dominate by repetition.
SIZES = ("SMALL", "MEDIUM", "LARGE", "MAXIMUM")
_SIZE_BASE = {"SMALL": 6, "MEDIUM": 18, "LARGE": 48}  # per-tool base count
_RATIO_EXPONENT = 0.5    # compress template-space size (sqrt) into a weight
_MAXIMUM_CAP = 20_000    # per-tool ceiling for MAXIMUM (generator slots are ~infinite)
_EXHAUST_STREAK = 2_000  # consecutive duplicate draws that signal a space is drained

# Random arguments to fill into the templates
ROUTERS = [
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R18",
    "R36",
    "R67",
    "R100",
    "Edge",
    "Core",
    "RR1",
    "PE1",
    "CE1",
    "Doraemon",
    "Conan",
    "Onii-chan",
    "Nahida",
    "Hutao",
]
ASNS = [1, 18, 36, 67, 69, 100, 200, 420, 1412, 65001, 65002, 64512]
COSTS = [1, 5, 10, 18, 20, 36, 50, 67, 69, 100]
PATH_ATTRIBUTES = ["weight", "local-pref", "med", "prepend"]
HELP_CMDS = [
    "router",
    "link",
    "loopback",
    "peer",
    "advertise",
    "static",
    "ibgp-mesh",
    "neighbor",
    "shutdown",
    "cut",
    "repair",
    "send",
]

# Templates for each tool
# They won't let you live when they take over, bruh
PREFIX = ["", "please", "can you", "could you", "help me", "I need to", "I want to"]
ARTICLE = ["a", "one", ""]
# Two-router "noun" phrasings join the pair as "<lead> A <mid> B". Each joiner
# word is independently optional, so the full product yields "between A and B",
# "between A B", "A and B" and the bare "A B".
JOIN_LEAD = ["between", ""]
JOIN_MID = ["and", ""]

# Routers
ROUTER_VERB_ADD = ["add", "create", "make", "spin up", "set up", "establish"]
ROUTER_VERB_REMOVE = ["remove", "delete", "get rid of", "tear down", "destroy"]
ROUTER_EXTRA_VERB_ADD = ["give me", "I need", "I want", "build"]
ROUTER_TARGET = ["router", "device", "node"]
ROUTER_NAME_PREFIX = ["named", "called", "with name", ""]
AS_SPLIT = ["in", "as part of", "", "within"]
AS_PREFIX = ["AS", "autonomous system", "as"]


def templates_create_router() -> list[str]:
    """To create routers, we need a name and an optional asn"""
    templates = []
    starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, ROUTER_VERB_ADD)
    ] + ROUTER_EXTRA_VERB_ADD
    for s, a, t, n in product(starts, ARTICLE, ROUTER_TARGET, ROUTER_NAME_PREFIX):
        # Join words, filtering out empty strings to avoid double spaces
        templates.append(" ".join(filter(None, [s, a, t, n, "{name}"])))
        for as_s, as_p in product(AS_SPLIT, AS_PREFIX):
            phrase = " ".join(filter(None, [s, a, t, n, "{name}", as_s, as_p, "{asn}"]))
            templates.append(phrase)
    return templates


def templates_destroy_router() -> list[str]:
    """To destroy routers, we just need the name."""
    # Damn, one-liner. What will the future me think of this? Good luck...
    return [
        " ".join(filter(None, [p, v, t, "{name}"]))
        for p, v, t in product(PREFIX, ROUTER_VERB_REMOVE, ROUTER_TARGET)
    ]


# Links
# Verb noun + target or verb pair (direct)
LINK_VERB_PAIR = ["connect", "link", "wire up", "plug", "hook up", "cable"]
LINK_VERB_NOUN = ["create", "add", "set up", "make"]
LINK_VERB_UNPAIR = ["disconnect", "unplug", "permanently disconnect"]
LINK_VERB_UNNOUN = ["remove", "delete", "permanently remove"]
LINK_TARGET = ["link", "cable", "connection"]
LINK_CONNECTOR = ["", "to", "and", "with"]
LINK_COST_PREFIX = ["with cost", "cost", "with metric", "metric", "and an igp cost of"]


def templates_connect_routers() -> list[str]:
    """Create a link between two routers, with an optional cost."""
    templates = []
    tails = [" ".join([p, "{cost}"]) for p in LINK_COST_PREFIX]
    # verb-direct: "please connect {router_a} to {router_b} [cost]"
    pair_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, LINK_VERB_PAIR)
    ]
    for s, conn, tail in product(pair_starts, LINK_CONNECTOR, tails):
        head = " ".join([s, "{router_a}", conn, "{router_b}"])
        templates.append(" ".join(filter(None, [head, tail])))
    # verb-noun: "create a link (between) {router_a} (and) {router_b} [cost]"
    noun_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, LINK_VERB_NOUN)
    ]
    for s, a, t, tail, lead, mid in product(
        noun_starts, ARTICLE, LINK_TARGET, tails, JOIN_LEAD, JOIN_MID
    ):
        head = " ".join(filter(None, [s, a, t, lead, "{router_a}", mid, "{router_b}"]))
        templates.append(" ".join(filter(None, [head, tail])))
    return templates


def templates_disconnect_routers() -> list[str]:
    """Remove the link between two routers permanently (slots == tool params)."""
    templates = []
    # verb-direct: "disconnect {router_a} from {router_b}"
    pair_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, LINK_VERB_UNPAIR)
    ]
    for s in pair_starts:
        templates.append(" ".join([s, "{router_a}", "from", "{router_b}"]))
    # verb-noun: "remove the link (between) {router_a} (and) {router_b}"
    noun_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, LINK_VERB_UNNOUN)
    ]
    for s, t, lead, mid in product(noun_starts, LINK_TARGET, JOIN_LEAD, JOIN_MID):
        templates.append(
            " ".join(filter(None, [s, "the", t, lead, "{router_a}", mid, "{router_b}"]))
        )
    return templates


# Loopbacks
LOOPBACK = ["loopback", "loopback interface", "lo0"]
LOOPBACK_VERB_ADD = ["add", "create", "configure", "set up", "new"]
LOOPBACK_PREP_ADD = ["to", "on", "for"]
LOOPBACK_VERB_REMOVE = ["remove", "delete", "drop"]
LOOPBACK_PREP_REMOVE = ["", "from", "on"]


def templates_add_loopback() -> list[str]:
    """Give a router a loopback interface. Only the router name varies."""
    templates = []
    # verb-prep: "add a loopback (on) {router}"
    add_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, LOOPBACK_VERB_ADD)
    ]
    for s, a, lb, prep in product(add_starts, ARTICLE, LOOPBACK, LOOPBACK_PREP_ADD):
        templates.append(" ".join(filter(None, [s, a, lb, prep, "{router}"])))
    # dative: "give {router} a loopback"
    give_starts = [" ".join(filter(None, [p, "give"])) for p in PREFIX]
    for s, a, lb in product(give_starts, ARTICLE, LOOPBACK):
        templates.append(" ".join(filter(None, [s, "{router}", a, lb])))
    return templates


def templates_remove_loopback() -> list[str]:
    """Remove a router's loopback interface. Only the router name varies."""
    templates = []
    rm_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, LOOPBACK_VERB_REMOVE)
    ]
    # verb-prep: "remove the loopback (from) {router}"
    for s, lb, prep in product(rm_starts, LOOPBACK, LOOPBACK_PREP_REMOVE):
        templates.append(" ".join(filter(None, [s, "the", lb, prep, "{router}"])))
    # possessive: "delete {router}'s loopback"
    for s, lb in product(rm_starts, LOOPBACK):
        templates.append(" ".join(filter(None, [s, "{router}'s", lb])))
    return templates


# BGP sessions
BGP_VERB_OPEN = [
    "open",
    "establish",
    "set up",
    "create",
    "configure",
    "bring up",
    "start",
]
BGP_VERB_CLOSE = ["close", "tear down", "remove", "drop", "destroy", "end"]
BGP_SESSION = ["bgp session", "peering", "peering session", "bgp peering"]
BGP_PAIR_CONNECTOR = ["", "with", "and", "to"]


def templates_open_bgp_session() -> list[str]:
    """Open a BGP peering between two routers."""
    templates = []
    # noun-between: "open a peering (between) {router_a} (and) {router_b}"
    noun_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, BGP_VERB_OPEN)
    ]
    for s, a, sess, lead, mid in product(
        noun_starts, ARTICLE, BGP_SESSION, JOIN_LEAD, JOIN_MID
    ):
        templates.append(
            " ".join(filter(None, [s, a, sess, lead, "{router_a}", mid, "{router_b}"]))
        )
    # pair: "peer {router_a} with {router_b}"
    pair_starts = [" ".join(filter(None, [p, "peer"])) for p in PREFIX]
    for s, conn in product(pair_starts, BGP_PAIR_CONNECTOR):
        templates.append(" ".join([s, "{router_a}", conn, "{router_b}"]))
    # neighbors idiom: "make {router_a} (and) {router_b} bgp neighbors"
    for p, mid in product(PREFIX, JOIN_MID):
        s = " ".join(filter(None, [p, "make"]))
        templates.append(
            " ".join(
                filter(None, [s, "{router_a}", mid, "{router_b}", "bgp neighbors"])
            )
        )
    return templates


def templates_close_bgp_session() -> list[str]:
    """Tear down the BGP peering between two routers."""
    templates = []
    noun_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, BGP_VERB_CLOSE)
    ]
    # noun-between: "close the bgp session (between) {router_a} (and) {router_b}"
    for s, sess, lead, mid in product(noun_starts, BGP_SESSION, JOIN_LEAD, JOIN_MID):
        templates.append(
            " ".join(
                filter(None, [s, "the", sess, lead, "{router_a}", mid, "{router_b}"])
            )
        )
    return templates


# Advertise/Withdraw
PREFIX_NOUN = ["", "network", "prefix", "route"]
ADV_VERB = ["advertise", "announce", "originate", "inject", "publish"]
ADV_VERB_3SG = ["advertises", "announces", "originates", "injects", "publishes"]
ADV_PREP = ["from", "on", "out of"]
WDR_VERB = [
    "withdraw",
    "unadvertise",
    "stop advertising",
    "stop announcing",
    "stop originating",
]
WDR_VERB_3SG = [
    "withdraws",
    "unadvertises",
    "stops advertising",
    "stops announcing",
    "stops originating",
]
WDR_PREP = ["from", "on"]
CAUSATIVE = ["have", "make"]


def templates_advertise_prefix() -> list[str]:
    """Originate a prefix into BGP from a router. Slots {router}, {prefix}."""
    templates = []
    # verb-first: "advertise network {prefix} from {router}"
    starts = [" ".join(filter(None, [p, v])) for p, v in product(PREFIX, ADV_VERB)]
    for s, noun, prep in product(starts, PREFIX_NOUN, ADV_PREP):
        templates.append(
            " ".join(filter(None, [s, noun, "{prefix}", prep, "{router}"]))
        )
    # causative: "have {router} advertise network {prefix}"
    for c, v, noun in product(CAUSATIVE, ADV_VERB, PREFIX_NOUN):
        templates.append(" ".join(filter(None, [c, "{router}", v, noun, "{prefix}"])))
    # active: "{router} advertises network {prefix}"
    for v, noun in product(ADV_VERB_3SG, PREFIX_NOUN):
        templates.append(" ".join(filter(None, ["{router}", v, noun, "{prefix}"])))
    return templates


def templates_withdraw_prefix() -> list[str]:
    """Stop originating a prefix from a router. Slots {router}, {prefix}."""
    templates = []
    # verb-first: "stop advertising {prefix} from {router}"
    starts = [" ".join(filter(None, [p, v])) for p, v in product(PREFIX, WDR_VERB)]
    for s, noun, prep in product(starts, PREFIX_NOUN, WDR_PREP):
        templates.append(
            " ".join(filter(None, [s, noun, "{prefix}", prep, "{router}"]))
        )
    # causative: "make {router} stop advertising {prefix}"
    for c, v, noun in product(CAUSATIVE, WDR_VERB, PREFIX_NOUN):
        templates.append(" ".join(filter(None, [c, "{router}", v, noun, "{prefix}"])))
    # active: "{router} stops advertising {prefix}"
    for v, noun in product(WDR_VERB_3SG, PREFIX_NOUN):
        templates.append(" ".join(filter(None, ["{router}", v, noun, "{prefix}"])))
    return templates


# Static routes
STATIC_VERB_ADD = ["add", "create", "install", "configure", "set up"]
STATIC_VERB_REMOVE = ["remove", "delete", "drop", "clear"]
STATIC_ROUTE = ["static route", "static", "route"]
STATIC_DEST = ["to", "for", "toward"]  # connector before {prefix}
STATIC_VIA = ["via", "next-hop", "through", "pointing to"]  # before {next_hop}
STATIC_ON = ["on", "to"]  # before {router}


def templates_add_static_route() -> list[str]:
    """Install a static route. Slots {router}, {prefix}, {next_hop}."""
    templates = []
    # verb-noun: "add a static route on {router} to {prefix} via {next_hop}"
    starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, STATIC_VERB_ADD)
    ]
    for s, a, r, on, dest, via in product(
        starts, ARTICLE, STATIC_ROUTE, STATIC_ON, STATIC_DEST, STATIC_VIA
    ):
        templates.append(
            " ".join(
                filter(
                    None, [s, a, r, on, "{router}", dest, "{prefix}", via, "{next_hop}"]
                )
            )
        )
    # dative: "give {router} a static route to {prefix} via {next_hop}"
    give_starts = [" ".join(filter(None, [p, "give"])) for p in PREFIX]
    for s, a, r, dest, via in product(
        give_starts, ARTICLE, STATIC_ROUTE, STATIC_DEST, STATIC_VIA
    ):
        templates.append(
            " ".join(
                filter(None, [s, "{router}", a, r, dest, "{prefix}", via, "{next_hop}"])
            )
        )
    # route-verb: "route {prefix} through {next_hop} on {router}"
    for p, via, on in product(PREFIX, STATIC_VIA, STATIC_ON):
        s = " ".join(filter(None, [p, "route"]))
        templates.append(
            " ".join(filter(None, [s, "{prefix}", via, "{next_hop}", on, "{router}"]))
        )
    return templates


def templates_remove_static_route() -> list[str]:
    """Remove a static route. Slots {router}, {prefix}."""
    templates = []
    starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, STATIC_VERB_REMOVE)
    ]
    # verb-noun: "remove the static route to {prefix} on {router}"
    for s, r, dest, on in product(starts, STATIC_ROUTE, STATIC_DEST, STATIC_ON):
        templates.append(
            " ".join(filter(None, [s, "the", r, dest, "{prefix}", on, "{router}"]))
        )
    # possessive: "delete {router}'s static route to {prefix}"
    for s, r, dest in product(starts, STATIC_ROUTE, STATIC_DEST):
        templates.append(" ".join(filter(None, [s, "{router}'s", r, dest, "{prefix}"])))
    return templates


# iBGP mesh. Mesh nouns carry their correct article as (article, noun) -- "an
# ibgp" vs "a full mesh" disagree -- and the article is optional, so each noun
# yields both "build an ibgp mesh ..." and the bare "build ibgp mesh ...".
IBGP_VERB_BUILD = ["build", "create", "set up", "configure", "establish", "make"]
IBGP_VERB_REMOVE = ["remove", "tear down", "delete", "drop", "destroy", "get rid of"]
IBGP_MESH_BUILD = [
    ("an", "ibgp mesh"),
    ("an", "ibgp full mesh"),
    ("a", "full ibgp mesh"),
    ("a", "full mesh of ibgp"),
    ("", "ibgp everywhere"),
]
IBGP_MESH_REMOVE = [
    ("the", "ibgp mesh"),
    ("the", "ibgp full mesh"),
    ("the", "full ibgp mesh"),
    ("the", "full mesh of ibgp"),
    ("", "ibgp"),
]
IBGP_AS_PREP = ["in", "across", "for", "within", "inside", "throughout"]


def _ibgp_templates(verbs: list[str], meshes: list[tuple[str, str]]) -> list[str]:
    """Cross verb x (optional article + mesh noun) x AS phrasing. {asn} required."""
    templates = []
    starts = [" ".join(filter(None, [p, v])) for p, v in product(PREFIX, verbs)]
    for s, (art, mesh) in product(starts, meshes):
        for a in dict.fromkeys([art, ""]):  # article optional (deduped, ordered)
            base = " ".join(filter(None, [s, a, mesh]))
            for prep, asp in product(IBGP_AS_PREP, AS_PREFIX):
                templates.append(" ".join([base, prep, asp, "{asn}"]))
    return templates


def templates_build_ibgp_mesh() -> list[str]:
    """Full-mesh iBGP in an AS. Slot {asn} is required."""
    return _ibgp_templates(IBGP_VERB_BUILD, IBGP_MESH_BUILD)


def templates_remove_ibgp_mesh() -> list[str]:
    """Tear down the iBGP mesh in an AS. Slot {asn} is required."""
    return _ibgp_templates(IBGP_VERB_REMOVE, IBGP_MESH_REMOVE)


# Per-neighbor BGP policy. set_neighbor_policy(router, neighbor, attribute,
# value) and clear_neighbor_policy(router, neighbor, attribute). The four valued
# attributes fill {attribute} from PATH_ATTRIBUTES; next-hop-self takes no value
# and renders via the value-less {nhs} alias slot (see _SLOT_ALIASES), so it
# never lands in a "set X to {value}" frame.
POLICY_SET_VERB = ["set", "configure", "change", "adjust"]
POLICY_NHS_VERB = ["set", "enable", "turn on", "configure"]
POLICY_CLEAR_VERB = ["clear", "reset", "remove", "delete", "unset", "revert"]
POLICY_CAUSATIVE = ["have", "make"]
POLICY_TO = ["to", ""]  # between {attribute} and {value}
POLICY_ON_ROUTER = ["on", "at", ""]  # before {router} (the policy owner)
POLICY_FOR_NEIGHBOR = ["for", "toward", "to", "facing", ""]  # before {neighbor}
NEIGHBOR_NOUN = ["", "neighbor", "peer"]
POLICY_VALUES = [0, 1, 2, 3, 5, 10, 50, 100, 150, 200, 300, 500, 1000]


def templates_set_neighbor_policy() -> list[str]:
    """Set a per-neighbor BGP policy. Slots {router}, {neighbor}, {attribute},
    {value}; next-hop-self carries no value and arrives via the {nhs} alias."""
    templates = []
    set_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, POLICY_SET_VERB)
    ]
    nhs_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, POLICY_NHS_VERB)
    ]
    # valued attributes ----------------------------------------------------
    # verb-first: "set the {attribute} to {value} on {router} for peer {neighbor}"
    for s, to, on, fr, noun in product(
        set_starts, POLICY_TO, POLICY_ON_ROUTER, POLICY_FOR_NEIGHBOR, NEIGHBOR_NOUN
    ):
        templates.append(" ".join(filter(None, [
            s, "{attribute}", to, "{value}", on, "{router}", fr, noun, "{neighbor}",
        ])))
    # causative: "have {router} use {attribute} {value} toward {neighbor}"
    for c, fr, noun in product(POLICY_CAUSATIVE, POLICY_FOR_NEIGHBOR, NEIGHBOR_NOUN):
        templates.append(" ".join(filter(None, [
            c, "{router}", "use", "{attribute}", "{value}", fr, noun, "{neighbor}",
        ])))
    # possessive: "set {neighbor}'s {attribute} to {value} on {router}"
    for s, to, on in product(set_starts, POLICY_TO, POLICY_ON_ROUTER):
        templates.append(" ".join(filter(None, [
            s, "{neighbor}'s", "{attribute}", to, "{value}", on, "{router}",
        ])))
    # next-hop-self (value-less, via {nhs}) --------------------------------
    for s, on, fr, noun in product(
        nhs_starts, POLICY_ON_ROUTER, POLICY_FOR_NEIGHBOR, NEIGHBOR_NOUN
    ):
        templates.append(" ".join(filter(None, [
            s, "{nhs}", on, "{router}", fr, noun, "{neighbor}",
        ])))
    for c, fr, noun in product(POLICY_CAUSATIVE, POLICY_FOR_NEIGHBOR, NEIGHBOR_NOUN):
        templates.append(" ".join(filter(None, [
            c, "{router}", "use", "{nhs}", fr, noun, "{neighbor}",
        ])))
    for s, on in product(nhs_starts, POLICY_ON_ROUTER):
        templates.append(
            " ".join(filter(None, [s, "{neighbor}'s", "{nhs}", on, "{router}"]))
        )
    return templates


def templates_clear_neighbor_policy() -> list[str]:
    """Revert a per-neighbor BGP policy to default. Slots {router}, {neighbor},
    {attribute}; next-hop-self arrives via the {nhs} alias."""
    templates = []
    starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, POLICY_CLEAR_VERB)
    ]
    for attr in ("{attribute}", "{nhs}"):
        # verb-first: "clear the {attribute} on {router} for peer {neighbor}"
        for s, the, on, fr, noun in product(
            starts, ("the", ""), POLICY_ON_ROUTER, POLICY_FOR_NEIGHBOR, NEIGHBOR_NOUN
        ):
            templates.append(" ".join(filter(None, [
                s, the, attr, on, "{router}", fr, noun, "{neighbor}",
            ])))
        # possessive: "reset {router}'s {attribute} toward {neighbor}"
        for s, fr, noun in product(starts, POLICY_FOR_NEIGHBOR, NEIGHBOR_NOUN):
            templates.append(" ".join(filter(None, [
                s, "{router}'s", attr, fr, noun, "{neighbor}",
            ])))
    return templates


# Interface admin state. shutdown_interface(router, peer) and start_interface(
# router, peer) -- the latter is "no shutdown". Same two-router frame, only the
# verb differs, so both share _iface_templates. {peer} is a ROUTER_SLOTS slot,
# so it is drawn distinct from {router}.
IFACE = ["interface", "link", "port"]
IFACE_ON = ["on", "at", ""]  # before {router} (the owner)
IFACE_TOWARD = ["facing", "toward", "towards", "to", ""]  # before {peer}
SHUT_VERB = [
    "shut down",
    "shutdown",
    "disable",
    "administratively disable",
    "take down",
    "bring down",
]
START_VERB = [
    "bring up",
    "start",
    "re-enable",
    "enable",
    "turn on",
    "no shutdown",
    "unshut",
]


def _iface_templates(verbs: list[str]) -> list[str]:
    """Toggle {router}'s interface toward {peer}. Verb-parameterised."""
    templates = []
    starts = [" ".join(filter(None, [p, v])) for p, v in product(PREFIX, verbs)]
    # possessive: "shut down {router}'s interface facing {peer}"
    for s, iface, toward in product(starts, IFACE, IFACE_TOWARD):
        templates.append(" ".join([s, "{router}'s", iface, toward, "{peer}"]))
    # on-router: "disable the interface on {router} toward {peer}"
    for s, the, iface, on, toward in product(
        starts, ("the", ""), IFACE, IFACE_ON, IFACE_TOWARD
    ):
        templates.append(" ".join(filter(None, [
            s, the, iface, on, "{router}", toward, "{peer}",
        ])))
    # hyphenated pair: "shut down the {router}-{peer} link"
    for s, the, iface in product(starts, ("the", ""), IFACE):
        templates.append(" ".join(filter(None, [s, the, "{router}-{peer}", iface])))
    return templates


def templates_shutdown_interface() -> list[str]:
    """Administratively disable {router}'s interface toward {peer}."""
    return _iface_templates(SHUT_VERB)


def templates_start_interface() -> list[str]:
    """Bring {router}'s shut-down interface toward {peer} back up."""
    return _iface_templates(START_VERB)


# Cut/repair a link
# Simulate a cable fault between two routers, then repair it.
# Have many vivid scenarios for fun.
CABLE = ["cable", "link", "line", "fiber", "fibre", "connection", "wire"]
CUT_VERB = [
    "cut",
    "break",
    "sever",
    "snip",
    "slice through",
    "knock out",
    "take down",
    "kill",
    "disrupt",
    "fault",
]
CUT_SIM = [
    "simulate an outage on",
    "simulate a fault on",
    "simulate a cut on",
    "inject a fault on",
    "fake an outage on",
]
CUT_AGENT = [
    "a shark",
    "a backhoe",
    "an excavator",
    "a squirrel",
    "a rat",
    "a ship's anchor",
    "the storm",
    "lightning",
    "a construction crew",
    "a careless intern",
    "a vandal",
    "a falling tree",
    "a rodent",
    "a drunk driver",
    "an earthquake",
    "I",
]
CUT_AGENT_ACTION = [
    "ate",
    "bit through",
    "chewed through",
    "sliced",
    "cut",
    "severed",
    "dug up",
    "snapped",
    "destroyed",
    "took out",
    "knocked out",
    "ripped out",
]
REPAIR_VERB = [
    "repair",
    "fix",
    "restore",
    "reconnect",
    "bring back",
    "patch",
    "mend",
    "splice",
    "re-establish",
    "bring back up",
]
REPAIR_AGENT = [
    "the technician",
    "the field tech",
    "the repair crew",
    "the ISP",
    "a lineman",
    "the on-call engineer",
    "the contractor",
    "I",
]
REPAIR_AGENT_ACTION = [
    "fixed",
    "repaired",
    "restored",
    "spliced back together",
    "reconnected",
    "patched up",
    "brought back",
    "revived",
]
REPAIR_STATE = [
    "is back",
    "is back up",
    "is fixed",
    "is repaired",
    "has been repaired",
    "has been restored",
    "is working again",
    "is up again",
]


def _link_refs() -> list[str]:
    """Noun phrases naming the link between {router_a} and {router_b}.

    Covers "[the] <cable> between A and B" (with the joiners independently
    optional) and the hyphenated "[the] A-B <cable>"."""
    refs = []
    for the, cable in product(("the", ""), CABLE):
        for lead, mid in product(JOIN_LEAD, JOIN_MID):
            refs.append(" ".join(filter(
                None, [the, cable, lead, "{router_a}", mid, "{router_b}"]
            )))
        refs.append(" ".join(filter(None, [the, "{router_a}-{router_b}", cable])))
    return refs


def templates_cut_link() -> list[str]:
    """Disrupt the cable between {router_a} and {router_b} (a fault, not deletion)."""
    templates = []
    refs = _link_refs()
    # imperative: "cut the cable between {router_a} and {router_b}"
    starts = [" ".join(filter(None, [p, v])) for p, v in product(PREFIX, CUT_VERB)]
    for s, ref in product(starts, refs):
        templates.append(" ".join([s, ref]))
    # simulate: "simulate an outage on the {router_a}-{router_b} link"
    for sim, ref in product(CUT_SIM, refs):
        templates.append(" ".join([sim, ref]))
    # narrative: "a shark ate the cable between {router_a} and {router_b}"
    for agent, action, ref in product(CUT_AGENT, CUT_AGENT_ACTION, refs):
        templates.append(" ".join([agent, action, ref]))
    return templates


def templates_repair_link() -> list[str]:
    """Repair the previously-cut cable between {router_a} and {router_b}."""
    templates = []
    refs = _link_refs()
    # imperative: "repair the {router_a}-{router_b} link"
    starts = [" ".join(filter(None, [p, v])) for p, v in product(PREFIX, REPAIR_VERB)]
    for s, ref in product(starts, refs):
        templates.append(" ".join([s, ref]))
    # narrative: "the technician fixed the cable between {router_a} and {router_b}"
    for agent, action, ref in product(REPAIR_AGENT, REPAIR_AGENT_ACTION, refs):
        templates.append(" ".join([agent, action, ref]))
    # status report: "the link between {router_a} and {router_b} is back up"
    for ref, state in product(refs, REPAIR_STATE):
        templates.append(" ".join([ref, state]))
    return templates


# Send a test packet. send_packet(router, destination): {router} is the source,
# {destination} a host IP (filled by the _random_host generator, registered as a
# slot). Three frames: send-from-to, ping-from, and active voice ({router} as
# the subject -- "R1 pings {destination}").
SEND_VERB = [
    "send a packet", "send a test packet", "send a ping",
    "shoot a packet", "send traffic", "send some traffic",
]
SEND_TO = ["to", "toward", "destined for", "headed for"]  # before {destination}
PING_VERB = ["ping", "traceroute", "probe", "trace the route to", "trace the path to"]
PING_3SG = ["pings", "traceroutes", "probes", "reaches", "hits"]  # direct object
SEND_3SG = ["sends", "shoots"]  # 3sg verb, crossed with SEND_OBJ
SEND_OBJ = ["a packet", "a test packet", "a ping", "traffic", "some traffic"]


def templates_send_packet() -> list[str]:
    """Send a test packet from {router} to host {destination}."""
    templates = []
    # send-from-to: "send a test packet from {router} to {destination}"
    send_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, SEND_VERB)
    ]
    for s, to in product(send_starts, SEND_TO):
        templates.append(" ".join([s, "from", "{router}", to, "{destination}"]))
    # ping-from: "ping {destination} from {router}"
    ping_starts = [
        " ".join(filter(None, [p, v])) for p, v in product(PREFIX, PING_VERB)
    ]
    for s in ping_starts:
        templates.append(" ".join([s, "{destination}", "from", "{router}"]))
    # active voice: "{router} pings {destination}"
    for v in PING_3SG:
        templates.append(" ".join(["{router}", v, "{destination}"]))
    # active "{router} sends a packet to {destination}" + dative
    # "{router} sends {destination} a packet", over the same verb x object cross
    for v, obj in product(SEND_3SG, SEND_OBJ):
        for to in SEND_TO:
            templates.append(" ".join(["{router}", v, obj, to, "{destination}"]))
        templates.append(" ".join(["{router}", v, "{destination}", obj]))
    return templates


# Show help. show_help(command=""): no-arg forms ask for the whole list (args
# {}), topic forms name a {command} from HELP_CMDS (registered as a slot pool).
HELP_NO_ARG = [
    "help", "show help", "show me the commands", "list the available commands",
    "list commands", "what commands are there?", "what can I do here?",
    "what can I do?", "show the command list", "give me the command list",
    "I'm lost, what now?", "commands", "menu", "?", "h",
]
HELP_TOPIC = [
    "help {command}",
    "help with {command}",
    "help on {command}",
    "how does {command} work?",
    "how do I use {command}?",
    "how to use {command}",
    "what does {command} do?",
    "what is {command} for?",
    "what's the syntax for {command}?",
    "explain {command}",
    "explain the {command} command",
    "show help for {command}",
    "tell me about the {command} command",
    "{command} help",
]


def templates_show_help() -> list[str]:
    """Show the full command list (no args) or help for one {command}."""
    return HELP_NO_ARG + HELP_TOPIC


# Random argument generators
def _random_ip_prefix(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.7:
        net = rng.choice([10, 172, 192])
        return f"{net}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.0/24"
    if roll < 0.85:
        return f"10.0.0.{rng.randint(1, 254)}/32"
    return f"10.0.0.0/{rng.choice([8, 16, 22])}"


def _random_host(rng: random.Random) -> str:
    return rng.choice(
        [f"10.0.0.{rng.randint(1, 254)}", f"192.168.{rng.randint(0, 255)}.{rng.randint(1, 254)}"]
    )


# --- Template engine -------------------------------------------------------
# A combinatorial `templates_*()` returns thousands of phrasings with `{slot}`
# placeholders whose names match the target tool's parameters. `_realize` picks
# one and fills it, deriving the arguments dict from exactly the slots present
# (via `string.Formatter`) -- so optional args appear only when the chosen
# template used them. Slots naming a hostname are filled jointly so a pair like
# router_a/router_b is always distinct.
ROUTER_SLOTS = {"name", "router", "router_a", "router_b", "neighbor", "peer"}
# Scalar slots: a fixed pool to `rng.choice` from, or a generator `rng -> value`
# for slots whose space is too large to enumerate (e.g. CIDR prefixes).
_SLOT_VALUES = {
    "asn": ASNS,
    "cost": COSTS,
    "attribute": PATH_ATTRIBUTES,
    "value": POLICY_VALUES,
    "command": HELP_CMDS,
}
_SLOT_GENERATORS = {
    "prefix": _random_ip_prefix,
    "next_hop": _random_host,
    "destination": _random_host,
}
# Alias slots: a slot whose name is *not* a tool parameter. It injects a fixed
# (param, value) into the arguments while the text shows one of several display
# spellings -- letting a template family pin an attribute that takes no value
# (next-hop-self) without polluting the {attribute} pool or the {value} frame.
_SLOT_ALIASES = {
    "nhs": ("attribute", "next-hop-self", ["next-hop-self", "next hop self", "nhs"]),
}


def _slots(template: str) -> list[str]:
    """Field names in `template`, de-duplicated, in order of first appearance."""
    seen: dict[str, None] = {}
    for _, field, _, _ in Formatter().parse(template):
        if field:
            seen.setdefault(field, None)
    return list(seen)


def _realize(rng: random.Random, templates: list[str]) -> tuple[str, dict]:
    """Pick one template and fill it, returning (query, arguments).

    `fmt` holds the values spliced into the text; `args` holds the tool-call
    arguments. They diverge only for alias slots, where the text shows a display
    spelling while the arguments carry the canonical (param, value)."""
    template = rng.choice(templates)
    slots = _slots(template)
    fmt: dict = {}
    args: dict = {}
    router_slots = [s for s in slots if s in ROUTER_SLOTS]
    for slot, host in zip(router_slots, rng.sample(ROUTERS, len(router_slots))):
        fmt[slot] = args[slot] = host
    for slot in slots:
        if slot in fmt:
            continue
        if slot in _SLOT_ALIASES:
            param, value, displays = _SLOT_ALIASES[slot]
            fmt[slot] = rng.choice(displays)
            args[param] = value
        elif slot in _SLOT_VALUES:
            fmt[slot] = args[slot] = rng.choice(_SLOT_VALUES[slot])
        else:
            fmt[slot] = args[slot] = _SLOT_GENERATORS[slot](rng)
    return template.format(**fmt), args


# Every tool's phrasings come from the combinatorial template engine: a name ->
# `templates_*()` map. `build_conversations` realises draws via `_realize`.
_TEMPLATES = {
    "add_router": templates_create_router,
    "remove_router": templates_destroy_router,
    "connect_routers": templates_connect_routers,
    "disconnect_routers": templates_disconnect_routers,
    "add_loopback": templates_add_loopback,
    "remove_loopback": templates_remove_loopback,
    "open_bgp_session": templates_open_bgp_session,
    "close_bgp_session": templates_close_bgp_session,
    "advertise_prefix": templates_advertise_prefix,
    "withdraw_prefix": templates_withdraw_prefix,
    "add_static_route": templates_add_static_route,
    "remove_static_route": templates_remove_static_route,
    "build_ibgp_mesh": templates_build_ibgp_mesh,
    "remove_ibgp_mesh": templates_remove_ibgp_mesh,
    "set_neighbor_policy": templates_set_neighbor_policy,
    "clear_neighbor_policy": templates_clear_neighbor_policy,
    "shutdown_interface": templates_shutdown_interface,
    "start_interface": templates_start_interface,
    "cut_link": templates_cut_link,
    "repair_link": templates_repair_link,
    "send_packet": templates_send_packet,
    "show_help": templates_show_help,
}


def _conversation(query: str, name: str, arguments: dict) -> dict:
    """Wrap one (query, tool call) pair as a FunctionGemma chat conversation."""
    return {
        "messages": [
            {"role": "developer", "content": DEVELOPER_PROMPT},
            {"role": "user", "content": query},
            {
                "role": "assistant",
                "tool_calls": [
                    {"type": "function", "function": {"name": name, "arguments": arguments}}
                ],
            },
        ]
    }


def _per_tool_targets(built: dict[str, list[str]], size: str) -> dict[str, int]:
    """How many unique examples to draw per tool.

    A command's share scales with the (sqrt-compressed) size of its template
    space -- richer commands appear more, tiny ones (show_help) don't dominate
    by repetition -- with a floor so every command stays learnable. MAXIMUM
    asks for everything (capped, since generator slots are ~infinite).
    """
    if size == "MAXIMUM":
        return {name: _MAXIMUM_CAP for name in built}
    base = _SIZE_BASE[size]
    weights = {name: len(t) ** _RATIO_EXPONENT for name, t in built.items()}
    mean_w = sum(weights.values()) / len(weights)
    return {name: max(base // 2, round(base * w / mean_w)) for name, w in weights.items()}


def build_conversations(size: str = "MEDIUM", seed: int = 42) -> list[dict]:
    """Build a shuffled, de-duplicated list of training conversations.

    `size` (SMALL/MEDIUM/LARGE/MAXIMUM) scales the whole set; each command's
    share is proportional to its template-space size. Identical phrasings within
    a command are dropped, and a command stops early once its space is drained
    (a long run of duplicate draws).
    """
    size = size.upper()
    if size not in SIZES:
        raise ValueError(f"size must be one of {SIZES}, got {size!r}")
    rng = random.Random(seed)
    built = {name: fn() for name, fn in _TEMPLATES.items()}  # built once, reused for weights
    targets = _per_tool_targets(built, size)
    convs: list[dict] = []
    for fn in TOOLS:
        name = fn.__name__
        templates, target = built[name], targets[name]
        seen: set[str] = set()
        miss_streak = 0
        while len(seen) < target and miss_streak < _EXHAUST_STREAK:
            query, arguments = _realize(rng, templates)
            if query in seen:
                miss_streak += 1
                continue
            miss_streak = 0
            seen.add(query)
            convs.append(_conversation(query, name, arguments))
    rng.shuffle(convs)
    return convs


if __name__ == "__main__":
    # Quick eyeball: `python -m gemma.dataset` prints a LARGE set + per-tool spread.
    import json
    from collections import Counter

    sample = build_conversations(size="LARGE")
    counts = Counter(c["messages"][2]["tool_calls"][0]["function"]["name"] for c in sample)
    print(f"{len(sample)} conversations ({len(TOOLS)} tools), LARGE")
    for name, n in counts.most_common():
        print(f"  {name:22s} {n}")
    print("\nsamples:")
    for conv in sample[:6]:
        user = conv["messages"][1]["content"]
        call = conv["messages"][2]["tool_calls"][0]["function"]
        print(f"  {user!r}\n    -> {call['name']}({json.dumps(call['arguments'])})")
