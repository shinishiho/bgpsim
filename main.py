from ipaddress import IPv4Address

from models import World
from models.packet import Packet

def main():
    world = World()

    r1 = world.routers.create()
    r2 = world.routers.create()
    r3 = world.routers.create()
    r4 = world.routers.create()

    r1_r2 = world.links.create(r1, r2)
    r2_r3 = world.links.create(r2, r3)
    r3_r4 = world.links.create(r3, r4)
    print("The network address of r1 and r2 is:", r1_r2.network.network_address)
    print("The network address of r2 and r3 is:", r2_r3.network.network_address)
    print("The network address of r3 and r4 is:", r3_r4.network.network_address)

    r1.add_static_route(
        network=r2_r3.network,
        next_hop=r1_r2.get_peer_ip(r2)
    )

    r1.add_static_route(
        network=r3_r4.network,
        next_hop=r1_r2.get_peer_ip(r2)
    )

    r2.add_static_route(
        network=r3_r4.network,
        next_hop=r2_r3.get_peer_ip(r3)
    )

    r3_r4.state = False

    packet = Packet(
        src=IPv4Address("192.168.0.1"),
        dst=IPv4Address("192.168.2.2"),
        payload="Hello, World!"
    )

    print(r1.forward(packet))


if __name__ == "__main__":
    main()
