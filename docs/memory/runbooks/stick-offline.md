# A stick looks offline

1. **Is the Mac home?** `.venv/bin/python -m redzone.network`. At school (10.23.x) the sticks always look offline; that's not a fault.
2. **Tailscale?** If it names a route problem, quit Tailscale or run `.venv/bin/python -m redzone.network --fix`.
3. **Power:** sticks on wall adapters, not TV USB (TV USB cuts power when the TV sleeps).
4. **Reconnect:** `adb disconnect 100.110.165.150 && adb connect 100.110.165.150` (or `.181`). The watchdog and supervisor already retry; a breaker stops hammering a hung stick.
5. Still dead: the app runs on one stick; tell the coordinator. Sticks: `.150` = HDMI1 (CEC 0x1000), `.181` = HDMI2 (CEC 0x2000) (confirmed 2026-11-03).
