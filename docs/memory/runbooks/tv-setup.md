# TV setup (TV-side input switching)

Detail: [instant-switching.md](../../instant-switching.md) and steps 2–5 of [tonight-checklist.md](../../archive/tonight-checklist.md). Needs Nick at home, ~5 minutes.

1. Mac on the home Wi-Fi, Tailscale quit; `.venv/bin/python -m redzone.network` says **home**.
2. On the TV: Anynet+ (HDMI-CEC) On; Network > Expert Settings > IP Remote On, Power On with Mobile On. Note the TV's IP.
3. `.venv/bin/python scripts/tv_setup.py` (finds the TV by MAC `8c:79:f5:8e:e2:5f`). Press **Allow** on the TV. Not found: add `--ip <TV IP>`.
4. Optional, archived Nov 11: `archive/scripts/multiview_probe.py` times YouTube TV Multiview on the hidden stick. `git mv` it and `archive/redzone/tuners/multiview.py` back first ([archive/README.md](../../../archive/README.md)).
5. Tokens land in `data/` (never git). Results go in docs/status.md.
