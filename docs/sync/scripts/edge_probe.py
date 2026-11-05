"""Read-only probe. Per stick: pos_now = position + (uptime_ms - updated) * speed.
E = mac_wall_mid - pos_now/1000 (constant while the stream plays in real time)."""
import subprocess, sys, time, json
sys.path.insert(0, "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone")
from redzone.tuners.adb import parse_playback
HOSTS = ["100.110.165.150:5555", "100.110.165.181:5555"]
def read(host):
    t0 = time.time()
    out = subprocess.run(["adb", "-s", host, "shell", "cat /proc/uptime; dumpsys media_session"],
                         capture_output=True, text=True, timeout=8).stdout
    t1 = time.time()
    up = float(out.split()[0]) * 1000
    b = parse_playback(out)
    if b.get("position") is None: return dict(host=host.split(":")[0][-3:], err="no session")
    sp = b.get("speed", 1.0) if b["state"] == "playing" else 0.0
    pos_now = b["position"] + (up - b["updated"]) * sp
    return dict(host=host.split(":")[0][-3:], pkg=(b["pkg"] or "").split(".")[-2], st=b["state"],
                age_s=round((up - b["updated"]) / 1000, 1), E=round((t0 + t1) / 2 - pos_now / 1000, 2),
                rtt=round(t1 - t0, 2), title=(b.get("title") or "")[:34], sub=b.get("subtitle", ""))
if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    for i in range(n):
        for h in HOSTS:
            try: print(json.dumps(read(h)))
            except Exception as e: print(json.dumps(dict(host=h, err=repr(e))))
        time.sleep(2)
