---
name: redzone-home-network
description: RedZone hardware network facts — home LAN 100.110.165.x, TV MAC, Mac travels to school
metadata:
  type: project
---

Facts as of 2026-11-03:

- **Home network.** The sticks were at 100.110.165.150 (Fire TV Stick 4K Plus, HDMI2) and .181 (Fire TV Stick 4K 2023, HDMI1).
- **TV.** Samsung "6 Series" 55". MAC 8c:79:f5:8e:e2:5f. The user said its IP was .151, but that was never verified.
- **Yesterday's TV failure.** Ports 8001/8002/55000/8080 timed out from the Mac, there was no ARP entry, and a ping from a stick also failed. So the IP was probably wrong, or the TV was on another band or guest network with isolation.
- **The Mac travels.** Nick takes the MacBook Air to school, where it's on a campus network (10.23.x.x) and can't reach the home sticks at all. "Sticks offline" while he's at school means the Mac isn't home; it does not mean the sticks lost power.

**Why:** On Nov 3 I wrongly blamed the sticks' power while the Mac was at school.

**How to apply:** Check the Mac's network (`ifconfig`, router address) before diagnosing sticks or the TV. Find the TV by its MAC on the home LAN rather than trusting a typed IP.
