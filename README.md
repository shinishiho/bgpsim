# BGP Simulator

A simple BGP Simulator written in Python. Targets networking beginners and enthusiasts, and me.

## Features

- Built with [Textual](https://github.com/textualize/textual) -- fully functional TUI.
- Beginner-friendly design and commands.
- Tick-based -- events are recorded each tick (timestep), including link state change, BGP update, etc.

## Assumption/Abstraction

- All routers speak BGP (eBGP or iBGP full-mesh). By default, they are assigned to AS 1.
- One network consists of only two routers.
By default, it will create a /24 network within `192.168.0.0/16`, and routers will have `.1` and `.2` address.
- Loopback interfaces use `/32` subnets of `10.0.0.0/24`.
- Cisco attributes and defaults.

## Limitations

- Only IPv4 is supported.
- No redistribution since there is no other IGP.
- No import/export policies.
- No route deflectors, confederations.

## Acknowledgment

- Networklessons.com for beautifully laid out lectures about BGP basics, mechanisms, attributes, etc.
(that said, access is limited pass chapter 1, since I'm not a member).
- Claude contributes ~50% of the code, mainly including refactors, logic bug fix, realistic BGP compliance, UI, etc.

