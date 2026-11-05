#!/usr/bin/env python3
"""truth_01 build_cache: fetch + cache the truth feeds and build the truth line per game.

Hypothesis: for every live NBA game the S3 liveData play-by-play gives (period, clock, timeActual)
for >= 95% of actions, the ESPN event id maps to the NBA game id automatically, and the truth line
has running segments in every period played.

Procedure (live): every 15 s for --minutes, re-fetch the NBA pbp (and ESPN summary every 60 s) for
--game (or every live NBA game on the S3 scoreboard), cache it, and log per game: actions, periods,
fraction with a clock+timeActual, running-pair count, latest action wall and how far that is behind
now (the "truth tail"). No stick is touched.
Procedure (--dry-run): offline; for every NBA game in the recorded plays.jsonl, load the cached pbp
(run once with --prefetch-recorded online to fill the cache) and log the same per-game numbers.

Pass rule: >= 90% of games map + load, and in those, >= 95% of actions carry clock+timeActual and
every period has >= 5 running pairs.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_01"


def game_stats(eid, offline):
    nid = eid if str(eid).startswith("00") else T.espn_to_nba(eid, offline)
    if not nid:
        return dict(game=eid, ok=False, why="no nba id")
    pbp = T.nba_pbp(nid, offline, refresh=not offline)
    raw = ((pbp or {}).get("game") or {}).get("actions") or []
    acts = T.nba_actions(pbp)
    if not acts:
        return dict(game=eid, nba=nid, ok=False, why="no pbp")
    prs = T.pairs(acts)
    per = {}
    for p in prs:
        per.setdefault(p["a"]["period"], 0)
        per[p["a"]["period"]] += p["ran"]
    last = max(a["wall"] for a in acts)
    return dict(game=eid, nba=nid, ok=True, actions=len(raw), usable=len(acts),
                usable_frac=round(len(acts) / max(1, len(raw)), 3), periods=sorted(per),
                running_pairs_per_period=per, last_wall=last, tail_s=round(time.time() - last, 1))


def main():
    ap = T.parser(ID, __doc__)
    ap.add_argument("--prefetch-recorded", action="store_true", help="online: cache every recorded game")
    a = ap.parse_args()
    out = T.Out(a.out, ID, a)
    offline = a.dry_run
    if a.prefetch_recorded:
        days = a.days.split(",")
        T.nba_scoreboard(False, refresh=True)
        for eid, lg in T.recorded_games(days).items():
            T.espn_summary(lg, eid, False)
            if lg == "nba":
                nid = T.espn_to_nba(eid, False)
                if nid:
                    T.nba_pbp(nid, False)
            time.sleep(0.5)
    if a.dry_run or a.prefetch_recorded:
        games = [a.game] if a.game else list(T.recorded_games(a.days.split(","), "nba"))
        print(f"[{ID}] dry-run: would poll NBA liveData pbp every 15 s for {a.minutes} min; "
              f"checking {len(games)} recorded games from cache")
        stats = [out.write("game", **game_stats(g, True)) for g in games]
    else:
        stats = []
        end = time.time() + a.minutes * 60
        k = 0
        while time.time() < end:
            if a.game:
                games = [a.game]
            else:
                sb = T.nba_scoreboard(False, refresh=True) or {}
                live = [g for g in (sb.get("scoreboard") or {}).get("games", []) if g.get("gameStatus") == 2]
                games = [g["gameId"] for g in live]     # NBA ids work directly
                for g in live:
                    out.write("live_nba", nba=g["gameId"], code=g["gameCode"], clock=g.get("gameClock"),
                              period=g.get("period"))
            for g in games:
                if k % 4 == 0 and not str(g).startswith("00"):
                    T.espn_summary("nba", g, False, refresh=True)
                s = game_stats(g, False)
                stats.append(out.write("game", **s))
                print(f"  {g} actions={s.get('actions')} tail={s.get('tail_s')} s")
            k += 1
            time.sleep(15)
        stats = [s for s in stats if s.get("kind") == "game"]
    ok = [s for s in stats if s.get("ok")]
    good = [s for s in ok if s["usable_frac"] >= 0.95 and all(v >= 5 for v in s["running_pairs_per_period"].values())]
    passed = (len(ok) >= 0.9 * len(stats) and len(good) == len(ok)) if stats else None
    out.verdict(passed, ">=90% games load; each >=95% actions usable and >=5 running pairs per period",
                games=len(stats), loaded=len(ok), good=len(good),
                failed=[(s.get("game"), s.get("why")) for s in stats if not s.get("ok")][:10])


if __name__ == "__main__":
    main()
