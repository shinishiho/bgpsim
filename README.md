# BGP Simulator

A simple BGP Simulator written in Python. Targets networking beginners and enthusiasts, and me.

## Features

### Backbone

- Language: Python
- Interface: Terminal, JSON (for API/WebUI)
- Simulator: tick-based, event-based

#### Classes

- World: contains routers, links, BGP sessions, prefixes, policies, timer
- Router: name(str), asn(int)
- Link: router1(router), router2(router), cost(int), state(bool)
- BGP session: localRouter(router), peerRouter(router), type("eBGP","iBGP"), holdTime(int), keepAliveTime(int), importPolicies(policy[]), exportPolicies(policy[]), nextHopSelf(bool), enabled(bool)
- BGP route: prefix(str,ip-like), nextHop(str,ip-like), origin(IGP,EGP,INCOMPLETE), asPath(int[]), localPref(int), med(int), source.type(local,iBGP,eBGP), source.router(router), source.session(session)
- BGP state: router(router), adjRibIn(<session,route[]>), locRib(<session,route>), adjRibOut(<session,route[]>)
- BGP message: one of OpenMessage, KeepAliveMessage, UpdateMessage, NotificationMessage

## Structure

