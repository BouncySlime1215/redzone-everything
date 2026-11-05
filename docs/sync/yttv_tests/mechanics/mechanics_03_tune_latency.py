#!/usr/bin/env python3
"""mechanics_03 tune_latency: distribution of YTTV deep-link tune time, three clocks.

Hypothesis: tune time (am start -> PLAYING on the new channel) is 0.9-4.1 s; the stick's own `updated`
stamp (boot + updated/1000) pins the PLAYING moment to ~0.05 s, better than the 0.5 s Mac poll; the first
MediaCodecLogger video line after the tune lags PLAYING by < 5 s (logger cadence) so it is only a coarse check.

Procedure (live, HIDDEN stick only): cycle through --channels for --n tunes (or --minutes): tune_measure()
records t_cmd, play_s_poll, play_s_upd, L; then read-only logcat for the first MediaCodecLogger video line
after t_cmd (codec_s). 4 s rest between tunes.
Dry run: app tune log data/sync.jsonl (kind=tune, youtubetv:*) joined with the first recorded sync_probe
row on that channel in PLAYING with a fresh `updated`; play_s_upd from boot-estimated updated stamps
(resolution limited by the recorder; reported, not judged hard).

Pass rule: n >= 8 and p90(play_s_upd) <= 4.5 s and |play_s_poll - play_s_upd| median <= 0.6 s.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_03_tune_latency"


def judge(out, recs):
    upd = [r["play_s_upd"] for r in recs if r.get("play_s_upd") is not None]
    poll = [r["play_s_poll"] - r["play_s_upd"] for r in recs
            if r.get("play_s_upd") is not None and r.get("play_s_poll") is not None]
    codec = [r["codec_s"] for r in recs if r.get("codec_s") is not None]
    by_ch = {}
    for r in recs:
        if r.get("play_s_upd") is not None:
            by_ch.setdefault(r["ch"], []).append(r["play_s_upd"])
    s_upd, s_gap = M.summary(upd), M.summary(poll)
    out("summary", play_s_upd=s_upd, poll_minus_upd=s_gap, codec_s=M.summary(codec),
        by_channel={k: M.summary(v) for k, v in by_ch.items()})
    ok = None
    if s_upd.get("n", 0) >= 8:
        ok = s_upd["p90"] <= 4.5 and (s_gap.get("n", 0) == 0 or abs(s_gap["p50"]) <= 0.6)
    out.verdict(ok, "n>=8, p90(play_s_upd) <= 4.5 s, median |poll-upd| <= 0.6 s", n=s_upd.get("n", 0))


def dry(args, out):
    allrows = M.load_probe(args.dates)
    rows = M.yt_rows(allrows)
    segs = M.boot_segments(allrows)
    tunes = [r for r in M.load_jsonl(M.REPO / "data" / "sync.jsonl")
             if r.get("kind") == "tune" and str(r.get("target", "")).startswith("youtubetv:")]
    out("inputs", app_tunes=len(tunes), probe_rows=len(rows))
    recs = []
    for tu in tunes:
        ch = tu["target"].split(":", 1)[1]
        t0 = tu["t"]
        prev_upd = {}
        for r in rows:
            if r["t"] < t0 - 30:
                continue
            if r["t"] > t0 + 30:
                break
            h, ms = r["host"], r["ms"]
            if r["t"] < t0:
                prev_upd[h] = ms["updated"]
                continue
            if ms.get("state") == "playing" and M.chan_key(ms.get("subtitle")) == ch and \
                    ms["updated"] != prev_upd.get(h):
                boot = M.boot_at(segs, h, r["t"])
                pu = None if boot is None else round(boot + ms["updated"] / 1000 - t0, 3)
                if pu is not None and -1 < pu < 25:
                    rec = dict(ch=ch, host=h, t_cmd=t0, play_s_poll=round(r["t"] - t0, 2), play_s_upd=pu,
                               L=round(ms["position"] / 1000, 2), src="sync.jsonl+sync_probe")
                    recs.append(rec)
                    out("tune", **rec)
                break
    out("would_measure", plan=f"live: {args.n} tunes over {args.channels} on the hidden stick, "
                             "play_s_poll/play_s_upd/codec_s per tune")
    judge(out, recs)


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    host = hosts[0]
    chans = [c.strip() for c in args.channels.split(",") if c.strip()]
    recs = []
    end = time.time() + args.minutes * 60
    with M.Guard(host, out, args.state_url) as g:
        i = 0
        while time.time() < end and len(recs) < args.n:
            ch = chans[i % len(chans)]
            i += 1
            res = M.tune_measure(g, ch)
            if res.get("ok"):
                time.sleep(2)
                lines = [t for t, s in M.codec_lines_since(host, res["t_cmd"]) if "video" in s]
                res["codec_s"] = round(lines[0] - res["t_cmd"], 2) if lines else None
                recs.append(res)
            out("tune", host=host, **res)
            time.sleep(4)
    judge(out, recs)


def extra(ap):
    ap.add_argument("--channels", default="espn,tnt,espn2,nbatv,tbs")
    ap.add_argument("--n", type=int, default=15)


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=8, extra=extra)
