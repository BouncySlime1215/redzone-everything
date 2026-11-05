#!/usr/bin/env python3
"""mechanics_04 seek_keys: step size and settle time of each seek input YTTV accepts.

Hypothesis: media_session advertises REWIND / FAST_FORWARD / SEEK_TO (actions=383). At least one input
(media_session dispatch rewind / fast-forword, KEYCODE_MEDIA_REWIND 89 / FAST_FORWARD 90, DPAD_LEFT 21 /
RIGHT 22 while the scrubber is up) moves the content position by a FIXED step (sd <= 0.3 s) and settles
(media_session `updated` stable, PLAYING) within 3 s, so we can dial an exact extra delay on the hidden stick.

Procedure (live, HIDDEN stick only, guard re-checked per press): for each variant in --variants:
  tune --channel (fresh live edge) -> settle; then --presses times: r0 = settled reading; press once;
  wait_settle -> r1; step = (r1.pos_now - r0.pos_now) - (r1.t - r0.t)  [content seconds jumped beyond real
  time; negative = back]; dE = r1.E - r0.E; settle_s; state. Backward variants run before forward ones (FF at
  the live edge is expected to be a no-op, and that is logged too). Re-tune to live after each variant.
Dry run: recorded E discontinuities on the same channel while PLAYING that are NOT channel changes (seek-like
jumps from ad pods, pauses or rejoin), and print the per-variant plan.

Pass rule: some variant gives n >= 3 non-zero steps with sd(step) <= 0.3 s and median settle <= 3 s
(report each variant's step/sd/settle; FF-at-live = no-op is expected, not a failure).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_04_seek_keys"
VARIANTS = {"d_rew": ("dispatch", "rewind"), "k_rew": ("key", 89), "k_left": ("key", 21),
            "d_ff": ("dispatch", "fast-forword"), "k_ff": ("key", 90), "k_right": ("key", 22)}


def judge(out, res: dict):
    good = []
    for v, xs in res.items():
        steps = [x["step"] for x in xs if x.get("step") is not None and abs(x["step"]) >= 0.5]
        settle = [x["settle_s"] for x in xs if x.get("settle_s") is not None]
        s, ss = M.summary(steps), M.summary(settle)
        out("variant", variant=v, step=s, settle_s=ss, n_presses=len(xs), noop=len(xs) - len(steps))
        if s.get("n", 0) >= 3 and s["sd"] <= 0.3 and ss.get("n", 0) and ss["p50"] <= 3:
            good.append(v)
    out.verdict(bool(good) if res else None, "a variant with n>=3 steps, sd <= 0.3 s, settle p50 <= 3 s",
                usable=good)


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    jumps = []
    for h in M.HOSTS.values():
        prev = None
        for r in M.yt_rows(rows, h):
            ms = r["ms"]
            if ms.get("state") != "playing":
                continue
            E = M.rec_E(r, segs)
            ch = M.chan_key(ms.get("subtitle"))
            if prev and prev[0] == ch and E is not None and prev[1] is not None and abs(E - prev[1]) > 0.5 \
                    and r["t"] - prev[2] < 30:
                jumps.append(dict(host=h, ch=ch, t=r["t"], dE=round(E - prev[1], 2), gap_s=round(r["t"] - prev[2], 1)))
            prev = (ch, E, r["t"])
    for j in jumps[:40]:
        out("recorded_jump", **j)
    out("recorded_jumps", n=len(jumps), dE=M.summary([j["dE"] for j in jumps]),
        note="these are pauses/stalls/rejoins, not key presses: no key-press data exists yet")
    for v, (kind, code) in VARIANTS.items():
        if v in args.variants.split(","):
            out("would_measure", variant=v, input=f"{kind} {code}", presses=args.presses,
                measure="step = d(pos_now) - d(wall), settle_s, state; re-tune to live after")
    judge(out, {})


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    host = hosts[0]
    res = {}
    end = time.time() + args.minutes * 60
    with M.Guard(host, out, args.state_url) as g:
        r = M.read(host)
        out("actions", host=host, mask=r and r["actions"], decoded=M.decode_actions(r and r["actions"]))
        for v in [x for x in args.variants.split(",") if x in VARIANTS]:
            if time.time() > end:
                break
            kind, code = VARIANTS[v]
            out("tune_live", **M.tune_measure(g, args.channel))
            r0, _ = M.wait_settle(host, hold_s=2.0)
            for i in range(args.presses):
                if r0 is None:
                    break
                g.dispatch(code) if kind == "dispatch" else g.key(code)
                r1, settle = M.wait_settle(host, hold_s=2.0, timeout=15)
                if r1 is None:
                    break
                rec = dict(variant=v, i=i, step=round((r1["pos_now"] - r0["pos_now"]) - (r1["t"] - r0["t"]), 3),
                           dE=round(r1["E"] - r0["E"], 3), settle_s=settle, st=r1["st"], pos=round(r1["pos"], 3))
                out("press", **rec)
                res.setdefault(v, []).append(rec)
                if r1["st"] != "playing":  # some inputs pause the stream: resume so the next step is clean
                    g.dispatch("play")
                    r1, _ = M.wait_settle(host, hold_s=2.0)
                r0 = r1
                time.sleep(1)
        out("relink", **M.tune_measure(g, args.channel))
    judge(out, res)


def extra(ap):
    ap.add_argument("--variants", default="d_rew,k_rew,k_left,d_ff,k_ff,k_right")
    ap.add_argument("--presses", type=int, default=4)


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=15, extra=extra)
