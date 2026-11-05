#!/usr/bin/env bash
# TNF feed race (tracker item 4): run on a Colab / Kaggle CPU box from ~8:00 PM ET. NEVER on the Mac.
# Starts the Polymarket jump trigger and the ESPN pollers side by side, on the same NTP-synced clock.
#   bash scripts/markets/run_race.sh            # JAX@BAL, 5 h
set -euo pipefail
DATE=${DATE:-2026-11-05}; TEAMS=${TEAMS:-JAX,BAL}; HOURS=${HOURS:-5}
pip -q install websockets requests ntplib >/dev/null
mkdir -p out/race
clock() {  # NTP offset of this box at start and end, so the grader can correct it (UDP 123 may be blocked: then null)
  python - "$1" <<'PY' >> out/race/clock.jsonl
import json, sys, time
try:
    import ntplib
    r = ntplib.NTPClient().request("time.google.com", version=3, timeout=5)
    off = round(r.offset, 4)
except Exception as e:  # noqa: BLE001
    off = None
print(json.dumps({"when": sys.argv[1], "t": time.time(), "ntp_offset_s": off}))
PY
}
clock start
python scripts/markets/poly_live_trigger.py --league nfl --date "$DATE" --out out/markets_live --hours "$HOURS" \
  > out/race/poly.log 2>&1 &
python scripts/markets/feed_race.py espn --teams "$TEAMS" --hours "$HOURS" --out out/race > out/race/espn.log 2>&1 &
echo "running: out/markets_live/polymarket/nfl/${DATE//-/}.jsonl and out/race/espn.jsonl"
wait
clock end
cat out/race/clock.jsonl
