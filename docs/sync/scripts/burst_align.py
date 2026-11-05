"""Pair YTTV ad-spot bursts (media_session 'updated' every ~10-16 s while playing) with NBA/other timeouts' restart times."""
import json, subprocess, time, datetime as dt, sys
boot = {}
for h in ["100.110.165.150:5555", "100.110.165.181:5555"]:
    t0 = time.time(); up = float(subprocess.run(["adb", "-s", h, "shell", "cat /proc/uptime"], capture_output=True, text=True).stdout.split()[0]); boot[h] = t0 - up
f = lambda t: dt.datetime.fromtimestamp(t).strftime('%H:%M:%S')
days = sys.argv[1:] or ["2026-11-04"]
ev, seen = [], set()
for day in days:
    for l in open(f"data/recordings/{day}/sync_probe.jsonl"):
        r = json.loads(l); ms = r.get("ms") or {}
        if ms.get("pkg") != "com.amazon.firetv.youtube.tv" or not ms.get("updated"): continue
        k = (r["host"], ms["updated"])
        if k in seen: continue
        seen.add(k); ev.append(dict(w=boot[r["host"]] + ms["updated"] / 1000, h=r["host"][-8:-5], pos=ms["position"] / 1000, st=ms.get("state"), title=ms.get("title") or "", sub=ms.get("subtitle")))
ev.sort(key=lambda e: e["w"])
# bursts: consecutive updates on same stick+title, gaps 8-17 s, position advancing ~ the gap
bursts = []
for h in ("150", "181"):
    es = [e for e in ev if e["h"] == h]
    cur = []
    for a, b in zip(es, es[1:]):
        gap = b["w"] - a["w"]; dpos = b["pos"] - a["pos"]
        if a["title"] == b["title"] and 8 <= gap <= 17 and abs(dpos - gap) < 2:
            if not cur: cur = [a]
            cur.append(b)
        else:
            if len(cur) >= 2: bursts.append(cur)
            cur = []
    if len(cur) >= 2: bursts.append(cur)
plays = []
for day in days:
    plays += [json.loads(l) for l in open(f"data/recordings/{day}/plays.jsonl")]
print(f"{len(bursts)} bursts")
for b in bursts:
    title = b[0]["title"]; start, last = b[0]["w"], b[-1]["w"]; spot = (last - start) / (len(b) - 1); end = last + spot
    print(f"\nstick{b[0]['h']} {b[0]['sub']} '{title[:34]}' spots={len(b)} {f(start)}..{f(last)} est_pod_end={f(end)}")
    # candidate games: plays whose text appears? we use league timeouts in the 6 min before the burst end
    tos = [p for p in plays if p["type"] and "imeout" in p["type"] and end - 420 <= p["wall"] <= end]
    for to in sorted({(p["game"], p["wall"]) for p in tos}):
        g, tw = to
        nxt = sorted([p for p in plays if p["game"] == g and p["wall"] > tw + 5 and "ubstitution" not in (p["type"] or "") and "imeout" not in (p["type"] or "")], key=lambda p: p["wall"])
        if nxt:
            print(f"   game {g}: timeout {f(tw)} -> first play {f(nxt[0]['wall'])} ({nxt[0]['type'][:14]})  pod_end - first_play = {end - nxt[0]['wall']:+.1f}s")
