"""[show scorecard] Info-only product scorecard per league: what the SIMULATED show let a viewer see, by moment tier.

Replays the two-stick show model (scripts/buffered_jump_sim.py run_maxrate, no TV, no adb, no engine) over the dev
nights of an archive, then asks of every moment redzone/moments detects (play-by-play, tiers.yaml): was it on screen?

  python scripts/show_scorecard.py                          # nfl and nba, dev nights only (--split fit)
  python scripts/show_scorecard.py --leagues nba --games 4 --max-slates 5 --json out.json
  --nights 20261101,20261102   only those nights; exit 2 if any is locked or fresh

Columns: must-show seen (tiers.must_show kinds; wider than tier 1 for football), setup seen (tier 2), highlight seen
(tier 3), cuts per 10 min against the planner band (docs/agents/planner/2026-11-09-product-per-league.md section 4),
and the sim's own Rule #1 counters (mid-play joins and leaves, commercial seconds per 10 min). A moment is "seen" when
its play time sits inside a clip the model actually aired (landing to leave). A play the model could not show clean
(a hurry-up) is never a clip, so it counts as not seen: the table does not flatter the show.

This is a REPLAY OF THE SIMULATED SHOW, not the real TV. Seek, prep and feed lag are model constants (the --prep and
--seek flags), the archive has no snap times, and nothing here has touched a stick.

It is NOT a release gate. Scorecards and the release gate are frozen (docs/grader_freeze.json) and grader changes are
Nick's: this file imports none of the frozen files and prints a table, no pass or fail, exit 0 on a table.
Dev only: nights are filtered by scripts/coverage_audit.DevOnly BY FILE NAME; a locked night's file is never opened.
`--split` takes fit; test and all are refused (exit 2).

Why: the build plan's P1 "product gate per league" must stay info-only until Nick approves a grader change, and WP-20
needs a number for "did the show show the must-see moments" that does not edit a frozen grader.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import random
import statistics
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if ROOT.as_posix() not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from scripts import buffered_jump_sim as bjs  # noqa: E402
from scripts.coverage_audit import DevOnly, LockedNightError  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.moments import tiers  # noqa: E402
from redzone.moments.detect import detect as detect_moments  # noqa: E402

LEAGUES = ("nfl", "cfb", "nba", "cbb")        # the leagues the sim can replay (football and basketball)
# Planner cuts-per-10-minutes bands by games live: (max games, low, high). Section 4 of the per-league doc.
BANDS = {
    "football": ((3, 2, 4), (7, 6, 10), (99, 10, 14)),
    "hoops": ((3, 2, 4), (7, 4, 6), (99, 5, 8)),
}


def band(league: str, games: float) -> tuple[int, int] | None:
    """(low, high) planner band for this many live games; None for one game (the plan says stay) or no list."""
    try:
        fam = tiers.family(league)
    except KeyError:
        return None
    if games < 2 or fam not in BANDS:
        return None
    for top, lo, hi in BANDS[fam]:
        if games <= top:
            return lo, hi
    return None


@contextlib.contextmanager
def capture_items():
    """Record the clips the sim builds (it keeps them local). Restores bjs.mr_items on exit. {gid: [MRItem]}"""
    got: dict[str, list] = {}
    real = bjs.mr_items

    def spy(gid, plays, lg, p, rng, lag):
        items = real(gid, plays, lg, p, rng, lag)
        got[gid] = items
        return items
    bjs.mr_items = spy
    try:
        yield got
    finally:
        bjs.mr_items = real


def window(plays: dict) -> tuple[float, float]:
    """The sim's own scoring window (run_maxrate): after the second kickoff + 2 min, until the second-last game ends."""
    first = sorted(q[0][0] for q in plays.values())
    last = sorted(q[-1][0] for q in plays.values())
    return max(first[:2]) + 120, last[-2]


def seen_counts(league: str, moments, items, t0: float, t_end: float) -> dict:
    """Moments of the games the sim used, inside its window, and how many sat inside an aired clip.
    Keys: must/setup/highlight -> [seen, total]; by_kind -> {kind: [seen, total]}. Pure."""
    clips: dict[str, list[tuple[float, float]]] = {}
    for gid, its in items.items():
        clips[gid] = sorted((it.land, it.leave) for it in its if it.shown)
    out = {"must": [0, 0], "setup": [0, 0], "highlight": [0, 0], "by_kind": {}}
    for m in moments:
        if m.game_id not in items or not (t0 <= m.t <= t_end):
            continue
        hit = any(a <= m.t <= b for a, b in clips.get(m.game_id, ()))
        groups = []
        if tiers.must_show(league, m.kind):
            groups.append("must")
        if m.tier == tiers.TIER_SETUP:
            groups.append("setup")
        if m.tier == tiers.TIER_HIGHLIGHT:
            groups.append("highlight")
        for g in groups:
            out[g][0] += hit
            out[g][1] += 1
        k = out["by_kind"].setdefault(m.kind, [0, 0])
        k[0] += hit
        k[1] += 1
    return out


def merge(a: dict, b: dict) -> dict:
    for g in ("must", "setup", "highlight"):
        a[g][0] += b[g][0]
        a[g][1] += b[g][1]
    for kind, (s, n) in b["by_kind"].items():
        k = a["by_kind"].setdefault(kind, [0, 0])
        k[0] += s
        k[1] += n
    return a


def empty() -> dict:
    return {"must": [0, 0], "setup": [0, 0], "highlight": [0, 0], "by_kind": {}}


def pct(pair: list[int]) -> float | None:
    return None if not pair[1] else round(100.0 * pair[0] / pair[1], 1)


def score_league(league: str, root: Path, p: bjs.MR, seed: int, min_games: int, max_slates: int,
                 only_nights: list[str] | None = None) -> dict:
    """One league's row over its dev slates. Raises LockedNightError for a locked/fresh night or an unknown split."""
    lg = League(league)
    dev = DevOnly(root, league)
    if only_nights:
        dev.require(only_nights)
    ids = dev.game_ids()
    if only_nights:
        ids = {g: n for g, n in ids.items() if n in set(only_nights)}
    league_dir = (root / league).as_posix()
    group = bjs.maxrate_slates(league_dir, lg, min_games, 0, set(ids))
    group = group[:max_slates] if max_slates else group
    seen, rows = empty(), []
    for n, (_day, games) in enumerate(group):
        with capture_items() as items:
            r = bjs.run_maxrate(games, p, random.Random(seed + n), lg)
        if not r:
            continue
        t0, t_end = window(dict(games))
        moments = []
        for gid in items:
            f = league_dir + f"/summary_{gid}.json"
            moments += detect_moments(league, json.load(open(f)), gid).moments
        merge(seen, seen_counts(league, moments, items, t0, t_end))
        rows.append(r)
    if not rows:
        return {"league": league, "slates": 0}
    mins = sum(r["minutes"] for r in rows)
    jumps = sum(r["jumps"] for r in rows)
    games = p.games or statistics.median(r["games_used"] for r in rows)   # the held set, else the slate
    return {
        "league": league, "slates": len(rows), "minutes": round(mins), "games_live": games,
        "must_seen_pct": pct(seen["must"]), "must_n": seen["must"][1],
        "setup_seen_pct": pct(seen["setup"]), "setup_n": seen["setup"][1],
        "highlight_seen_pct": pct(seen["highlight"]), "highlight_n": seen["highlight"][1],
        "cuts_per_10": round(jumps / mins * 10, 1), "band": band(league, games),
        "mid_play_joins": sum(r["joins_mid_play"] for r in rows), "mid_play_leaves": sum(r["leaves_mid_play"] for r in rows),
        "ad_s_per_10": round(sum(r["ad_s"] for r in rows) / mins * 10, 2),
        "by_kind": {k: pct(v) for k, v in sorted(seen["by_kind"].items())},
    }


def render(rows: list[dict]) -> str:
    out = ["REPLAY OF THE SIMULATED SHOW on dev nights, not the real TV. Info only, not a release gate.",
           f"{'league':<6}{'slates':>7}{'games':>6}{'must%':>8}{'(n)':>6}{'setup%':>8}{'(n)':>6}{'hilite%':>8}{'(n)':>6}"
           f"{'cuts/10':>9}{'band':>8}{'mid-join':>9}{'mid-leave':>10}{'ad s/10':>8}"]
    f = lambda v: "-" if v is None else f"{v:.1f}"  # noqa: E731
    for r in rows:
        if not r["slates"]:
            out.append(f"{r['league']:<6}  no slates in the dev nights")
            continue
        b = "-" if not r["band"] else f"{r['band'][0]}-{r['band'][1]}"
        out.append(f"{r['league']:<6}{r['slates']:>7}{r['games_live']:>6.0f}{f(r['must_seen_pct']):>8}{r['must_n']:>6}"
                   f"{f(r['setup_seen_pct']):>8}{r['setup_n']:>6}{f(r['highlight_seen_pct']):>8}{r['highlight_n']:>6}"
                   f"{r['cuts_per_10']:>9.1f}{b:>8}{r['mid_play_joins']:>9}{r['mid_play_leaves']:>10}{r['ad_s_per_10']:>8.1f}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", type=Path, default=ROOT / "data" / "archive")
    ap.add_argument("--leagues", default="nfl,nba", help=f"comma list of {','.join(LEAGUES)}")
    ap.add_argument("--split", choices=("fit", "test", "all"), default="fit",
                    help="fit = dev nights. test and all would read the locked nights and are refused")
    ap.add_argument("--nights", default="", help="comma list of YYYYMMDD; exit 2 if any is locked or fresh")
    ap.add_argument("--games", type=int, default=4, help="stay on this many games at a time (0 = every game on)")
    ap.add_argument("--sticks", type=int, default=2)
    ap.add_argument("--seek", type=float, default=8.0, help="model constant: same-game seek, s")
    ap.add_argument("--prep", type=float, default=15.0, help="model constant: another game on a stick, s")
    ap.add_argument("--min-games", type=int, default=3)
    ap.add_argument("--max-slates", type=int, default=0, help="0 = every dev slate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.split != "fit":
        print(f"REFUSED: --split {a.split} would read the locked nights; this scorecard reads dev nights only", file=sys.stderr)
        return 2
    leagues = [x for x in a.leagues.split(",") if x]
    bad = [x for x in leagues if x not in LEAGUES]
    if bad:
        print(f"unknown league {bad}: pick from {LEAGUES}", file=sys.stderr)
        return 2
    only = [n for n in a.nights.split(",") if n] or None
    p = replace(bjs.MR(), games=a.games, sticks=a.sticks, seek_s=a.seek, prep_s=a.prep)
    rows = []
    for league in leagues:
        try:
            rows.append(score_league(league, a.archive, p, a.seed, a.min_games, a.max_slates, only))
        except LockedNightError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
    print(render(rows))
    if a.json:
        a.json.write_text(json.dumps({"replay_of_simulated_show": True, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
