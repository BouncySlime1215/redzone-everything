"""[highlight jump] Learn the jump/stay policy offline, from what actually happened on archive nights.

Two things are fit, on the fit nights only (train + valid of redzone/lab/splits.py; the LOCKED test
nights are never read), with forward-chaining folds by night (fit on older, check on the next block):

1. How long each kind of break really runs on screen, at its cautious end (p10): break event ->
   the restart (cutgrade.resume_after), per league and stoppage kind. Until the break model has
   quantiles of its own, this is the jump window's `dead_s` (the decider never lets it shrink the
   floor-based estimate it already had).

2. The margin on top of reel + both switches. Every moment the decider considered a reel is
   recorded with the reel it could have played (observe mode: nothing is acted on, so the archive
   night runs exactly as it does today) and scored by what really happened next in that game:
     safe       the dead ball really lasted out + reel + back  (no live play missed at home)
     replaced   seconds of that dead ball the reel would have filled (commercial seconds included)
   The fitted margin is the smallest one with no unsafe jump on the fitting fold.

    python -m redzone.lab.reeljump nfl|nba|nhl [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from redzone.decider import decider as D
from redzone.lab import cutgrade
from redzone.models import REAL_BREAKS, League, Stoppage
from redzone.reel import JumpWindow, fit_reel
from redzone.sources import espn

ROOT = Path(__file__).resolve().parent.parent.parent
MARGINS = (0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0)


# --- 1. break lengths on screen ------------------------------------------------------------------

def break_lengths(replays) -> dict[str, list[float]]:
    """Seconds from each break's first event to the restart, by stoppage kind."""
    out: dict[str, list[float]] = {}
    for r in replays:
        prev = Stoppage.NONE
        for i, p in enumerate(r.plays):
            st = espn.stoppage_for(r.league, p)
            if st in REAL_BREAKS and st != prev:
                res = cutgrade.resume_after(r.league, r.wall, r.plays, i)
                if math.isfinite(res) and res > r.wall[i]:
                    out.setdefault(st.value, []).append(res - r.wall[i])
            prev = st
    return out


def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def p10_table(replays, q: float = 0.10, min_n: int = 20) -> dict[str, float]:
    """{stoppage kind: p10 seconds}, only kinds seen at least min_n times (else no claim)."""
    return {k: round(quantile(v, q), 1) for k, v in sorted(break_lengths(replays).items()) if len(v) >= min_n}


# --- 2. candidate moments, scored by what happened ----------------------------------------------

@dataclass
class Moment:
    t: float
    game: str
    league: str
    kind: str              # the stoppage on screen
    dead_s: float          # the decider's cautious remaining length
    out_s: float
    back_s: float
    clips: tuple           # the offer: (seconds, value) per clip
    min_clips: int
    min_s: float
    blocked: str           # a hard stay (crunch, your team late, must-see, big moment soon), or ""
    true_left: float = 0.0  # seconds the dead ball really had left on screen
    true_brk: bool = False  # ...inside a commercial break


@dataclass
class Fold:
    moments: list[Moment] = field(default_factory=list)


def observe(nights, cfg, mine: set[str], targets_of=lambda reps: None) -> list[Moment]:
    """Run each night with reels simulated but never played, recording every moment a reel was
    considered and what the decider would have needed. Then score each against the archive."""
    from redzone.lab.backtest import run
    rec: list[Moment] = []
    orig = D.jump_value
    state = {}

    def spy(world, offer, window, policy):
        j = orig(world, offer, window, policy)
        dec, home = state["dec"], state["home"]
        tl = dec.timelines.get(home)
        st = tl.last("stop_start") if tl else None
        blocked = "" if j.ok or "fit" in j.reason else j.reason
        if offer is not None and offer.clips:
            rec.append(Moment(dec._clock(), home, tl.league.base.value if tl else "", st.label if st else "",
                              window.dead_s, window.out_s, window.back_s, offer.clips, offer.min_clips,
                              offer.min_s, blocked))
        return type(j)(j.ev_reel, j.ev_stay, j.cost_s, False, "observing", 0, 0.0)

    orig_rj = D.Decider._reel_jump

    def rj(self, home, watchable, best_visit=-1.0):
        state["dec"], state["home"] = self, home
        return orig_rj(self, home, watchable, best_visit)

    D.jump_value, D.Decider._reel_jump = spy, rj
    try:
        for reps, targets in nights:
            start = len(rec)
            rep = run(reps, cfg, mine, targets=targets, reels=True)
            by = {r.gid: r for r in reps}
            for m in rec[start:]:
                r = by.get(m.game)
                if r is None:
                    continue
                dead, brk, wait = cutgrade._dead_at(r.league, r.wall, r.plays, m.t - rep.lag_true)
                m.true_left, m.true_brk = (wait if dead else 0.0), brk
    finally:
        D.jump_value, D.Decider._reel_jump = orig, orig_rj
    return rec


def score(moments: list[Moment], margin: float) -> dict:
    """What a margin would have done on these moments: jumps, unsafe ones, seconds of dead ball filled."""
    jumps = unsafe = 0
    filled = commercial = 0.0
    seen: set[tuple[str, int]] = set()
    for m in moments:
        if m.blocked:
            continue
        key = (m.game, int(m.t // 120))   # one jump per break at most (the reel plays once)
        if key in seen:
            continue

        class _O:   # fit_reel only needs clips / min_clips / min_s
            clips, min_clips, min_s = m.clips, m.min_clips, m.min_s
        n, secs = fit_reel(_O, JumpWindow(m.dead_s, m.out_s, m.back_s, margin).room_s)
        if n == 0:
            continue
        seen.add(key)
        jumps += 1
        away = m.out_s + secs + m.back_s
        if m.true_left < away:
            unsafe += 1
        filled += min(secs, max(0.0, m.true_left - m.out_s))
        commercial += min(secs, max(0.0, m.true_left - m.out_s)) if m.true_brk else 0.0
    return {"margin_s": margin, "jumps": jumps, "unsafe": unsafe, "filled_s": round(filled),
            "commercial_s": round(commercial)}


def fit_margin(moments: list[Moment], tail: float = 0.10) -> float:
    """Smallest margin whose jumps outlast the real dead ball no more often than the window's own
    tail (p10 = 10%). 'unsafe' here assumes the reel always plays to its end; live, the restart shows
    up in the data ~a stream delay before it airs and the decider heads home for it (_home_due), so
    an unsafe moment costs part of the reel, not a play: the full scorecards (reel_home_late_s,
    entries mid-play, plays missed on entry) are what checks that."""
    for m in MARGINS:
        s = score(moments, m)
        if s["unsafe"] <= tail * s["jumps"]:
            return m
    return MARGINS[-1]


# --- nights ------------------------------------------------------------------------------------

def league_nights(league: League, split: str = "fit"):
    """[(night, replays, targets)] of one time split (redzone/lab/splits.py), oldest first. Tuning
    reads only "fit" (train + valid): the LOCKED test nights are never passed here."""
    import importlib.util
    if split == "test":
        raise ValueError("the LOCKED test split is never used for fitting")
    if league == League.NFL:
        from redzone.lab import splits
        spec = importlib.util.spec_from_file_location("_ev", ROOT / "scripts/eval.py")
        ev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ev)
        night = {gid: nt for nt, ids in splits.archive_nights(ROOT / "data/archive/nfl").items() for gid in ids}
        out = [(night.get(reps[0].gid, ""), reps, None) for reps in ev.eval_set(split=split)]
        return sorted(out, key=lambda x: x[0])
    spec = importlib.util.spec_from_file_location("_evs", ROOT / "scripts/eval_sports.py")
    evs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evs)
    return sorted(evs.nights(league, 10_000, evs.evaldata.window_end(league.value, evs.EVAL_END), split),
                  key=lambda x: x[0])


def _fit_one(fit_on, cfg, mine, lg):
    table = p10_table([r for _, reps, _ in fit_on for r in reps])
    cfg.data["reel"]["jump"]["p10_s"] = {lg.value: table}
    m_fit = observe([(reps, t) for _, reps, t in fit_on], cfg, mine)
    return table, fit_margin(m_fit), m_fit


def main(argv=None):
    """Forward-chaining folds over the fit nights (train + valid; never the LOCKED test split): fold k
    fits on blocks 0..k and checks on block k+1. Reports every fold, the mean and the worst fold,
    and the fit-vs-check gap; the final table and margin are fit on all fit nights."""
    from redzone.config import Config
    from redzone.lab import splits
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--json", default=None)
    ap.add_argument("--folds", type=int, default=splits.FOLDS)
    a = ap.parse_args(argv)
    lg = League(a.league)
    pinned = ROOT / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")
    cfg.data["reel"]["play"] = True   # in memory only
    mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
    nights = league_nights(lg, "fit")
    by_night: dict[str, list] = {}
    for nt, reps, t in nights:
        by_night.setdefault(nt, []).append((nt, reps, t))
    order = sorted(by_night)
    k = min(a.folds, max(1, len(order) - 1))
    out = {"league": lg.value, "split": "fit (train + valid)", "nights": order, "folds": []}
    for fit_n, check_n in splits.folds(order, k):
        fit_on = [x for n in fit_n for x in by_night[n]]
        check_on = [x for n in check_n for x in by_night[n]]
        table, margin, m_fit = _fit_one(fit_on, cfg, mine, lg)
        m_chk = observe([(reps, t) for _, reps, t in check_on], cfg, mine)
        fs, cs = score(m_fit, margin), score(m_chk, margin)
        rate = lambda d: round(d["unsafe"] / d["jumps"], 3) if d["jumps"] else 0.0
        out["folds"].append({"fit_through": fit_n[-1], "check": list(check_n), "p10_s": table,
                             "margin_s": margin, "fit": fs, "check_score": cs,
                             "unsafe_rate_fit": rate(fs), "unsafe_rate_check": rate(cs)})
    rates = [f["unsafe_rate_check"] for f in out["folds"] if f["check_score"]["jumps"]]
    out["check_unsafe_rate"] = {"mean": round(sum(rates) / len(rates), 3) if rates else None,
                                "worst": max(rates) if rates else None}
    gaps = [f["unsafe_rate_check"] - f["unsafe_rate_fit"] for f in out["folds"] if f["check_score"]["jumps"]]
    out["gap_check_minus_fit"] = round(sum(gaps) / len(gaps), 3) if gaps else None
    table, margin, allm = _fit_one(nights, cfg, mine, lg)
    out["final"] = {"p10_s": table, "margin_s": max(margin, *(f["margin_s"] for f in out["folds"]), 0.0),
                    "moments": len(allm), "fit_through": order[-1] if order else None}
    txt = json.dumps(out, indent=1)
    print(txt)
    if a.json:
        Path(a.json).write_text(txt)


if __name__ == "__main__":
    main()
