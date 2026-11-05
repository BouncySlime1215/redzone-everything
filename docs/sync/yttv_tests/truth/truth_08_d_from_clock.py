#!/usr/bin/env python3
"""truth_08 d_from_clock: THE tool - YTTV observation (stick time + on-screen game clock) -> D +- err.

Hypothesis: given a frame time on the stick and the game clock it showed (from any source: another
test family's UI/scorebug read, a burst of screencaps of a non-secure overlay, a Prime cross-check),
D = frame_wall - truth_wall(period, clock) is recovered to <= 1.0 s for >= 90% of readings while the
clock runs; frozen-clock readings come back as an interval (kind=frozen_interval), never a fake point.

Usage (live):
  --period 2 --clock-text 5:23 --obs-wall 1793900000.12          one reading, Mac epoch
  --period 2 --clock-text 5:23 --obs-uptime 206356.441 --stick HDMI2   stick uptime s -> epoch via a
                                                                   read-only /proc/uptime bracket
  --obs-file obs.jsonl   rows {game, period, clock (text or s), obs_wall | obs_uptime+host, channel,
                         edge?}: resolves each row, re-trying for --minutes until the feed has caught up
                         (truth lands ~10-30 s after the real moment, truth_05).
  --edge   the reading is the FIRST frame showing that value (true clock == value exactly), else the
           display is assumed ceil-rounded (true clock in (c-1, c], centre c-0.5, +-0.29 s).
Output rows: {D, err, truth_kind, real_wall, channel, host}. Read-only; never touches a stick except
'cat /proc/uptime' for --obs-uptime.
--dry-run: synthetic recovery on every recorded game: 300 instants inside running segments, display
ceil(true clock) (whole s >= 60, tenths below), observation = real wall + D_true (29 s) + frame jitter
U(0, 0.5 s); half the readings flagged --edge at a tick. Score |D_hat - 29| and bar coverage.

Pass rule (dry-run synthetic): |D_hat - D_true| <= 1.0 s for >= 90% of point readings and coverage
(|err| <= 2 x bar) >= 85%. Live: N/A (reports D, the fleet decides with truth_11).
"""
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_08"


def parse_clock(v):
    return float(v) if isinstance(v, (int, float)) else T.clock_text(str(v))


def resolve(row, lines, offline, boots):
    g = row.get("game")
    if g not in lines or lines[g] is None:
        lines[g] = T.truth_for(g, offline)[2]
    tl = lines[g]
    if tl is None:
        return None
    ow = row.get("obs_wall")
    if ow is None and row.get("obs_uptime") is not None:
        h = T.resolve_stick(row.get("host") or "HDMI1")
        if h not in boots:
            boots[h] = T.stick_boot_epoch(h)[0]
        ow = boots[h] + float(row["obs_uptime"])
    c = parse_clock(row["clock"])
    if ow is None or c is None:
        return None
    return T.estimate_d(tl, int(row["period"]), c, ow, obs_sigma=float(row.get("obs_sigma", 0.25)),
                        display="exact" if row.get("edge") else "ceil")


def synth(out, games, n=300, d_true=29.0, seed=7):
    rng = random.Random(seed)
    errs, cov, kinds = [], [], {}
    for g in games:
        nid, acts, tl = T.truth_for(g, True)
        if not tl:
            continue
        runs = [p for p in T.pairs(acts) if p["ran"] and p["dc"] >= 3]
        for _ in range(max(1, n // max(1, len(games)))):
            p = rng.choice(runs)
            true_c = rng.uniform(p["b"]["clock"], p["a"]["clock"])
            real = p["b"]["wall"] - (true_c - p["b"]["clock"])
            edge = rng.random() < 0.5
            if true_c >= 60:
                shown = float(math.ceil(true_c)) if not edge else float(math.floor(true_c))
                if edge:   # first frame at the tick: true clock == shown
                    real = p["b"]["wall"] - (shown - p["b"]["clock"])
            else:
                shown = math.ceil(true_c * 10) / 10 if not edge else math.floor(true_c * 10) / 10
                if edge:
                    real = p["b"]["wall"] - (shown - p["b"]["clock"])
            obs = real + d_true + rng.uniform(0, 0.5)
            r = T.estimate_d(tl, p["a"]["period"], shown, obs, obs_sigma=0.15,
                             display="exact" if edge else "ceil")
            if not r:
                continue
            kinds[r["truth_kind"]] = kinds.get(r["truth_kind"], 0) + 1
            if r["truth_kind"] == "frozen_interval":
                continue
            e = r["D"] - d_true - 0.25          # remove the mean of the synthetic frame jitter
            errs.append(abs(e))
            cov.append(abs(e) <= 2 * r["err"])
            out.write("synthetic", game=g, period=p["a"]["period"], shown=shown, edge=edge, **r, D_true=d_true)
    return errs, cov, kinds


def main():
    ap = T.parser(ID, __doc__)
    ap.add_argument("--period", type=int)
    ap.add_argument("--clock-text")
    ap.add_argument("--obs-wall", type=float)
    ap.add_argument("--obs-uptime", type=float)
    ap.add_argument("--obs-file")
    ap.add_argument("--edge", action="store_true")
    a = ap.parse_args()
    out = T.Out(a.out, ID, a)
    if a.dry_run:
        games = [a.game] if a.game else list(T.recorded_games(a.days.split(","), "nba"))
        print(f"[{ID}] dry-run: synthetic D recovery (D_true=29 s) on {len(games)} recorded games")
        errs, cov, kinds = synth(out, games)
        f1 = sum(e <= 1.0 for e in errs) / max(1, len(errs))
        cv = sum(cov) / max(1, len(cov))
        out.verdict(None if not errs else (f1 >= 0.9 and cv >= 0.85),
                    "synthetic: |D_hat-D_true|<=1.0 s for >=90% and coverage>=0.85",
                    n=len(errs), within_1s=round(f1, 3), abs_err=T.summ(errs), coverage=round(cv, 3), kinds=kinds)
        return
    lines, boots = {}, {}
    if a.obs_file:
        done = set()
        t0 = time.time()
        while True:
            rows = T.load_jsonl(a.obs_file)
            for i, row in enumerate(rows):
                if i in done:
                    continue
                if not row.get("game") and a.game:
                    row["game"] = a.game
                lines.pop(row.get("game"), None)      # refresh: the feed may have caught up
                if row.get("game"):
                    nid = T.espn_to_nba(row["game"], False)
                    if nid:
                        T.nba_pbp(nid, False, refresh=True)
                r = resolve(row, lines, True, boots)
                if r:
                    done.add(i)
                    rec = out.write("d", **row, **r)
                    print(json.dumps({k: rec[k] for k in ("D", "err", "truth_kind")}), row.get("channel"))
            if len(done) == len(rows) and rows or time.time() - t0 > a.minutes * 60:
                break
            time.sleep(10)
        out.verdict(None, "live: reports D rows; judged by truth_11", resolved=len(done), rows=len(rows))
        return
    if a.game is None or a.period is None or a.clock_text is None:
        sys.exit("need --game --period --clock-text and --obs-wall/--obs-uptime (or --obs-file)")
    nid = T.espn_to_nba(a.game, False)
    if nid:
        T.nba_pbp(nid, False, refresh=True)
    row = dict(game=a.game, period=a.period, clock=a.clock_text, obs_wall=a.obs_wall, obs_uptime=a.obs_uptime,
               host=a.stick, channel=a.channel, edge=a.edge)
    r = resolve(row, lines, True, boots)
    out.write("d", **row, **(r or {"D": None}))
    print(json.dumps(r))
    out.verdict(None, "live single reading", result=r)


if __name__ == "__main__":
    main()
