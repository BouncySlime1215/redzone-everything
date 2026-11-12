"""Catch-up replay: the PARK show with the catch-up move (park.catchup) on recorded dev nights, fake sticks, no device.

    nice -n 15 python scripts/catchup_replay.py --night 20261028 --games 4                  # proven numbers only
    nice -n 15 python scripts/catchup_replay.py --night 20261028 --games 4 --scenario library --lines 20
    nice -n 15 python scripts/catchup_replay.py --table                                     # the PR's table

What runs: `scripts/park_replay.replay` (the app's own `Show` on a recorded night, fed row by row, fake sticks and a fake
TV, Rule #1 judged where the pictures truly were) with three classes swapped in for the run only:
  * `redzone.noggin.park_catchup.CatchupShow` for `Show` (with park.catchup.enabled off it IS the show);
  * `CatchupRig` for `FakeRig`: the fake sticks plus `cu_cue`, the catch-up's one new step (a stick put at a clip's
    start by rewind, Key plays reel or Prime Key Moments), timed from catchup_pack.PackParams (measured where measured,
    named priors where not);
  * `CatchupJudge` for `Judge`: a catch-up is ONE cut for the judge and the frozen grader: the leave of the screen's game
    (live) and the live join of game X, with the replay between them. The replay is not a live picture.
park_replay.py itself is not edited (claimed by PARK-UNIFY until its claim closes); this script wraps it.

The age rule in every scenario is Nick's (Nov 11): 2+ plays have no age cap, one play keeps the 3-minute cap.
The scenarios (catchup_pack's docstring has every unknown):
  off       the show as it is (park.catchup off)
  proven    rewind reach 175 s behind live (measured Nov 9), no reel, no Prime Key Moments
  rewind10  WHAT-IF: the hidden stick rewinds 10 minutes behind live
  library   WHAT-IF: the hidden stick rewinds the whole game (Nick's call #3, games in the YouTube TV library)
  reel      WHAT-IF: proven rewind, plus PR 382's Key plays reel (one read: 12 min lag) and Prime Key Moments (priors)

Replayed with fake sticks is NOT measured on the TV. Dev nights only.

Why: the catch-up engine (phase 2 plan 2C item 3) has to show, before it is switched on, how often a package fits, what it
shows, and that its live joins keep Rule #1 under the frozen grader.
"""
from __future__ import annotations

import argparse
import contextlib
import math
import random
import sys
from dataclasses import replace
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_replay as pr  # noqa: E402
from redzone.noggin import catchup_pack as cp  # noqa: E402
from redzone.noggin.park_catchup import CatchupParams, CatchupShow  # noqa: E402

SCENARIOS = {
    "off": None,
    "proven": {},
    "rewind10": {"reach_s": 600.0},
    "library": {"rewind_rule": "library"},
    "reel": {"reel": True, "moments": True},
}
NIGHTS = ("20261023", "20261028", "20261021")
# the NFL + Prime case of tests/test_park_nfl_lag.py (one NBA game, the NFL dev night on Prime, its feed 44 s late, parked deeper)
NFL_PRIME = {"nfl": 0, "prime": 1, "nfl_lag": 44.0, "deep": True}


# ---------------------------------------------------------------------------------------------- the fakes
class CatchupRig(pr.FakeRig):
    """The fake sticks plus the catch-up's step. `cu_cue(k, game, frame, source, visible)`: stick k is put at grid second
    `frame` of `game`, paused, by `source`; it takes the package's own model of that source (prep when hidden, the
    gap when the replay is already on screen). It lands within the read's error (cue_sd)."""

    def __init__(self, *a, pack: cp.PackParams | None = None, cue_sd: float = 1.0, **kw):
        super().__init__(*a, **kw)
        self.pack, self.cue_sd = pack or cp.PackParams(), cue_sd
        self.cues: list = []

    async def cu_cue(self, k: int, game: int, frame: float, source: str, visible: bool) -> tuple:
        P, now = self.pack, self._now()
        if source == "rewind":
            took = P.seek_gap_s if visible else P.seek_base_s + P.seek_per_s * max(0.0, now + self.delay - frame)
        elif source == "reel":
            took = P.reel_gap_s if visible else P.reel_open_s
        else:
            took = P.moments_gap_s if visible else P.moments_open_s
        took += min(4.0, self.rng.expovariate(1 / 0.8))
        self.sent.append(("cue", k, game, source))
        await self._wait(took)
        if self._fails("cue"):
            return False, took
        err = self.rng.gauss(0.0, self.cue_sd)
        self.game[k], self.paused[k], self.frozen[k] = game, True, float(frame) + err
        self.lag[k] = self._now() - self.frozen[k]
        self.cues.append((k, game, float(frame), err, source))
        return True, took


class CatchupJudge(pr.Judge):
    """The judge plus a catch-up's one cut: the leave's true second taken at the leave, the join's at the join."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.catchups: list[int] = []                           # indices into self.cuts that were catch-ups

    def leave_true(self, k: int, g: int, i: int) -> float:
        e1 = self.tm.errs(g)
        return i - self.e.get(k, 0.0) + self.rng.gauss(0.0, math.hypot(e1[2], e1[3]))

    def catchup_cut(self, t: int, g1: int, true_i: float, k2: int, g2: int, j: int, paused: bool) -> None:
        """Judge.flip's bookkeeping, with the leave already taken (the same rules, line for line)."""
        xs, xh, bad = self.A[g1], self.A[g2], 0
        e2 = self.tm.errs(g2)
        true_j = j - self.e.get(k2, 0.0) + self.rng.gauss(0.0, e2[2] if paused else math.hypot(e2[2], e2[3]))
        ti, ej = self._at(xs, true_i), self._at(xh, true_j)
        self.catchups.append(len(self.cuts))
        self.cuts.append((g1, true_i, g2, true_j))
        self.e_log.append((0.0, self.e.get(k2, 0.0)))
        if not xs.dead[ti]:
            if xs.legal[ti]:
                self.walk_leave += 1
            else:
                self.mid_leave, bad = self.mid_leave + 1, bad + 1
        if xs.hl[ti]:
            self.hl_leave, bad = self.hl_leave + 1, bad + 1
        if not xh.dead[ej]:
            if xh.legal[ej]:
                self.walk_entry += 1
            else:
                self.mid_entry, bad = self.mid_entry + 1, bad + 1
        if self.last is not None and self.live >= 3:
            self.jumps.append(self.last)
            self.plays += self.look_plays
            self.one_play += int(self.look_plays <= 1)
        elif self.last is not None:
            self.noplay += 1
        wait = xh.left[ej] if xh.dead[ej] else 0
        self.land_wait.append(wait)
        self.brk_land += int(bool(xh.brk[ej]) and wait > 10)
        self.flips.append((t, g2, bad))
        self.last, self.live, self.look_plays, self.was_dead, self.from_true = t, 0, 0, True, None


@contextlib.contextmanager
def wired(params: CatchupParams):
    """park_replay's Show, FakeRig and Judge swapped for the catch-up ones, for one run."""
    old = pr.Show, pr.FakeRig, pr.Judge
    pr.Show, pr.FakeRig, pr.Judge = partial(CatchupShow, catchup=params), partial(CatchupRig, pack=params.pack), CatchupJudge
    try:
        yield
    finally:
        pr.Show, pr.FakeRig, pr.Judge = old


def params_for(scenario: str) -> CatchupParams | None:
    over = SCENARIOS[scenario]
    if over is None:
        return None
    return CatchupParams(enabled=True, pack=replace(cp.PackParams(), **over))


def run(night: str, games: int, scenario: str, **kw):
    """One replay; (show, catch-up params or None)."""
    c = params_for(scenario)
    if c is None:
        return pr.replay(night, games, **kw), None
    with wired(c):
        return pr.replay(night, games, **kw), c


# ---------------------------------------------------------------------------------------------- the numbers
def _true_lo(judge, g: int, lo: float, rng: random.Random) -> float:
    """A clip's first second where the picture truly was: this game's K error, and the read of the cue."""
    return lo - judge.gd[g] - rng.gauss(0.0, judge.tm.errs(g)[0])


def metrics(show, c: CatchupParams | None) -> dict:
    """The catch-up's numbers on one run, judged on the whole recorded night (the judge's own arrays)."""
    from redzone.grading import SwitchLog, dead_at
    from redzone.lab import cutgrade
    j, rs, base = show.judge, show.replays, show.night.base
    a = show.night.span[0]
    end = (show.last_t + 1) if show.last_t is not None else a
    row = pr.result(show)
    big_all = {(g, m) for g, x in enumerate(j.A) for m in x.mom if a <= m < end}
    v2_all = {(g, m): v for g, x in enumerate(j.A) for m, v in x.v2.items() if a <= m < end and v > 0}
    live = {(g, m) for g, m in j.shown}
    via, v2_via, starts = set(), {}, {"dead": 0, "event": 0, "mid": 0}
    late, rng = 0, random.Random(7)
    log = getattr(show, "cu_log", [])
    for r in log:
        g = r["game"]
        x, rec = j.A[g], rs[g]
        for clip in r["clips"]:
            lo = _true_lo(j, g, clip.lo, rng)
            hi = lo + (clip.hi - clip.lo)
            for m in x.mom:
                if lo <= m <= hi and (g, m) not in live:
                    via.add((g, m))
            for m, v in x.v2.items():
                if v > 0 and lo <= m <= hi and (g, m) not in live:
                    v2_via[(g, m)] = v
            st = base + lo
            if rec.wall[0] <= st <= rec.wall[-1] and dead_at(rec.league, rec.wall, rec.plays, st)[0]:
                starts["dead"] += 1
            elif any(st - 0.5 <= w <= st + c.pack.lead_s + 2.5 for w in rec.wall):
                starts["event"] += 1
            else:
                starts["mid"] += 1
            late += int(clip.air + show.delay - clip.lo >= cp.age_cap(r.get("n_planned", len(r["plays"])), c.pack))
    out = {"minutes": row["minutes"], "jumps_per_min": row["jumps_per_min"], "behind_max_s": row["behind_max_s"],
           "grader_entries_mid_play": row["grader_entries_mid_play"], "grader_leaves_mid_play": row["grader_leaves_mid_play"],
           "grader_entries": row["grader_entries"], "mid_entry": row["mid_entry"], "mid_leave": row["mid_leave"], "hl_leave": row["hl_leave"],
           "big_n": len(big_all), "big_live": len(big_all & live), "big_via": len(via & big_all),
           "big_never": len(big_all - live - via),
           "v2_live": round(sum(v for k, v in v2_all.items() if k in live) / max(1e-9, sum(v2_all.values())), 3),
           "v2_via": round(sum(v2_via.values()) / max(1e-9, sum(v2_all.values())), 3)}
    if c is None:
        return out
    chances = show.cu_chances
    why: dict = {}
    for w in chances.values():
        if w:
            why[w] = why.get(w, 0) + 1
    for w, n in show.cu_aborts.items():
        why[w] = why.get(w, 0) + n
    lengths = sorted(r["screen_s"] for r in log)
    oldest = sorted(max(cp.age_s(cl.lo, cl.air, c.pack) for cl in r["clips"]) for r in log if r["clips"])
    hist: dict = {}
    for r in log:
        hist[len(r["plays"])] = hist.get(len(r["plays"]), 0) + 1
    plays = [len(r["plays"]) for r in log]
    src: dict = {}
    for r in log:
        for k, n in r["sources"].items():
            src[k] = src.get(k, 0) + n
    # the frozen grader on the catch-up cuts alone (the leave of the live game, the live join)
    cuts = [j.cuts[i] for i in getattr(j, "catchups", [])]
    sl = [SwitchLog(base + pj, rs[g1].gid, rs[g2].gid, "jump", "catch-up replay", leave_dt=pi - pj) for g1, pi, g2, pj in cuts]
    card = cutgrade.grade(rs, sl, 0.0, 0.0) if sl else None
    out.update(chances=len(chances), started=len(log) + sum(show.cu_aborts.values()), done=len(log), why_not=why,
               mean_s=round(sum(lengths) / len(lengths), 1) if lengths else 0.0, max_s=max(lengths) if lengths else 0,
               plays_per=round(sum(plays) / len(plays), 2) if plays else 0.0, sources=src,
               prep_s=round(sum(r["prep_s"] for r in log) / len(log), 1) if log else 0.0,
               join_wait_s=round(sum(r["join_wait_s"] for r in log) / len(log), 1) if log else 0.0,
               behind_join_s=max((r["behind_s"] for r in log), default=0.0),
               cu_joins=len(cuts), cu_join_mid=card.entries_mid_play if card else 0,
               cu_join_mid_strict=card.entries_mid_play_strict if card else 0,
               cu_leave_mid=(card.leaves - card.leaves_in_dead - card.leaves_between_plays) if card else 0,
               clip_starts=starts, clips_late=late, lines=[r["line"] for r in log],
               plays_hist=dict(sorted(hist.items())), len_med_s=_q(lengths, 0.5), len_p90_s=_q(lengths, 0.9),
               oldest_med_s=_q(oldest, 0.5), oldest_max_s=round(oldest[-1]) if oldest else 0)
    return out


def _q(xs: list, q: float) -> float:
    return round(xs[min(len(xs) - 1, int(q * len(xs)))]) if xs else 0


def describe(m: dict, head: str) -> str:
    lines = [head,
             f"  minutes {m['minutes']}, jumps {m['jumps_per_min']} a minute, worst behind live {m['behind_max_s']} s",
             f"  Rule #1, FROZEN grader, all joins: mid-play joins {m['grader_entries_mid_play']} of {m['grader_entries']}, "
             f"mid-play leaves {m['grader_leaves_mid_play']}; replay judge: {m['mid_entry']} / {m['mid_leave']} / {m['hl_leave']} (join / leave / high-leverage leave)",
             f"  big plays (grader's must-sees, {m['big_n']}): live {m['big_live']}, via catch-up {m['big_via']}, never {m['big_never']}; "
             f"graded value seen live {100 * m['v2_live']:.0f}%, via catch-up +{100 * m['v2_via']:.0f}%"]
    if "chances" in m:
        lines += [f"  catch-ups: {m['done']} done of {m['chances']} chances ({m['started']} started); "
                  f"mean {m['mean_s']} s on screen (max {m['max_s']}), {m['plays_per']} plays each, prep {m['prep_s']} s, "
                  f"join wait {m['join_wait_s']} s, join at most {m['behind_join_s']} s behind live; sources {m['sources'] or '-'}",
                  f"  catch-up cuts, FROZEN grader: joins mid-play {m['cu_join_mid']} of {m['cu_joins']} (strict {m['cu_join_mid_strict']}), "
                  f"leaves mid-play {m['cu_leave_mid']}; clip starts {m['clip_starts']}; clips over their age cap {m['clips_late']}",
                  f"  packages by plays {m['plays_hist']}; length median {m['len_med_s']} s, p90 {m['len_p90_s']} s; "
                  f"oldest clip median {m['oldest_med_s']} s, max {m['oldest_max_s']} s behind live",
                  "  why not: " + ("; ".join(f"{k} {v}" for k, v in sorted(m["why_not"].items(), key=lambda kv: -kv[1])) or "-")]
        if m["lines"]:
            lines.append("  e.g. " + " | ".join(m["lines"][:3]))
    return "\n".join(lines)


def table(seed: int = 0, dump: str = "") -> str:
    """The PR's table: the 3 NBA dev nights (4 games) and the pinned NFL + Prime case; off, proven, and the 10-minute
    rewind what-if. `dump`: also write each run's numbers as one JSON line to this file."""
    import json
    cases = [(f"NBA {n}, 4 games", n, 4, {}) for n in NIGHTS]
    cases += [("NFL on Prime + 1 NBA, pinned", "20261023", 1, {**NFL_PRIME, "pin": True})]
    head = ("| case | rewind | fits / chances | packages by plays (1/2/3/4/5) | length median / p90 s | oldest clip median / max s "
            "behind live | missed big plays: via catch-up / never | graded value seen: live + via catch-up | "
            "frozen grader: mid-play joins / leaves (catch-up joins) | jumps a min | worst behind live s |")
    rows = [head, "|" + "---|" * 11]
    print(head, flush=True)
    names = {"off": "off", "proven": "175 s (proven)", "rewind10": "10 min (what-if)", "library": "whole game (what-if)"}
    for label, night, games, kw in cases:
        for sc in ("off", "proven", "rewind10"):
            show, c = run(night, games, sc, seed=seed, **kw)
            m = metrics(show, c)
            missed = m["big_n"] - m["big_live"]
            seen = f"{100 * m['v2_live']:.0f}%" + (f" + {100 * m['v2_via']:.0f}%" if c else "")
            if c is None:
                cells = ["-", "-", "-", "-", f"{missed} missed: - / {m['big_never']}"]
            else:
                hist = "/".join(str(m["plays_hist"].get(k, 0)) for k in range(1, 6))
                cells = [f"{m['done']} / {m['chances']}", hist, f"{m['len_med_s']} / {m['len_p90_s']}",
                         f"{m['oldest_med_s']} / {m['oldest_max_s']}", f"{missed} missed: {m['big_via']} / {m['big_never']}"]
            fg = f"{m['grader_entries_mid_play']} / {m['grader_leaves_mid_play']}" + (f" ({m.get('cu_join_mid', 0)} of {m.get('cu_joins', 0)})" if c else "")
            rows.append(f"| {label} | {names[sc]} | " + " | ".join(cells) + f" | {seen} | {fg} | {m['jumps_per_min']} | {m['behind_max_s']} |")
            print(rows[-1], flush=True)
            if dump:
                with open(dump, "a") as fh:
                    fh.write(json.dumps({"case": label, "scenario": sc, **{k: v for k, v in m.items() if k != "lines"}}) + "\n")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="The PARK show with catch-up packages, on a recorded dev night, with fake sticks.")
    ap.add_argument("--night", default="20261028")
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--scenario", choices=tuple(SCENARIOS), default="proven")
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lines", type=int, default=0, help="how many show lines to print")
    ap.add_argument("--nfl", type=int, default=-1)
    ap.add_argument("--prime", type=int, default=0)
    ap.add_argument("--nfl-lag", type=float, default=None)
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--pin", action="store_true")
    ap.add_argument("--table", action="store_true", help="the 3 NBA nights and the NFL + Prime case, every scenario")
    ap.add_argument("--dump", default="", help="with --table: also append each run's numbers as JSON lines here")
    a = ap.parse_args()
    if a.table:
        table(a.seed, a.dump)
        return 0
    shown = [0]

    def say(line: str) -> None:
        shown[0] += 1
        if shown[0] <= a.lines:
            print(line)
    show, c = run(a.night, a.games, a.scenario, minutes=a.minutes, seed=a.seed, say=say, nfl=a.nfl, prime=a.prime,
                  nfl_lag=a.nfl_lag, deep=a.deep, pin=a.pin)
    print(describe(metrics(show, c), f"CATCH-UP REPLAY ({a.scenario}; night {a.night}, {a.games} games, FAKE sticks, not measured on the TV)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
