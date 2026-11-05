#!/usr/bin/env python3
"""truth_06 stoppage_walls: real-world wall times of timeouts, period ends/starts and the restarts.

Hypothesis: every timeout / period boundary in the NBA pbp yields (a) the real call time (timeActual
of the timeout / period-end action) and (b) the real RESTART time = wall(B) - (clock at stop - clock
at B) for the first later action B; certified when B came <= 24 s of game clock after the restart
or B sits in a rigid run (B -> next pair ran). Timeouts last
60-240 s real, quarter breaks 120-200 s, halftime 13-20 min. These are the anchors that YTTV
ad-pod and 'break' observations (truth_10) are matched against.

Procedure: TruthLine.stoppages() per game; check restart is after the call and B's following pair
ran (certified); write one 'stop' row per stoppage (wall, restart, dur, certified). Live: rebuild every
20 s for --game during --minutes and emit each stoppage once, when it first becomes certified, with
the certification delay (now - restart). --dry-run: every recorded game.

Pass rule: >= 90% of timeouts have a certified restart, and >= 90% of certified timeout durations
fall in 60-240 s.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_06"


def certified(tl, s):
    """Restart trusted when the clock ran only briefly before the anchor action (<= 24 s: about one
    possession, few chances of an unlogged stop) or the pair after the anchor ran (rigid run)."""
    if s["restart"] is None or s["restart"] < s["wall"]:
        return False
    return s["run_s"] is not None and (s["run_s"] <= 24 or s["b_ran"])


def main():
    a = T.parser(ID, __doc__).parse_args()
    out = T.Out(a.out, ID, a)
    rows = []
    if a.dry_run:
        games = [a.game] if a.game else list(T.recorded_games(a.days.split(","), "nba"))
        print(f"[{ID}] dry-run: would rebuild stoppages every 20 s for {a.minutes} min; {len(games)} recorded games")
        for g in games:
            nid, acts, tl = T.truth_for(g, True)
            if not tl:
                continue
            for s in tl.stoppages():
                s.update(game=g, certified=certified(tl, s))
                rows.append(s)
                out.write("stop", **s)
    else:
        if not a.game:
            sys.exit("live mode needs --game")
        done = set()
        t0 = time.time()
        while time.time() - t0 < a.minutes * 60:
            nid = T.espn_to_nba(a.game, False)
            acts = T.nba_actions(T.nba_pbp(nid, False, refresh=True)) if nid else []
            tl = T.TruthLine(acts) if acts else None
            for s in (tl.stoppages() if tl else []):
                if s["n"] in done or not certified(tl, s):
                    continue
                done.add(s["n"])
                s.update(game=a.game, certified=True, cert_delay=round(time.time() - (s["restart"] or 0), 1))
                rows.append(s)
                out.write("stop", **s)
                print(f"  {s['type']} {s['sub']} P{s['period']} {s['clock']:.0f}s call {T.fmt(s['wall'])} "
                      f"restart {T.fmt(s['restart'])} dur {s['dur']:.0f}s")
            time.sleep(20)
    tos = [r for r in rows if r["type"] == "timeout"]
    cert = [r for r in tos if r["certified"] and r["dur"] is not None]
    inrange = [r for r in cert if 60 <= r["dur"] <= 240]
    pe = [r for r in rows if r["type"] == "period" and r["sub"] == "end" and r["dur"]]
    f1 = len(cert) / max(1, len(tos))
    f2 = len(inrange) / max(1, len(cert))
    passed = None if not tos else (f1 >= 0.9 and f2 >= 0.9)
    out.verdict(passed, ">=90% timeouts certified restart; >=90% durations 60-240 s",
                timeouts=len(tos), certified_frac=round(f1, 3), dur_in_range_frac=round(f2, 3),
                timeout_dur=T.summ([r["dur"] for r in cert]),
                period_break_dur=T.summ([r["dur"] for r in pe]),
                out_of_range=[(r["game"], r["period"], r["clock"], round(r["dur"])) for r in cert if r not in inrange][:8])


if __name__ == "__main__":
    main()
