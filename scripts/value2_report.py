#!/usr/bin/env python3
"""Value v2 against value v1 on archived dev games: what each one calls a big moment. Read only, no training.

    python scripts/value2_report.py --archive data/archive --leagues nfl,nba --max-nights 12 \
        --top nfl:20251207,nba:20261028 > report.md

Why: v1 gives a garbage-time touchdown the same 5 points as a go-ahead one (docs/plans/2026-11-09-value-v2.md). Before
any label or model moves to v2, Nick has to be able to read the top plays of a night he remembers and say "yes, those".
So this prints, per league: every v2 kind (count, mean base, mean value), the leverage and stakes spread, one night's
top 15 plays by v2 and by v1, the rank agreement of 3-minute windows, and where the two disagree most.

Dev nights only: a summary is opened only if scripts/coverage_audit.DevOnly allows its night; locked and fresh nights
stay closed. v1 here is the shard label's v1: published win probability is stripped first (value_shards.py), so wp5 and
wp10 never fire. Prints markdown to stdout; nothing is written to the repo.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from redzone.moments import value2  # noqa: E402
from redzone.moments.detect import detect, game_span  # noqa: E402
from redzone.noggin import value_shards as V  # noqa: E402
from scripts.coverage_audit import DevOnly  # noqa: E402

WINDOW = V.HORIZON_S
TOP_N = 15
ROUTINE = {"football": (1.0, "a 20-yard gain or a sack in an ordinary game state"),
           "hoops": (0.5, "a plain dunk or an early lead change in an ordinary game state")}
BIG = 5.0        # one ordinary-state touchdown


def _teams(d: dict) -> str:
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    by = {c.get("homeAway"): (c.get("team") or {}).get("abbreviation") or "?" for c in comp.get("competitors") or []}
    return f"{by.get('away', '?')} @ {by.get('home', '?')}"


def _clock(league: str, p: dict) -> str:
    per = (p.get("period") or {}).get("number") or 0
    regs = value2.config()["regulation"][league][0]
    name = f"Q{per}" if per <= regs else ("OT" if per == regs + 1 else f"OT{per - regs}")
    return f"{name} {(p.get('clock') or {}).get('displayValue') or '?'}"


def read_games(archive: Path, league: str, max_nights: int, must: set[str]):
    """(night, game id, clean summary) for the newest max_nights dev nights, plus any night in `must` (dev nights only)."""
    dev = DevOnly(archive, league)
    ids = dev.game_ids()
    nights = sorted(set(ids.values()))
    dev.require(must)                              # a locked night asked for by name is an error, not a silent skip
    keep = set(nights[-max_nights:]) | must
    for gid, n in sorted(ids.items(), key=lambda x: (x[1], x[0])):
        f = archive / league / f"summary_{gid}.json"
        if n in keep and f.is_file():
            yield n, gid, V.wall_ordered(league, V.strip_published_wp(json.loads(f.read_text())))


def league_tables(archive: Path, league: str, max_nights: int, top_night: str | None) -> dict:
    v2_rows, plays, windows, fields = [], [], [], collections.Counter()
    games = nights = 0
    seen_nights = set()
    for night, gid, d in read_games(archive, league, max_nights, {top_night} if top_night else set()):
        det = detect(league, d, gid)
        if game_span(det.times) is None:
            continue
        games += 1
        seen_nights.add(night)
        st = value2.game_stakes(d)
        fields[f"season type {st.season_type}"] += 1
        fields["has a pre-game spread"] += st.spread is not None
        fields["spread of 7 or more"] += bool(st.spread is not None and st.spread >= value2.config()["stakes"]["upset_spread"])
        fields["both teams ranked"] += st.ranked_vs_ranked
        graded = value2.grade(league, d, gid)
        index = {str(p.get("id")): p for _, p in V.flat_plays(league, d)}
        v1_by = collections.defaultdict(list)
        for m in det.moments:
            v1_by[m.play_id].append(m)
        v2_by = collections.defaultdict(list)
        for g in graded:
            v2_by[g.play_id].append(g)
            v2_rows.append({"kind": g.kind, "base": g.base, "leverage": g.leverage, "stakes": g.stakes, "value": g.value,
                            "dead": g.dead})
        for pid in set(v1_by) | set(v2_by):
            p = index.get(pid) or {}
            g2, m1 = v2_by.get(pid, []), v1_by.get(pid, [])
            plays.append({
                "night": night, "game": _teams(d), "gid": gid, "pid": pid, "t": (g2[0].t if g2 else m1[0].t),
                "when": _clock(league, p), "score": f"{p.get('awayScore', '?')}-{p.get('homeScore', '?')}",
                "v2": sum(g.value for g in g2), "v2_kinds": "+".join(g.kind for g in g2),
                "lev": max((g.leverage for g in g2 if not g.dead), default=float("nan")),
                "v1": sum(V.TIER_WEIGHTS[m.tier] for m in m1), "v1_kinds": "+".join(m.kind for m in m1),
                "v1_tier1": any(m.tier == 1 for m in m1),
                "text": " ".join(str(p.get("text") or "").split())[:150]})
        lo, hi = min(det.times), max(det.times)
        edges = np.arange(lo, hi, WINDOW)
        t1 = np.array([m.t for m in det.moments])
        w1 = np.array([V.TIER_WEIGHTS[m.tier] for m in det.moments])
        t2 = np.array([g.t for g in graded])
        w2 = np.array([g.value for g in graded])
        for e in edges:
            windows.append((float(w1[(t1 > e) & (t1 <= e + WINDOW)].sum()) if len(t1) else 0.0,
                            float(w2[(t2 > e) & (t2 <= e + WINDOW)].sum()) if len(t2) else 0.0))
    nights = len(seen_nights)
    return {"league": league, "games": games, "nights": nights, "night_span": (min(seen_nights), max(seen_nights)) if seen_nights else None,
            "v2": pd.DataFrame(v2_rows), "plays": pd.DataFrame(plays), "windows": pd.DataFrame(windows, columns=["v1", "v2"]),
            "fields": fields, "top_night": top_night}


def _md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(f"{x:.2f}" if isinstance(x, float) else str(x).replace("|", "/") for x in r) + " |")
    return "\n".join(out)


def _top(plays: pd.DataFrame, by: str) -> pd.DataFrame:
    other = "v1" if by == "v2" else "v2"
    top = plays.sort_values([by, "t"], ascending=[False, True], kind="stable").head(TOP_N)
    return pd.DataFrame({"#": range(1, len(top) + 1), by: top[by].to_numpy(float), other: top[other].to_numpy(float),
                         "game": top.game, "when": top.when, "score after (away-home)": top.score,
                         "kinds": top[f"{by}_kinds"], "play": top.text})


def render(r: dict) -> str:
    lg, fam = r["league"], value2.family(r["league"])
    v2, plays, win = r["v2"], r["plays"], r["windows"]
    out = [f"## {lg.upper()}: {r['games']} games on {r['nights']} dev nights ({r['night_span'][0]} to {r['night_span'][1]})", ""]
    kinds = v2.groupby("kind").agg(count=("value", "size"), mean_base=("base", "mean"), mean_value=("value", "mean"),
                                   min_value=("value", "min"), max_value=("value", "max")).reset_index()
    out += ["### Every v2 kind", "", _md(kinds.sort_values("count", ascending=False)), ""]
    live = v2[~v2.dead]
    q = live.leverage.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(2).tolist()
    bands = [("0.3 (floor)", live.leverage <= 0.3), ("0.31-0.9", (live.leverage > 0.3) & (live.leverage < 1.0)),
             ("1.0-1.2", (live.leverage >= 1.0) & (live.leverage <= 1.2)), ("1.21-2.3", (live.leverage > 1.2) & (live.leverage < 2.7)),
             ("2.7-3.0", live.leverage >= 2.7)]
    out += ["### Leverage of the graded moments (dead value left out)", "",
            f"{len(live)} moments. 10th / 25th / 50th / 75th / 90th percentile: {' / '.join(str(x) for x in q)}. Mean {live.leverage.mean():.2f}.", "",
            _md(pd.DataFrame({"leverage": [b for b, _ in bands], "moments": [int(m.sum()) for _, m in bands],
                              "share %": [100.0 * float(m.mean()) for _, m in bands]})), ""]
    stakes = live.stakes.value_counts().sort_index()
    out += ["### Stakes", "", "Multiplier on the graded moments: " + ", ".join(f"x{k:g}: {v}" for k, v in stakes.items()) + ".",
            "Fields the archive really has, per game: " + ", ".join(f"{k}: {v}" for k, v in sorted(r["fields"].items())) + ".", ""]
    rho = win.corr(method="spearman").loc["v1", "v2"]
    both0 = int(((win.v1 == 0) & (win.v2 == 0)).sum())
    out += ["### 3-minute windows, v1 against v2", "",
            f"{len(win)} windows. Spearman rank correlation {rho:.3f}. Windows where both are zero: {both0}. "
            f"v1 > 0 and v2 <= 0: {int(((win.v1 > 0) & (win.v2 <= 0)).sum())}. v1 = 0 and v2 > 0: {int(((win.v1 == 0) & (win.v2 > 0)).sum())}.", ""]
    t1 = plays[plays.v1_tier1]
    cut, what = ROUTINE[fam]
    low = t1[t1.v2 < cut]
    damped = t1[t1.lev <= 0.5]
    out += ["### Where they disagree", "",
            f"v1 tier-1 plays: {len(t1)}. v2 rates {len(low)} of them ({100.0 * len(low) / max(1, len(t1)):.1f}%) below {cut:g} points ({what}).",
            f"v2 leverage is 0.5 or less on {len(damped)} of them ({100.0 * len(damped) / max(1, len(t1)):.1f}%): the garbage-time ones. "
            f"v2 value of a v1 tier-1 play runs from {t1.v2.min():.2f} to {t1.v2.max():.2f} (v1 gives each the same {V.TIER_WEIGHTS[1]:g}).", ""]
    if len(damped):
        ex = damped.sort_values("v2").head(5)
        out += ["Lowest-rated v1 tier-1 plays:", "",
                _md(pd.DataFrame({"v2": ex.v2.to_numpy(float), "v1": ex.v1.to_numpy(float), "night": ex.night, "game": ex.game,
                                  "when": ex.when, "score after (away-home)": ex.score, "play": ex.text})), ""]
    big = plays[plays.v2 >= BIG]
    missed = big[big.v1 == 0]
    out += [f"v2 plays worth {BIG:g}+ points: {len(big)}. v1 has no moment at all on {len(missed)} of them "
            f"({100.0 * len(missed) / max(1, len(big)):.1f}%).", ""]
    if len(missed):
        ex = missed.sort_values("v2", ascending=False).head(8)
        out += [_md(pd.DataFrame({"v2": ex.v2.to_numpy(float), "night": ex.night, "game": ex.game, "when": ex.when,
                                  "score after (away-home)": ex.score, "kinds": ex.v2_kinds, "play": ex.text})), ""]
    if r["top_night"]:
        night = plays[plays.night == r["top_night"]]
        ties = int((night.v1 == night.v1.max()).sum()) if len(night) else 0
        out += [f"### Night {r['top_night']}: top {TOP_N} plays by v2", "", _md(_top(night, "v2")), "",
                f"### Night {r['top_night']}: top {TOP_N} plays by v1", "",
                f"{ties} plays tie at v1's top score of {night.v1.max():g}; ties are listed in time order.", "", _md(_top(night, "v1")), ""]
    return "\n".join(out)


def not_detectable() -> str:
    out = ["## Spec rows not detectable yet", ""]
    for part, rows in value2.config()["not_detectable"].items():
        out += [f"### {part}", "", _md(pd.DataFrame(rows, columns=["spec row", "why not", "data it needs"])), ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", default="data/archive")
    ap.add_argument("--leagues", default="nfl,nba")
    ap.add_argument("--max-nights", type=int, default=12, help="newest dev nights per league")
    ap.add_argument("--top", default="", help="league:night pairs for the top-15 lists, e.g. nfl:20251207,nba:20261028")
    a = ap.parse_args(argv)
    top = dict(x.split(":") for x in a.top.split(",") if x)
    print("# Value v2 against v1 on dev nights\n")
    print("A play's v2 value is the sum of its graded moments (base x leverage x stakes). v1 is the tier weight "
          f"({', '.join(f'tier {k} = {v:g}' for k, v in V.TIER_WEIGHTS.items())}) with published win probability stripped.\n")
    for lg in [x for x in a.leagues.split(",") if x]:
        print(render(league_tables(Path(a.archive), lg, a.max_nights, top.get(lg))))
    print(not_detectable())
    return 0


if __name__ == "__main__":
    sys.exit(main())
