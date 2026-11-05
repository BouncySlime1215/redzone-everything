#!/usr/bin/env python3
"""fusion_13 web vs stick alignment: each stick's offset to YTTV's ingest clock, from shared events.

ui_12 reads the web player's ingest epoch (getMediaReferenceTime) every second, and logs each program
boundary (title flip) with the ingest epoch it showed then (`web_flip`: t, ref). ui_05 logs each stick's
title flip on the same channel (`flip`: host, channel, t_flip), using the same Mac wall clock. One
boundary is one moment on the ingest clock, so for a stick:

    O_stick = t_flip(stick) - ref(web flip)        seconds the stick's picture runs behind ingest

The Mac clock cancels: both t_flip and the web wall are Mac time, and ref is YTTV's own stamp. If the two
sticks' O differ by ~10 s, that is the per-device 'Broadcast Delay' setting (Default vs Decreased), which
this test only measures; it never changes the setting (that is Nick's call).

Also read: ui_13 overlay frames (latency, E). If the TV app prints a latency, it's compared with O_stick.

Read-only: files only, no adb, no keys.
PASS: some stick has >= 2 matched boundaries whose O lie within 1.0 s of each other.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_13_web_align"
GRID = 1800.0
MATCH_S = 150.0


def boundary(t: float) -> float:
    return round(t / GRID) * GRID


def load(results_dir: str, include_dry: bool):
    web, stick, nerds = [], [], []
    for name, sink, kinds in (("ui_12_web_reference", web, ("web_flip",)),
                              ("ui_05_title_flip_epg", stick, ("flip",)),
                              ("ui_13_stats_for_nerds", nerds, ("frame",))):
        for suffix in ([".jsonl", ".dryrun.jsonl"] if include_dry else [".jsonl"]):
            for r in L.read_jsonl(os.path.join(results_dir, name + suffix)):
                if r.get("kind") in kinds:
                    sink.append(r)
    return web, stick, nerds


def align(web: list[dict], stick: list[dict]) -> list[dict]:
    """One row per stick flip that has a web flip on the same channel at the same boundary."""
    rows = []
    for f in stick:
        ch = L.norm_ch(f.get("channel"))
        tf = f.get("t_flip")
        if tf is None:
            continue
        best = None
        for w in web:
            if L.norm_ch(w.get("channel")) != ch or w.get("ref") is None:
                continue
            if boundary(w["ref"]) != boundary(tf):
                continue
            d = abs(w["t"] - tf)
            if d <= MATCH_S and (best is None or d < best[0]):
                best = (d, w)
        if best:
            w = best[1]
            rows.append(dict(channel=ch, stick=L.stick_name(f.get("host") or f.get("stick")), t=tf,
                             O=round(tf - w["ref"], 2), web_lag=w.get("lag"), boundary=boundary(w["ref"])))
    return rows


def per_stick(rows: list[dict]) -> dict:
    by: dict = {}
    for r in rows:
        by.setdefault((r["channel"], r["stick"]), []).append(r["O"])
    out = {}
    for k, v in by.items():
        med = statistics.median(v)
        core = [x for x in v if abs(x - med) <= 1.0]
        out[k] = dict(n=len(v), O_median=round(med, 2), core_n=len(core),
                      spread=round(max(core) - min(core), 2) if core else None)
    return out


def main():
    ap = L.make_parser(ID, __doc__)
    args = ap.parse_args()
    out = L.Out(args.out, ID, args)
    if args.dry_run:
        # synthetic: web 23.2 s behind ingest; stick hdmi1 31.0 s, hdmi2 41.0 s (the ~10 s setting gap)
        web, stick = [], []
        for b in (1_793_001_600.0, 1_793_003_400.0, 1_793_005_200.0):
            web.append(dict(channel="espn", t=b + 23.2, ref=b, lag=23.2))
            stick.append(dict(channel="espn", host=L.HOSTS["hdmi1"], t_flip=b + 31.0))
            stick.append(dict(channel="espn", host=L.HOSTS["hdmi2"], t_flip=b + 41.0))
        nerds = []
    else:
        web, stick, nerds = load(args.results_dir, include_dry=False)
    rows = align(web, stick)
    for r in rows:
        out.write("anchor_ingest", **r)
    st = per_stick(rows)
    for (ch, sk), s in sorted(st.items()):
        out.write("stick", channel=ch, stick=sk, **s)
        print(f"   {ch:10s} {sk:6s} n={s['n']} O={s['O_median']} core={s['core_n']} spread={s['spread']}")
    sticks = {}
    for (ch, sk), s in st.items():
        sticks.setdefault(ch, {})[sk] = s["O_median"]
    gaps = {ch: L.r2(max(v.values()) - min(v.values())) for ch, v in sticks.items() if len(v) == 2}
    lat = [f["latency"] for f in nerds if f.get("latency") is not None]
    ok = any(s["core_n"] >= 2 and (s["spread"] or 0) <= 1.0 for s in st.values())
    summ = dict(pass_=bool(ok), matched=len(rows), web_flips=len(web), stick_flips=len(stick),
                stick_gap=gaps,
                overlay_latency=dict(n=len(lat), median=round(statistics.median(lat), 2)) if lat else dict(n=0))
    if args.dry_run:
        summ["synthetic_ok"] = bool(st.get(("espn", "hdmi1"), {}).get("O_median") == 31.0
                                    and gaps.get("espn") == 10.0)
        summ["pass_"] = summ["synthetic_ok"]
    out.write("summary", **summ)
    print(f"SUMMARY {ID}: matched={len(rows)} gaps={gaps} pass={summ['pass_']} -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
