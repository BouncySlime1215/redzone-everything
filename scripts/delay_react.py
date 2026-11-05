"""[EXP-209] Delay-and-react arms A-D, offline (Nick, Nov 5 12:25 AM). Runs scripts/eval_sports.py for one arm and
writes one raw row per night (big plays with their time-to-see, switches, holds) for lane 3's grader (EXP-301,
redzone/lab/reactgrade.py, frozen by the integrator). This script never grades; its printout is a quick look only.

    python scripts/delay_react.py --arm B --league nba --split valid --seed 0 --out rows.jsonl [eval_sports args]

Arms: A = shipped; B = decider.react.mode=react; C = hybrid; D = hybrid + react.chase + lookahead (+ --behind-live B).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

ARMS = {
    "A": [],
    "B": ["decider.react.mode=react"],
    "C": ["decider.react.mode=hybrid"],
    "D": ["decider.react.mode=hybrid", "decider.react.chase=true", "noggin.lookahead.enabled=true"],
}


def big_see(replays, switches, lag_total, tune_s, t1, replayed) -> list[dict]:
    """Cross-check only (the grade is EXP-301's): each big play's grader-v14 `big_see_s` (0 live, -1 never),
    with the same rules as redzone/lab/sportsgrade.grade and backtest.time_to_see."""
    from redzone.grading import PLAY_LEN, is_big
    from redzone.lab.backtest import lag_of, offsets_of, time_to_see
    from redzone.lab.sportsgrade import watched_whole
    watching = [(s.t, f"replay:{s.to}" if s.kind == "replay" else s.to) for s in switches]
    offs = offsets_of(switches)
    out = []
    for r in replays:
        lag = lag_of(lag_total, r.gid)
        prev = None
        for w, p in zip(r.wall, r.plays):
            if is_big(r.league, p, prev):
                aired = w + lag
                on = watched_whole(watching, r.gid, aired - PLAY_LEN[r.league], aired, tune_s, t1, offs)
                x = time_to_see(on, replayed, (r.gid, round(w)), aired)
                out.append({"gid": r.gid, "wall": round(w, 1), "see_s": -1 if x is not None and math.isinf(x)
                            else (None if x is None else round(x, 1))})
            prev = p
    return out


def summary(rows: list[dict]) -> dict:
    tts = [x["see_s"] for r in rows for x in r["big"]]
    n = len(tts)
    if not n:
        return {"big": 0}
    seen = sorted(math.inf if t is None or t < 0 else t for t in tts)

    def q(f):
        v = seen[min(n - 1, int(f * n))]
        return None if math.isinf(v) else v
    share = lambda s: round(100 * sum(t <= s for t in seen) / n, 1)   # noqa: E731
    return {"big": n, "tts_p50": q(.5), "tts_p90": q(.9), "live": share(0), "le30": share(30), "le90": share(90),
            "le180": share(180), "mid_play": sum(r["card"]["entries_mid_play"] for r in rows),
            "dead_air": round(sum(r["card"]["dead_s"] for r in rows) / max(1, sum(r["card"]["watched_s"] for r in rows)) * 100, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=sorted(ARMS))
    ap.add_argument("--league", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tag", default="", help="free-form label stored on each row (e.g. B=30 feed=15)")
    ap.add_argument("--raw", type=Path, default=None, help="also write the raw cross-check rows here")
    ap.add_argument("--behind-live", type=float, default=None, help="arm D's hidden-stick buffer, seconds")
    ap.add_argument("--feed-lag", type=float, default=None, help="fixed feed lag (Nick's 5-30 s assumption)")
    ap.add_argument("--cond", default="", help="condition label (default: real-lag / feed-X / resync)")
    ap.add_argument("--split-name", default="fit")
    ap.add_argument("--seed-no", type=int, default=0)
    a, rest = ap.parse_known_args()
    import eval_sports as E
    from redzone.lab import backtest, evaldata, reactgrade, sportsgrade
    cond = a.cond or ("feed-%g" % a.feed_lag if a.feed_lag is not None else ("resync" if "--resync" in rest else "real-lag"))
    prof = json.loads((ROOT / "redzone/lab/feed_lag_profile.json").read_text())["leagues"].get(a.league, {})
    # the honesty check's feed lag: the measured 5th percentile (a replay before it knew too soon), else the fixed one
    feed_lag_s = float(a.feed_lag) if a.feed_lag is not None else float((prof.get("q") or [0, 0])[1])
    rows: list[dict] = []
    grade_rows: list[dict] = []
    st: dict = {"windows": set(), "targets": {}, "buffer": 0.0}
    orig_credit = backtest._credit_completed_replays

    def credit(windows, watching, now, replayed):
        st["windows"].update(windows)
        return orig_credit(windows, watching, now, replayed)
    backtest._credit_completed_replays = credit
    orig_run, orig_knobs, orig_grade = E.run, E.knobs, sportsgrade.grade

    def knobs(ns):
        b = getattr(ns, "behind_live", None)
        st["buffer"] = float(b or 0.0)
        return orig_knobs(ns)

    def run(reps, cfg, mine, targets=None, **kw):
        st["windows"], st["targets"], st["cfg"] = set(), dict(targets or {}), cfg
        rep = orig_run(reps, cfg, mine, targets=targets, **kw)
        st["rep"] = rep
        return rep

    def grade(replays, switches, my_teams, lag_total, tune_s, t0, t1, replayed=None):
        card = orig_grade(replays, switches, my_teams, lag_total, tune_s, t0, t1, replayed)
        if st.get("inside"):   # oracle.grade calls sportsgrade.grade again: pass straight through
            return card
        st["inside"] = True   # oracle_big_live and night_row grade again; those calls pass straight through
        try:
            _row(replays, switches, my_teams, lag_total, tune_s, t0, t1, replayed)
        finally:
            st["inside"] = False
        return card

    def _row(replays, switches, my_teams, lag_total, tune_s, t0, t1, replayed):
        ob = reactgrade.oracle_big_live(replays, my_teams, lag_total, tune_s, t0, t1)
        holds = [(s.t, "visible", s.to) for s in switches]   # the sim models the visible stick only
        dvr = [g for g, tg in st["targets"].items() if str(tg).startswith("youtubetv")]
        night = time.strftime("%Y%m%d", time.gmtime(t0 - 6 * 3600 - 4 * 3600))
        grade_rows.append(reactgrade.night_row(arm_name, night, a.split_name, replays, switches, my_teams, lag_total,
                                               tune_s, t0, t1, sorted(st["windows"]), feed_lag_s, holds, dvr,
                                               oracle_big=ob, buffer_s=st["buffer"], cond=cond))
        rows.append({"big": big_see(replays, switches, lag_total, tune_s, t1, replayed or {}),
                     "switches": [[round(s.t, 1), s.kind, s.frm, s.to, round(getattr(s, "offset", 0.0) or 0.0, 1)]
                                  for s in switches], "t0": t0, "t1": t1, "tune_s": tune_s})
    E.run, E.knobs, E.sportsgrade.grade, sportsgrade.grade = run, knobs, grade, grade
    arm_name = a.arm if a.arm != "D" else f"D@{a.behind_live:g}" if a.behind_live is not None else "D"
    argv = [a.league, *rest]
    if a.behind_live is not None:
        argv += ["--behind-live", str(a.behind_live)]
    if a.feed_lag is not None:
        argv += ["--feed-lag", str(a.feed_lag)]
    for kv in ARMS[a.arm]:
        argv += ["--set", kv]
    sys.argv = ["eval_sports.py", *argv]
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        E.main()
    text = buf.getvalue().splitlines()
    i = max(k for k, line in enumerate(text) if line.rstrip() == "{")
    res = json.loads("\n".join(text[i:]))
    for r, g, pn in zip(rows, grade_rows, res.get("per_night", [])):
        r.update({"arm": arm_name, "tag": a.tag, "league": a.league, "argv": argv, "night": pn["night"], "card": pn})
        g["night"] = pn["night"]
        g["seed"] = a.seed_no
    with a.out.open("a") as f:
        for g in grade_rows:
            f.write(json.dumps(g) + "\n")
    if a.raw is not None:
        with a.raw.open("a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    s = summary(rows)
    s.update({k: res.get(k) for k in ("big_seen_pct", "big_live_pct", "crunch_live_pct", "your_team_crunch_live",
                                      "switches_per_hour", "entries_mid_play", "mid_play_cuts_high")})
    print(json.dumps({"arm": arm_name, "tag": a.tag, "league": a.league, **s}))


if __name__ == "__main__":
    main()
