"""[NBA-BUCKET] What a join right after a made basket gets, on --split fit nights, with the flag off and on.

    PYTHONPATH=. python scripts/bucket_join_measure.py window          # make -> next shot percentiles per league
    PYTHONPATH=. python scripts/bucket_join_measure.py sim --sigma 2   # night table, flag off vs on (NBA, CBB)

Reads only fit nights (redzone/lab/splits.py): the locked nights are never loaded. CBB has no archived scoreboards, so a
night is the games that started on one Eastern date; the locked 25 % by that rule are skipped the same way.

The Decider hook (docs/agents/patches/nba-bucket.patch) is applied here at runtime only, so the flag can be measured
before the patch lands; the patch makes the same two calls. Off the flag the hook is inert.

"Judged by Nick's rule" is info only, never a gate: it does not touch the frozen grader (docs/grader_freeze.json).
A join counts as his rule's success when the picture comes up on or after a made basket, before the next possession's
first shot, and inside the measured floor (slate.bucket_floor_*: not in the halfcourt).

Why: Nick's "joining right after a bucket in transition" (decisions.md 2026-11-09) needs its window measured and its
honest failures counted before anyone flips the flag, and the frozen grader counts these joins as mid-play.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config  # noqa: E402
from redzone.decider import gate  # noqa: E402
from redzone.decider.decider import ENTRY_FASTEST_S, Decider  # noqa: E402
from redzone.grading import dead_at  # noqa: E402
from redzone.lab import evaldata, splits, sportsgrade  # noqa: E402
from redzone.lab.backtest import Replay, lag_of, run  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.moments import slate  # noqa: E402
from redzone.sources import espn  # noqa: E402

ARCH = ROOT / "data/archive"
FLOOR_END = "20261101"
FLOOR = {"nba": slate.Params().bucket_floor_nba_s, "cbb": slate.Params().bucket_floor_cbb_s}


def _clock(p) -> float | None:
    c = espn._get(p, "clock", "displayValue")
    m = re.match(r"(?:(\d+):)?(\d+(?:\.\d+)?)$", str(c)) if c is not None else None
    return None if m is None else float(m.group(1) or 0) * 60 + float(m.group(2))


def _ttype(p) -> str:
    return (espn._get(p, "type", "text") or "").lower()


def _is_fg(p) -> bool:
    return bool(p.get("shootingPlay")) and "free throw" not in _ttype(p)


def _period(p):
    return espn._get(p, "period", "number")


def fit_files(league: str) -> list[list[Path]]:
    """Fit nights only, as a list of per-night file lists."""
    a = ARCH / league
    if league == "cbb":
        by = defaultdict(list)
        for f in sorted(a.glob("summary_*.json")):
            w = next((espn._ts(p.get("wallclock")) for p in json.loads(f.read_text()).get("plays") or [] if p.get("wallclock")), None)
            if w is not None:
                by[(dt.datetime.fromtimestamp(w, dt.UTC) - dt.timedelta(hours=5)).strftime("%Y%m%d")].append(f)
        s = splits.league_split("cbb", sorted(by))
        return [by[n] for n in (*s.train, *s.valid)]
    keep = splits.nights_for(a, "fit", evaldata.window_end(league, FLOOR_END))
    out = []
    for sb in sorted(a.glob("scoreboard_*.json")):
        if sb.stem.split("_")[-1] in keep:
            fs = [a / f"summary_{e['id']}.json" for e in json.loads(sb.read_text()).get("events", [])]
            out.append([f for f in fs if f.exists()])
    return out


def after_makes(files: list[Path], last_period: int) -> list[dict]:
    """One row per made field goal with a next field-goal attempt by the other team in the same period."""
    out = []
    for f in files:
        ps = json.loads(f.read_text()).get("plays") or []
        for i, p in enumerate(ps):
            if not (_is_fg(p) and p.get("scoringPlay")):
                continue
            w0, c0, per = espn._ts(p.get("wallclock")), _clock(p), _period(p)
            if w0 is None or c0 is None:
                continue
            and1 = stop = False
            nx = None
            for q in ps[i + 1:i + 25]:
                if _period(q) != per:
                    break
                t, cq = _ttype(q), _clock(q)
                and1 |= cq == c0 and ("foul" in t or "free throw" in t)
                stop |= any(x in t for x in ("timeout", "review", "jump ball")) or ("foul" in t and cq != c0)
                if _is_fg(q):
                    nx = q
                    break
            wn, cn = (espn._ts(nx.get("wallclock")), _clock(nx)) if nx is not None else (None, None)
            if wn is None or cn is None or (nx.get("team") or {}).get("id") == (p.get("team") or {}).get("id"):
                continue
            fast = False
            for q in reversed(ps[max(0, i - 6):i]):
                t, cq = _ttype(q), _clock(q)
                if _period(q) != per or _is_fg(q) or "offensive rebound" in t:
                    break
                if "defensive rebound" in t or "steal" in t or "turnover" in t:
                    fast = cq is not None and cq - c0 <= 8
                    break
            out.append({"wall": wn - w0, "clock": c0 - cn, "and1": and1, "stop": stop, "fast": fast,
                        "last2": per >= last_period and c0 <= 120})
    return out


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[round(q / 100 * (len(xs) - 1))] if xs else float("nan")


def window_report() -> dict:
    out = {}
    for lg, last in (("nba", 4), ("cbb", 2)):
        rows = after_makes([f for night in fit_files(lg) for f in night], last)
        groups = {"all": rows, "no_stop": [r for r in rows if not r["stop"] and not r["and1"]],
                  "fast_break": [r for r in rows if r["fast"] and not r["stop"] and not r["and1"]],
                  "and_one": [r for r in rows if r["and1"]], "last_2_min": [r for r in rows if r["last2"]]}
        out[lg] = {k: {"n": len(v), "wall": {f"p{q}": pct([r["wall"] for r in v], q) for q in (1, 5, 10, 25, 50, 75, 90)},
                       "clock": {f"p{q}": pct([r["clock"] for r in v], q) for q in (1, 5, 10, 25, 50, 75, 90)}}
                   for k, v in groups.items()}
    return out


# ----- the runtime hook: exactly what docs/agents/patches/nba-bucket.patch does, measured here before it lands -----

PLANNED: set[tuple[str, float]] = set()   # (game, tick) of every entry plan the hook returned: which cuts were bucket joins


def install_hook() -> None:
    plain_plan, plain_jump = Decider.entry_plan, Decider.jump_lands_dead

    def entry_plan(self, gid, cands, now, cost=None, urgent=False, possession=False):
        plan = plain_plan(self, gid, cands, now, cost, urgent, possession)
        if plan is None or (plan.ok and not plan.rewind_s) or not gate.bucket_on(self.cfg):
            return plan
        x, tl = cands.get(gid) or self._known.get(gid), self.timelines.get(gid)
        if x is None or tl is None or not x.target:
            return plan
        c = cost if cost is not None else (self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target))
        fastest = float(self.cfg.get("decider.entry.fastest_s", ENTRY_FASTEST_S))
        b = gate.bucket_plan(self.cfg, tl, x.scored, self.delays.get(x.target), now, c, fastest)
        if b is not None:
            PLANNED.add((gid, round(now, 3)))
        return b or plan

    def jump_lands_dead(self, gid, x, now, cost=None):
        if plain_jump(self, gid, x, now, cost):
            return True
        tl = self.timelines.get(gid)
        if not gate.bucket_on(self.cfg) or x is None or tl is None or not x.target or self._bj_pic(gid, x, now) is not None:
            return False
        c = cost if cost is not None else (self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target))
        fastest = float(self.cfg.get("decider.entry.fastest_s", ENTRY_FASTEST_S))
        land = float(self.cfg.get("jump_mode.land_dead_s", 2.0))
        ok = gate.bucket_plan(self.cfg, tl, x.scored, self.delays.get(x.target), now, c + land, fastest) is not None
        if ok:
            PLANNED.add((gid, round(now, 3)))
        return ok

    Decider.entry_plan, Decider.jump_lands_dead = entry_plan, jump_lands_dead


def nights_of(league: str):
    lg = League(league)
    for night in fit_files(league):
        reps = [r for f in night if (r := Replay.load(lg, f))]
        if len(reps) >= 2:
            yield reps


def _last_make(r, st: float):
    """(make wall, next FG attempt wall or None) when the last live action on screen at st is a made FG. Substitutions
    and other non-actions after the make don't count as action."""
    i = max((k for k, w in enumerate(r.wall) if w <= st), default=-1)
    while i >= 0 and "substitution" in _ttype(r.plays[i]):
        i -= 1
    if i < 0 or not (r.plays[i].get("scoringPlay") and (_is_fg(r.plays[i]) or "free throw" in _ttype(r.plays[i]))):
        return None
    nxt = next((r.wall[k] for k in range(i + 1, min(len(r.plays), i + 25)) if _is_fg(r.plays[k])), None)
    return r.wall[i], nxt


def _nearby_make(r, st: float, lim: float = 12.0) -> float | None:
    """The next made basket's wall time within lim s after st (the picture came up before it), or None."""
    for w, p in zip(r.wall, r.plays):
        if st < w <= st + lim and p.get("scoringPlay") and (_is_fg(p) or "free throw" in _ttype(p)):
            return w
    return None


def judge(reps, rep, tune_s: float, floor: float) -> dict:
    """Per entry, the frozen verdict; for bucket joins (hook planned), Nick's rule and the honest failures."""
    by = {r.gid: r for r in reps}
    frozen_mid = joins = ok = before_make = after_shot = halfcourt = other = 0
    for s in rep.switches:
        if s.kind == "replay":
            continue
        r = by.get(s.to or "")
        if r is None:
            continue
        off = getattr(s, "offset", 0.0) or 0.0
        st = s.t + tune_s + (getattr(s, "arrive_dt", 0.0) or 0.0) - lag_of(rep.lag_true, r.gid) - off
        if st < r.wall[0] or st > r.wall[-1]:
            continue
        frozen_mid += not dead_at(r.league, r.wall, r.plays, st)[0]
        if (s.to, round(s.t, 3)) not in PLANNED or off > 0:
            continue
        joins += 1
        made = _last_make(r, st)
        if made is None:
            next_make = _nearby_make(r, st)
            before_make += next_make is not None
            other += next_make is None
            continue
        make, nxt = made
        if nxt is not None and st >= nxt:
            after_shot += 1
        elif st - make > floor:
            halfcourt += 1
        else:
            ok += 1
    return {"frozen_mid_play": frozen_mid, "bucket_joins": joins, "nick_ok": ok, "before_make": before_make,
            "after_next_shot": after_shot, "halfcourt_late": halfcourt, "other_miss": other}


def sim_one(league: str, reps, flag: bool, knobs: dict, extra: list[str], tune_s: float = 5.0) -> dict:
    cfg = Config(user_path=ROOT / "data/eval_config.yaml")
    evaldata.use_shipped_switch_costs(cfg)
    evaldata.apply_set(cfg, f"basketball.join_after_bucket={'true' if flag else 'false'}")
    for kv in extra:
        evaldata.apply_set(cfg, kv)
    evaldata.flatten_league_overrides(cfg, league)
    mine = set(cfg.get(f"teams.{league}") or ["WSH"])
    PLANNED.clear()
    rep = run(reps, cfg, mine, **knobs)
    card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, tune_s, rep.t0, rep.t1, rep.replayed)
    cs = card.cut.summary()
    j = judge(reps, rep, tune_s, FLOOR[league])
    hours = max(0.001, (rep.t1 - rep.t0) / 3600)
    waits = want_waits(rep.decisions, rep.switches)
    return {"entries": cs["entries"], "entries_mid_play": cs["entries_mid_play"], "plays_missed_entry": cs["plays_missed_entry"],
            "switches": card.switches, "hours": round(hours, 3), "dead_s": round(card.dead, 1), "watched_s": round(card.watched, 1),
            "big": card.big, "big_seen": card.big_seen, "wait_sum_s": round(sum(waits), 1), "wait_n": len(waits),
            "mid_play_cuts": len(card.cuts), "high_leverage_mid_leaves": len(card.cuts_high),
            "makes": opportunities(reps, FLOOR[league], knobs.get("sigma", 3.0))[0],
            "makes_that_fit": opportunities(reps, FLOOR[league], knobs.get("sigma", 3.0))[1], **j}


SPREAD_S = 1.5   # the arrival spread the fit check assumes: tune cost less the fastest flip (5.5 s vs 4 s)


def opportunities(reps, floor: float, sigma: float) -> tuple[int, int]:
    """(made baskets, of those the ones whose window can hold the landing at this doubt)."""
    p = slate.Params()
    doubt, makes, fit = p.bucket_sigmas * sigma, 0, 0
    for r in reps:
        for i, row in enumerate(r.plays):
            if not (row.get("scoringPlay") and (_is_fg(row) or "free throw" in _ttype(row))):
                continue
            makes += 1
            nxt = next((r.wall[k] for k in range(i + 1, len(r.plays)) if "substitution" not in _ttype(r.plays[k])
                        and r.wall[k] > r.wall[i]), None)
            end = floor if nxt is None else min(floor, nxt - r.wall[i] - p.bucket_guard_s)
            fit += end - p.bucket_after_s >= 2 * doubt + SPREAD_S
    return makes, fit


def want_waits(decisions, switches) -> list[float]:
    """Seconds from the first tick the decider wanted a game (a held switch) until it actually cut there."""
    first: dict[str, float] = {}
    out = []
    by_t = {round(s.t, 3): s for s in switches}
    for d in decisions:
        s = by_t.get(round(getattr(d, "at", 0.0), 3))
        if s is not None and s.to in first:
            out.append(s.t - first.pop(s.to))
        elif getattr(d, "want", None) and d.want not in first:
            first[d.want] = d.at
    return out


def _total(rows: list[dict]) -> dict:
    t = {k: round(sum(r[k] for r in rows), 2) for k in rows[0]}
    t["cuts_per_10min"] = round(t["switches"] / max(0.1, t["hours"] * 6), 2)
    t["dead_air_pct"] = round(100 * t["dead_s"] / max(1.0, t["watched_s"]), 2)
    t["must_show_seen_pct"] = round(100 * t["big_seen"] / max(1, t["big"]), 1)
    t["mean_wait_s"] = round(t["wait_sum_s"] / max(1, t["wait_n"]), 1)
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", choices=("window", "sim"))
    ap.add_argument("--sigma", type=float, default=3.0)
    ap.add_argument("--step", type=float, default=None)
    ap.add_argument("--feed-lag", type=float, default=None)
    ap.add_argument("--real-lag", action="store_true")
    ap.add_argument("--true-delay", type=float, default=None)
    ap.add_argument("--leagues", default="nba,cbb")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override config for both runs, e.g. jump_mode.enabled=true")
    ap.add_argument("--nights", type=int, default=99)
    a = ap.parse_args()
    if a.what == "window":
        print(json.dumps(window_report(), indent=1))
        return
    install_hook()
    knobs: dict = {"sigma": a.sigma, "step": a.step, "true_delay": a.true_delay}
    if a.real_lag:
        knobs["feed_lag_model"] = "measured"
    if a.feed_lag is not None:
        knobs["feed_lag"] = a.feed_lag
    out: dict = {}
    for lg in a.leagues.split(","):
        rows = []
        for i, reps in enumerate(nights_of(lg)):
            if i >= a.nights:
                break
            rows.append({"off": sim_one(lg, reps, False, knobs, a.set), "on": sim_one(lg, reps, True, knobs, a.set)})
        out[lg] = {"nights": len(rows), "per_night": rows,
                   "total": {side: _total([r[side] for r in rows]) for side in ("off", "on")} if rows else {}}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
