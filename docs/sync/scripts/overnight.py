"""Overnight sync harness (hidden stick .181 only; read-only on .150). Light: one action per ~60-90 s.
Experiments, round-robin, logged to overnight.jsonl:
  A land   : deep-link tune to a channel -> tune seconds, landing L (pos_now - secs since play) per channel
  B pause  : pause N s (5..60) then play -> behind-live after resume (does YTTV auto-rejoin?)
  C twin   : tune .181 to the channel .150 is on -> both sticks' L, for the bitrate cross-correlation
  D hold   : just watch E on both sticks for drift/stalls over 2 min
"""
import json, random, subprocess, sys, time
sys.path.insert(0, "/private/tmp/claude-501/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/db4845a1-e6cf-4448-8874-21dc8f52883f/scratchpad/sync")
from t1_retune import read, sh, CODES, PKG
CODES.update({"tbs": "vv_r1X6N3sc", "usa": "ZgiS2N0QkbU", "fox": "njJRoIW1Lps", "espnu": "6x-T96Q-5eY",
              "cbssn": "XG1jwCkVgUE", "nflnetwork": "_pYg9qMKKIA", "trutv": "x2IdxPvLNC0", "btn": "1D2LKf_AW5w"})
HID, VIS = "100.110.165.181:5555", "100.110.165.150:5555"
OUT = open(sys.argv[1], "a"); END = time.time() + float(sys.argv[2]) * 3600
def log(**k): k["t"] = round(time.time(), 2); OUT.write(json.dumps(k) + "\n"); OUT.flush()
def tune(ch):
    b = read(HID); t0 = time.time()
    sh(HID, f"am start -a android.intent.action.VIEW -d https://tv.youtube.com/watch/{CODES[ch]} {PKG}")
    while time.time() - t0 < 25:
        r = read(HID)
        if r and r["st"] == "playing" and r["sub"].lower().replace(" ", "") in (ch, ch.replace("network", "")) and (b is None or r["upd"] != b["upd"]):
            tp = time.time(); time.sleep(3); a = read(HID)
            return dict(play_s=round(tp - t0, 2), L=round(time.time() - a["E"] - (time.time() - tp), 2) if a else None,
                        L_raw=round(time.time() - a["E"], 2) if a else None, title=(a or {}).get("title", "")[:30], E=a and round(a["E"], 2))
        time.sleep(0.25)
    return dict(play_s=None, err="no confirm", sub=(read(HID) or {}).get("sub"))
i = 0
while time.time() < END:
    try:
        exp = ["A", "A", "B", "C", "D"][i % 5]; i += 1
        if exp == "A":
            ch = random.choice(list(CODES)); log(exp="A", ch=ch, **tune(ch))
        elif exp == "B":
            ch = "espn"; tune(ch); time.sleep(2); e0 = read(HID)["E"]; n = random.choice([5, 8, 12, 15, 20, 30, 45, 60])
            sh(HID, "input keyevent KEYCODE_MEDIA_PAUSE"); time.sleep(n); sh(HID, "input keyevent KEYCODE_MEDIA_PLAY"); time.sleep(5)
            r = read(HID); log(exp="B", ch=ch, pause_s=n, behind_s=round(r["E"] - e0, 2) if r else None, st=r and r["st"])
        elif exp == "C":
            v = read(VIS); ch = (v or {}).get("sub", "").lower().replace(" ", "")
            if ch in CODES:
                res = tune(ch); v2 = read(VIS)
                log(exp="C", ch=ch, hid=res, vis_E=v2 and round(v2["E"], 2), vis_title=v2 and v2["title"][:30])
            else: log(exp="C", skip=f"visible on {ch!r}")
        else:
            s = []
            for _ in range(8):
                a, b = read(HID), read(VIS); s.append([a and round(a["E"], 2), b and round(b["E"], 2)]); time.sleep(15)
            log(exp="D", samples=s)
    except Exception as e:
        log(exp="err", err=repr(e)[:200])
    time.sleep(random.uniform(30, 60))
