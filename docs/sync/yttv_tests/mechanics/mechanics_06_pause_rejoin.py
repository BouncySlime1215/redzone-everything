#!/usr/bin/env python3
"""mechanics_06 pause_rejoin: how a pause of d seconds moves E, and when YTTV snaps back to live.

Hypothesis: pause+play adds exactly d to the delay (dE = d +- 0.5 s) for short pauses; past some threshold
(~10 s seen once) YTTV sometimes rejoins live on resume (dE ~ 0) or auto-resumes by itself. A known,
exact pause is the cheapest way to add a calibrated delay to the hidden stick, so we need the rule.

Procedure (live, HIDDEN stick only): for each d in --pauses (shuffled once, each --reps times):
  tune --channel (fresh live edge), settle, r0; dispatch pause; poll every 1 s for d seconds logging state
  (auto-resume while paused?); dispatch play; wait_settle -> r1; dE = r1.E - r0.E; residual = dE - d_actual
  (d_actual = Mac time between the two dispatches); rejoined = |dE| < d/2. Also: 'pause_keycode' variant
  (KEYCODE_MEDIA_PLAY_PAUSE 85) on the first d to compare with media_session dispatch.
Dry run: recorded PAUSED spans per stick (sync_probe), E before vs after on the same channel; plus
overnight.jsonl exp B (pause_s, behind_s).

Pass rule: for non-rejoin trials |residual| <= 0.5 s (p90), and the rejoin threshold is reported as the
smallest d with a rejoin (None if never). Pass = residual rule holds on >= 5 trials.
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_06_pause_rejoin"


def judge(out, trials):
    nr = [t for t in trials if not t.get("rejoined") and t.get("residual") is not None]
    rj = sorted(t["d"] for t in trials if t.get("rejoined"))
    res = M.summary([abs(t["residual"]) for t in nr])
    out("summary", non_rejoin=len(nr), rejoin_ds=rj, abs_residual=res,
        rejoin_threshold=rj[0] if rj else None)
    ok = None if len(nr) < 5 else res["p90"] <= 0.5
    out.verdict(ok, "p90 |dE - d| <= 0.5 s on >= 5 non-rejoin trials; report rejoin threshold")


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    trials = []
    for h in M.HOSTS.values():
        before, pause_t0, pause_ch = None, None, None
        for r in M.yt_rows(rows, h):
            ms = r["ms"]
            ch = M.chan_key(ms.get("subtitle"))
            if ms.get("state") == "playing":
                E = M.rec_E(r, segs)
                if pause_t0 is not None and before is not None and ch == pause_ch and E is not None:
                    d_upper = r["t"] - pause_t0  # pause length is only bounded by the probe cadence
                    dE = E - before
                    t = dict(host=h, ch=ch, t=r["t"], d=round(d_upper, 1), dE=round(dE, 2),
                             residual=None, rejoined=abs(dE) < 0.5 * d_upper, src="sync_probe")
                    trials.append(t)
                    out("recorded_pause", **t)
                pause_t0 = None
                before, pause_ch = E, ch
            elif ms.get("state") == "paused" and pause_t0 is None:
                pause_t0 = r["t"]
    for r in M.load_jsonl(M.SYNC / "overnight.jsonl"):
        if r.get("exp") == "B" and r.get("behind_s") is not None:
            t = dict(host="100.110.165.181:5555", ch=r["ch"], d=r["pause_s"], dE=r["behind_s"],
                     residual=round(r["behind_s"] - r["pause_s"], 2), rejoined=r["behind_s"] < r["pause_s"] / 2,
                     src="overnight B (includes key latency)")
            trials.append(t)
            out("recorded_pause", **t)
    out("would_measure", plan=f"live: pauses {args.pauses} x{args.reps} on the hidden stick")
    judge(out, trials)


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    host = hosts[0]
    ds = [float(x) for x in args.pauses.split(",")] * args.reps
    random.shuffle(ds)
    trials = []
    end = time.time() + args.minutes * 60
    with M.Guard(host, out, args.state_url) as g:
        for i, d in enumerate(ds):
            if time.time() + d + 30 > end:
                break
            out("tune_live", **M.tune_measure(g, args.channel))
            r0, _ = M.wait_settle(host, hold_s=2.0)
            if r0 is None or r0["st"] != "playing":
                continue
            ta = time.time()
            g.key(85) if (i == 0 and args.keycode_first) else g.dispatch("pause")
            states = []
            while time.time() - ta < d:
                time.sleep(1.0)
                r = M.read(host)
                states.append(r and r["st"])
            tb = time.time()
            g.dispatch("play")
            r1, settle = M.wait_settle(host, hold_s=2.0)
            if r1 is None:
                continue
            dE = r1["E"] - r0["E"]
            t = dict(host=host, d=d, d_actual=round(tb - ta, 2), dE=round(dE, 3),
                     residual=round(dE - (tb - ta), 3), rejoined=abs(dE) < (tb - ta) / 2,
                     auto_resumed="playing" in states, settle_s=settle, st=r1["st"])
            trials.append(t)
            out("trial", **t)
        out("relink", **M.tune_measure(g, args.channel))
    judge(out, trials)


def extra(ap):
    ap.add_argument("--pauses", default="2,5,8,10,12,15,20,30,45,60")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--keycode-first", action="store_true", help="first trial pauses via KEYCODE 85")


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=15, extra=extra)
