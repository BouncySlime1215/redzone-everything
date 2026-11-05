#!/usr/bin/env python3
"""truth_04 espn_vs_nba_agreement: do ESPN wallclock and NBA timeActual agree per play?

Hypothesis: the same play in the ESPN summary (wallclock, whole seconds) and in NBA liveData
(timeActual, ms) differs by < 1 s at the median and < 2 s at p90, with ESPN ~0.5 s early (it
truncates to the second). If so either feed can be the truth line and a single-feed glitch is
caught by the other; a systematic offset > 1 s would bias every D.

Procedure: for each NBA game, for each ESPN play find NBA actions in the same period with the same
clock (|dc| <= 0.5 s) and the same score after the play; if several, take the one whose description
shares the most words; delta = ESPN wall - NBA wall. Report percentiles, mean bias, match rate, and
per-game medians. Live: refresh both feeds for --game at the end of --minutes. --dry-run: cached
feeds of every recorded game.

Pass rule: match rate >= 60%, median |delta| <= 1.0 s, p90 |delta| <= 2.0 s, |bias| <= 1.0 s.
"""
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_04"


def words(s):
    return set(re.findall(r"[a-z]{3,}", (s or "").lower()))


def match(espn, nba):
    idx = {}
    for x in nba:
        idx.setdefault((x["period"], round(x["clock"])), []).append(x)
    out = []
    for e in espn:
        c = [x for k in (-1, 0, 1) for x in idx.get((e["period"], round(e["clock"]) + k), [])
             if abs(x["clock"] - e["clock"]) <= 0.5]
        sc = [x for x in c if x["score"] == tuple(str(v) for v in e["score"]) or
              (str(x["score"][0]), str(x["score"][1])) == (str(e["score"][0]), str(e["score"][1]))]
        c = sc or c
        if not c:
            continue
        best = max(c, key=lambda x: (len(words(x["desc"]) & words(e["desc"])), -abs(x["wall"] - e["wall"])))
        if not words(best["desc"]) & words(e["desc"]):
            continue
        out.append(dict(clock=e["clock"], period=e["period"], d=e["wall"] - best["wall"], espn=e["desc"][:40],
                        nba=best["desc"][:40]))
    return out


def main():
    a = T.parser(ID, __doc__).parse_args()
    out = T.Out(a.out, ID, a)
    games = [a.game] if a.game else list(T.recorded_games(a.days.split(","), "nba"))
    if a.dry_run:
        print(f"[{ID}] dry-run: would refresh ESPN summary + NBA pbp after {a.minutes} min; matching "
              f"{len(games)} recorded games")
    else:
        if not a.game:
            sys.exit("live mode needs --game")
        time.sleep(a.minutes * 60)
        T.espn_summary("nba", a.game, False, refresh=True)
        nid = T.espn_to_nba(a.game, False)
        if nid:
            T.nba_pbp(nid, False, refresh=True)
    ds, matched, total = [], 0, 0
    for g in games:
        es = T.espn_actions(T.espn_summary("nba", g, True))
        nid, nba, _ = T.truth_for(g, True)
        if not es or not nba:
            out.write("game", game=g, ok=False)
            continue
        m = match(es, nba)
        total += len(es)
        matched += len(m)
        d = [x["d"] for x in m]
        ds += d
        out.write("game", game=g, ok=True, espn_plays=len(es), matched=len(m),
                  median=round(statistics.median(d), 3) if d else None, abs=T.summ([abs(x) for x in d]))
        for x in m[:: max(1, len(m) // 20)]:
            out.write("sample", game=g, **x)
    rate = matched / max(1, total)
    ab = [abs(x) for x in ds]
    passed = None if not ds else (rate >= 0.6 and T.pct(ab, .5) <= 1.0 and T.pct(ab, .9) <= 2.0
                                  and abs(statistics.fmean(ds)) <= 1.0)
    out.verdict(passed, "match>=60%, median|d|<=1.0, p90|d|<=2.0, |bias|<=1.0 (d = ESPN wall - NBA wall)",
                match_rate=round(rate, 3), n=len(ds), abs=T.summ(ab), bias=round(statistics.fmean(ds), 3) if ds else None,
                signed=T.summ(ds))


if __name__ == "__main__":
    main()
