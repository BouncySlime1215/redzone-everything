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


def time_to_see(replays, switches, my_teams, lag_total, tune_s, t1, replayed) -> list[dict]:
    """Every big play: 0 if watched whole live, else seconds from the end of its airing to the end of its
    catch-up replay, None if never seen. Same big-play and live rules as redzone/lab/sportsgrade.grade."""
    from redzone.grading import PLAY_LEN, is_big
    from redzone.lab.backtest import lag_of, offsets_of
    from redzone.lab.sportsgrade import watched_whole
    watching = [(s.t, f"replay:{s.to}" if s.kind == "replay" else s.to) for s in switches]
    offs = offsets_of(switches)
    out = []
    for r in replays:
        teams = {r.matchup.split(" @ ")[0], r.matchup.split(" @ ")[-1]}
        lag = lag_of(lag_total, r.gid)
        prev = None
        for w, p in zip(r.wall, r.plays):
            if is_big(r.league, p, prev):
                aired = w + lag
                on = watched_whole(watching, r.gid, aired - PLAY_LEN[r.league], aired, tune_s, t1, offs)
                key = (r.gid, round(w))
                at = replayed.get(key) if isinstance(replayed, dict) else None
                tts = 0.0 if on else (max(0.0, at - aired) if at is not None else None)
                out.append({"gid": r.gid, "wall": round(w, 1), "aired": round(aired, 1), "live": bool(on),
                            "tts": None if tts is None else round(tts, 1), "mine": bool(teams & my_teams)})
            prev = p
    return out


def summary(rows: list[dict]) -> dict:
    tts = [x["tts"] for r in rows for x in r["big"]]
    n = len(tts)
    if not n:
        return {"big": 0}
    seen = sorted(t if t is not None else math.inf for t in tts)

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
    a, rest = ap.parse_known_args()
    import eval_sports as E
    from redzone.lab import sportsgrade
    rows: list[dict] = []
    orig = sportsgrade.grade

    def grade(replays, switches, my_teams, lag_total, tune_s, t0, t1, replayed=None):
        card = orig(replays, switches, my_teams, lag_total, tune_s, t0, t1, replayed)
        rows.append({"big": time_to_see(replays, switches, my_teams, lag_total, tune_s, t1, replayed or {}),
                     "switches": [[round(s.t, 1), s.kind, s.frm, s.to, round(getattr(s, "offset", 0.0) or 0.0, 1)]
                                  for s in switches],
                     "t0": t0, "t1": t1, "tune_s": tune_s})
        return card
    sportsgrade.grade = grade
    E.sportsgrade.grade = grade
    argv = [a.league, *rest]
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
    for r, pn in zip(rows, res.get("per_night", [])):
        r.update({"arm": a.arm, "tag": a.tag, "league": a.league, "argv": argv, "night": pn["night"], "card": pn})
    with a.out.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    s = summary(rows)
    s.update({k: res.get(k) for k in ("big_seen_pct", "big_live_pct", "crunch_live_pct", "your_team_crunch_live",
                                      "switches_per_hour", "entries_mid_play", "mid_play_cuts_high")})
    print(json.dumps({"arm": a.arm, "tag": a.tag, "league": a.league, **s}))


if __name__ == "__main__":
    main()
