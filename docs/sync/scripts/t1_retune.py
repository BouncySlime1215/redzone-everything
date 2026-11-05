"""T1: deep-link retunes on one stick; per tune: seconds until media_session says PLAYING on the new channel,
and the landed origin E (wall - extrapolated position). Logs JSONL to stdout."""
import subprocess, sys, time, json
sys.path.insert(0, "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone")
from redzone.tuners.adb import parse_playback
PKG = "com.amazon.firetv.youtube.tv"
CODES = {"espn": "ferg3lVdMOg", "tnt": "lVQYDh9weHg", "espn2": "qHoTWZ9M9gw", "fs1": "_2ONrjDR7S8", "nbatv": "ykTstVbuNpg"}
def sh(host, cmd, timeout=10):
    return subprocess.run(["adb", "-s", host, "shell", cmd], capture_output=True, text=True, timeout=timeout).stdout
def read(host):
    t0 = time.time(); out = sh(host, "cat /proc/uptime; dumpsys media_session"); t1 = time.time()
    up = float(out.split()[0]) * 1000; b = parse_playback(out, PKG)
    if b.get("position") is None: return None
    sp = b.get("speed", 1.0) if b["state"] == "playing" else 0.0
    return dict(st=b["state"], sub=b.get("subtitle", ""), title=b.get("title", ""), upd=b["updated"],
                E=(t0 + t1) / 2 - (b["position"] + (up - b["updated"]) * sp) / 1000)
def tune(host, ch):
    before = read(host)
    t0 = time.time()
    sh(host, f"am start -a android.intent.action.VIEW -d https://tv.youtube.com/watch/{CODES[ch]} {PKG}")
    seen = None
    while time.time() - t0 < 25:
        r = read(host)
        if r and r["st"] == "playing" and r["sub"].lower().replace(" ", "") == ch and (before is None or r["upd"] != before["upd"]):
            seen = r; break
        time.sleep(0.25)
    dt = time.time() - t0
    time.sleep(3); after = read(host)
    return dict(ch=ch, play_s=round(dt, 2) if seen else None, E_land=round(seen["E"], 2) if seen else None,
                E_3s=round(after["E"], 2) if after else None, title=(after or {}).get("title", "")[:30])
if __name__ == "__main__":
    host = sys.argv[1]; seq = sys.argv[2].split(",")
    for ch in seq:
        res = tune(host, ch); res["t"] = round(time.time(), 1); print(json.dumps(res), flush=True)
        time.sleep(4)
