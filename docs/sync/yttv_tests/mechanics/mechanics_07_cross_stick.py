#!/usr/bin/env python3
"""mechanics_07 cross_stick: content offset between the two sticks on the same YTTV channel.

Hypothesis: E differs per tune (L is fixed while the DVR window slides), so E_150 - E_181 is NOT the picture
offset. The picture offset is measured from shared content events: DAI ad-spot boundaries fire a
media_session update on BOTH sticks at the same content instant. Matching those update epochs
(boot + updated/1000) across sticks gives offset = t_upd(150) - t_upd(181) to ~0.1 s; it should be stable
(sd <= 0.3 s) and equal to (L_150 - L_181) + (t_tune_150 - t_tune_181) adjusted ... i.e. we test whether
the landing-L difference (~10 s ESPN) is the real picture difference.

Procedure (live): read-only on the visible stick. If the visible stick is on a YTTV channel we have a code
for, tune the HIDDEN stick to that channel (guarded) - otherwise tune hidden to --channel and only log.
Then for --minutes poll both sticks every 1 s (read-only): log E, state, `updated` epochs. Fresh updates
(updated changed) on each stick form an event train; best constant offset in [-60, 60] s maximising
matches within 0.4 s; refine by median of matched diffs. Also log both sticks' MediaCodecLogger bitrate
lines at the end (read-only) for an independent xcorr later.
Dry run: recorded windows where both sticks show the same channel PLAYING; same event matching on
boot-estimated update epochs; also overnight.jsonl exp C/D (E of both sticks after a twin tune).

Pass rule: >= 3 matched update events covering >= 50% of the smaller event train, sd(matched diffs) <= 0.3 s; report offset, dL, dE.
"""
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_07_cross_stick"


def match(a: list[float], b: list[float], tol=0.4, rng=60.0):
    """best offset o s.t. a_i ~ b_j + o. Candidate offsets = all pairwise diffs within rng."""
    best = (0, None, [])
    cands = [x - y for x in a for y in b if abs(x - y) <= rng]
    for o in cands:
        diffs = []
        for x in a:
            near = min(b, key=lambda y: abs(x - y - o)) if b else None
            if near is not None and abs(x - near - o) <= tol:
                diffs.append(x - near)
        if len(diffs) > best[0]:
            best = (len(diffs), o, diffs)
    n, o, diffs = best
    frac = round(n / min(len(a), len(b)), 2) if a and b else 0.0
    return dict(n=n, match_frac=frac, offset=round(st.median(diffs), 3) if diffs else None,
                sd=round(st.pstdev(diffs), 3) if len(diffs) > 1 else None)


def judge(out, res_list):
    # a real shared-content train matches most events; a few chance matches (frac < 0.5) mean "no signal"
    good = [r for r in res_list if r.get("n", 0) >= 3 and r.get("sd") is not None and r.get("match_frac", 0) >= 0.5]
    ok = None if not good else all(r["sd"] <= 0.3 for r in good)
    out.verdict(ok, ">=3 matched update events (>=50% of the smaller train) per window, sd <= 0.3 s", windows=len(good),
                offsets=[r["offset"] for r in good])


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    h1, h2 = M.HOSTS["hdmi1"], M.HOSTS["hdmi2"]
    # windows: consecutive time where both sticks report the same channel playing
    by_t = {}
    for r in M.yt_rows(rows):
        by_t.setdefault(round(r["t"], 0), {})[r["host"]] = r
    wins, cur = [], None
    for t in sorted(by_t):
        d = by_t[t]
        if h1 in d and h2 in d and d[h1]["ms"].get("state") == "playing" == d[h2]["ms"].get("state") \
                and M.chan_key(d[h1]["ms"].get("subtitle")) == M.chan_key(d[h2]["ms"].get("subtitle")) != "":
            ch = M.chan_key(d[h1]["ms"].get("subtitle"))
            if cur and cur["ch"] == ch and t - cur["t1"] < 60:
                cur["t1"] = t
                cur["rows"].append(d)
            else:
                cur = dict(ch=ch, t0=t, t1=t, rows=[d])
                wins.append(cur)
    res_list = []
    for w in wins:
        if w["t1"] - w["t0"] < 120:
            continue
        ev = {h1: [], h2: []}
        last = {}
        for d in w["rows"]:
            for h in (h1, h2):
                u = d[h]["ms"]["updated"]
                if last.get(h) is not None and u != last[h]:
                    b = M.boot_at(segs, h, d[h]["t"])
                    if b is not None:
                        ev[h].append(b + u / 1000)
                last[h] = u
        E1 = [M.rec_E(d[h1], segs) for d in w["rows"]]
        E2 = [M.rec_E(d[h2], segs) for d in w["rows"]]
        dE = M.summary([a - b for a, b in zip(E1, E2) if a is not None and b is not None])
        m = match(ev[h1][:200], ev[h2][:200])
        L1 = w["rows"][-1][h1]["ms"]["position"] / 1000
        L2 = w["rows"][-1][h2]["ms"]["position"] / 1000
        rec = dict(ch=w["ch"], t0=w["t0"], minutes=round((w["t1"] - w["t0"]) / 60, 1), events=(len(ev[h1]), len(ev[h2])),
                   dE_150_minus_181=dE, **m)
        res_list.append(rec)
        out("window", **rec)
    for r in M.load_jsonl(M.SYNC / "overnight.jsonl"):
        if r.get("exp") in ("C", "D"):
            out("overnight_twin", exp=r["exp"], data={k: r[k] for k in r if k not in ("t",)})
    out("would_measure", plan="live: tune hidden to the visible stick's YTTV channel, poll both 1 Hz, "
                              "match DAI update epochs across sticks")
    judge(out, res_list)


def live(args, out):
    hosts, ss = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    hid = hosts[0]
    vis = next(iter(ss["visible"]))
    v = M.read(vis)
    ch = M.chan_key(v and v["sub"])
    if not (v and v["st"] == "playing" and ch in M.T.CODES):
        out("note", why=f"visible stick not on a known YTTV channel ({ch!r}); using --channel, offsets may be empty")
        ch = args.channel
    with M.Guard(hid, out, args.state_url) as g:
        out("twin_tune", **M.tune_measure(g, ch))
    clk = {h: M.stick_clock(h) for h in (vis, hid)}
    out("clocks", clocks=clk)
    ev = {vis: [], hid: []}
    last, Es = {}, {vis: [], hid: []}
    end = time.time() + args.minutes * 60
    while time.time() < end:
        for h in (vis, hid):
            r = M.read(h)
            if not r:
                continue
            Es[h].append(r["E"])
            if last.get(h) is not None and r["upd"] != last[h] and clk[h].get("boot_epoch"):
                ev[h].append(clk[h]["boot_epoch"] + r["upd"] / 1000)
                out("update", host=h, upd_epoch=round(ev[h][-1], 3), st=r["st"], E=round(r["E"], 3), sub=r["sub"])
            last[h] = r["upd"]
        time.sleep(1.0)
    m = match(ev[vis], ev[hid])
    codec = {h: M.codec_lines_since(h, end - args.minutes * 60)[-200:] for h in (vis, hid)}
    rec = dict(ch=ch, vis=vis, hid=hid, dE_vis_minus_hid=M.summary(
        [a - b for a, b in zip(Es[vis], Es[hid])]), **m)
    out("window", **rec)
    out("codec", lines={h: [(round(t, 3), s[-60:]) for t, s in v] for h, v in codec.items()})
    judge(out, [rec])


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=20)
