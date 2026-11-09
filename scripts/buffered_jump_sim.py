"""[buffered jump] Jumps per minute, plays shown and Rule #1 on recorded NFL slates (data/archive, ESPN wallclocks).

Two sticks, both scrubbed behind live (screen delay = edge + behind), the stick we leave keeps playing (HDMI-CEC
off). The editor (redzone/decider/buffered.py cut_now) is called every `step` seconds on what the play-by-play
holds at that moment (each game's entries arrive feed_lag after their wallclock); the hidden stick re-tunes per
rotate_due (unready prep_s). Truth: the same entries, all of them, at the TRUE screen delay (belief + a per-tune
error of err_sd). Counts a mid-play JOIN when the true picture at the landing is inside a play (snap - 10 s ..
whistle + 3 s), a mid-play LEAVE when a play is on screen during the flip.

  python scripts/buffered_jump_sim.py                      # every Sunday slate in data/archive/nfl
  python scripts/buffered_jump_sim.py --sigma 4 --lag 30   # stated sigma (4 s floor), slower feed
  python scripts/buffered_jump_sim.py --json out.json

Numbers only; no play text leaves this script.
Why: Tests the buffered-jump pass bar (jumps per minute, Rule #1) on recorded slates (docstring).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redzone.decider import buffered as bj  # noqa: E402
from redzone.decider.cuts import AFTER_PLAY_S, PLAY_S, DeadTracker, _is_action, walk_params  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources.espn import _kind, _ts, flatten_plays  # noqa: E402

EDGE_S = 34.9
LG = League.NFL   # --league
# [Nick 22:17 Nov 7 pass bar] break windows that cut to commercials, and how long after the whistle the ads start /
# how long before the restart they end (an assumption: the network stays on the field ~10 s each side)
AD_KINDS = {"tv_timeout", "media_timeout", "timeout", "timeout_long", "two_minute", "end_period", "halftime",
            "intermission", "review"}
AD_EDGE_S = 10.0
PASS_JUMPS, PASS_WINDOW_S = 75, 600.0


def load_game(path: str, lg: League | None = None, d: dict | None = None):
    lg = lg or LG
    d = json.load(open(path)) if d is None else d
    out = []
    for p in flatten_plays(lg, d):
        t = _ts(p.get("wallclock"))
        if t is None or not p.get("id"):
            continue
        out.append((t, str(p["id"]), _kind(lg, p), p.get("text") or "", (p.get("type") or {}).get("text") or "",
                    ((p.get("period") or {}).get("number"))))
    out.sort()
    return out


def slates(archive: str, min_games: int):
    """Groups of games that overlap in time (a Sunday window), by scoreboard date."""
    by_day = defaultdict(list)
    for f in glob.glob(os.path.join(archive, "summary_*.json")):
        try:
            g = load_game(f)
        except (OSError, ValueError):
            continue
        if len(g) < 60:
            continue
        gid = os.path.basename(f)[8:-5]
        by_day[int(g[0][0] // 86400)].append((gid, g))
    for day, games in sorted(by_day.items()):
        games.sort(key=lambda z: z[1][0][0])
        # one slate = games starting within 30 min of each other
        cur = []
        for gid, g in games:
            if cur and g[0][0] - cur[0][1][0][0] > 1800:
                if len(cur) >= min_games:
                    yield day, cur
                cur = []
            cur.append((gid, g))
        if len(cur) >= min_games:
            yield day, cur


def truth_tracker(plays, lg: League | None = None) -> DeadTracker:
    dt = DeadTracker(lg or LG)
    for t, pid, kind, text, tt, per in plays:
        dt.add(pid, t, t, kind, text, tt, None, per)
    dt.seen(1e12, 0.0)
    return dt


def play_spans(plays, lg: League | None = None) -> list[tuple[float, float]]:
    lg = lg or LG
    dt = truth_tracker(plays, lg)
    if lg.base == League.NBA:
        # basketball: the ball is live between dead windows, so a "play" is each live stretch (whistle to whistle)
        out, t = [], plays[0][0]
        for w in dt.windows():
            if w.start > t:
                out.append((t, w.start))
            t = max(t, w.end if w.end is not None else plays[-1][0])
        return out
    out = []
    for m in dt._order():
        if _is_action(lg, m):
            out.append((m.lo - PLAY_S[lg], m.hi + AFTER_PLAY_S))
    return out


def in_play(spans, a: float, b: float) -> bool:
    return any(s < b and a < e for s, e in spans)


def ad_windows(plays, lg: League | None = None) -> list[tuple[float, float]]:
    """Real-time spans that are commercials on TV: a break window's inside, AD_EDGE_S in from each end."""
    out = []
    for w in truth_tracker(plays, lg).windows():
        if w.kind in AD_KINDS and w.end is not None and w.end - w.start > 2 * AD_EDGE_S:
            out.append((w.start + AD_EDGE_S, w.end - AD_EDGE_S))
    return out


def pass_bar(segs, spans, ads, cut_at, mid_at, t0: float, t_end: float) -> dict:
    """[Nick 22:15/22:17 Nov 7] Each whole 10-minute window of the slate: PASS = at least 75 on-screen jumps AND 0
    commercial seconds on screen AND every play shown in full (none cut off at a cut) AND 0 mid-play joins/leaves."""
    cut_plays = []   # wall time of each cut that cut a play off (at its entry or at its exit)
    for gid, a, b, mu in segs:
        x0, x1 = a - mu, b - mu
        for s, e in spans[gid]:
            if s < x1 and x0 < e:
                if s < x0 and a > t0:
                    cut_plays.append(a)
                if e > x1 and b < t_end:
                    cut_plays.append(b)
    wins, w0 = [], t0
    while w0 + PASS_WINDOW_S <= t_end:
        w1 = w0 + PASS_WINDOW_S
        ad_s = 0.0
        for gid, a, b, mu in segs:
            a, b = max(a, w0), min(b, w1)
            if b > a:
                ad_s += sum(max(0.0, min(b - mu, e) - max(a - mu, s)) for s, e in ads[gid])
        jumps = sum(1 for t in cut_at if w0 <= t < w1)
        partial = sum(1 for t in cut_plays if w0 <= t < w1)
        mid = sum(1 for t in mid_at if w0 <= t < w1)
        ok = jumps >= PASS_JUMPS and ad_s == 0 and partial == 0 and mid == 0
        wins.append({"jumps": jumps, "ad_s": round(ad_s, 1), "partial_plays": partial, "mid_play": mid, "pass": ok})
        w0 = w1
    return {"windows": len(wins), "windows_pass": sum(w["pass"] for w in wins),
            "best_window_jumps": max((w["jumps"] for w in wins), default=0),
            "ad_s": round(sum(w["ad_s"] for w in wins), 1), "ad_free_windows": sum(w["ad_s"] == 0 for w in wins),
            "partial_plays": len(cut_plays), "window_rows": wins}


def run_slate(games, args, rng: random.Random, cfg) -> dict:
    lag = {gid: max(5.0, rng.gauss(args.lag, args.lag_sd)) for gid, _ in games}
    plays = dict(games)
    spans = {gid: play_spans(p) for gid, p in plays.items()}
    belief = {gid: DeadTracker(LG) for gid in plays}
    cursor = {gid: 0 for gid in plays}
    first = {gid: p[0][0] for gid, p in plays.items()}
    last = {gid: p[-1][0] for gid, p in plays.items()}
    t0 = max(sorted(first.values())[:2]) + 120
    t_end = sorted(last.values())[-2]   # while at least two games are on
    if t_end - t0 < 1800:
        return {}

    def depth() -> float:
        k = max(1, round((args.behind - 28.5) / 16.8))
        return 16.8 * k + 28.5

    def new_stick(gid, at_wall):
        b = depth()
        return {"gid": gid, "mu": EDGE_S + b, "err": rng.gauss(0.0, args.err_sd), "ready_at": at_wall, "paused_at": None}

    order = sorted(plays, key=lambda g: first[g])
    vis, hid = new_stick(order[0], t0), new_stick(order[1], t0)
    pair_since, landed_at, left_at = t0, t0, defaultdict(lambda: t0 - 300)
    cuts, joins_mid, leaves_mid, rot, snap_cuts, parks, expired = 0, 0, 0, 0, 0, 0, 0
    reasons = defaultdict(int)
    segs = []   # (gid, wall0, wall1, mu_true)
    cut_at, mid_at = [], []   # wall times of each cut / each mid-play join or leave
    seg0 = t0
    flip = float(bj.cfg_get(cfg, "flip_s"))
    walk = walk_params(cfg, LG)   # basketball's walk up court after a shot, like the live decider's Pic
    t = t0
    while t < t_end:
        for gid, p in plays.items():   # the play-by-play as it stands at t
            i = cursor[gid]
            while i < len(p) and p[i][0] + lag[gid] <= t:
                w, pid, kind, text, tt, per = p[i]
                belief[gid].add(pid, w, w, kind, text, tt, None, per)
                i += 1
            cursor[gid] = i
            belief[gid].seen(t, lag[gid])
        V = bj.Pic(belief[vis["gid"]], vis["mu"], args.sigma, vis["gid"], walk)
        H = bj.Pic(belief[hid["gid"]], hid["mu"], args.sigma, hid["gid"], walk) if t >= hid["ready_at"] else None
        park = bool(bj.cfg_get(cfg, "park"))
        if H is not None and park:
            if hid["paused_at"] is None:
                pin = bj.park_in(H, t, cfg)
                if pin is not None and pin <= args.step:
                    hid["paused_at"] = t + max(0.0, pin)
                    parks += 1
            elif t - hid["paused_at"] > float(bj.cfg_get(cfg, "park_max_age_s")):
                hid["mu"] += t - hid["paused_at"]   # rolled on the hidden stick: re-parks at its next snap
                hid["paused_at"] = None
                expired += 1
        if H is not None and (not park or (hid["paused_at"] is not None and hid["paused_at"] <= t)):
            mu_v = vis["mu"] + vis["err"]
            seen = sum(1 for s, e in spans[vis["gid"]] if s >= landed_at - mu_v and e <= t - mu_v)
            if park:
                c = bj.cut_parked(V, bj.frozen(H, t, hid["paused_at"]), t, cfg, plays_seen=seen)
            else:
                c = bj.cut_now(V, H, t, cfg, plays_seen=seen)
            reasons[c.why] += 1
            if c.go:
                f = rng.uniform(float(bj.cfg_get(cfg, "flip_fast_s")), flip)
                if hid["paused_at"] is not None:   # PLAY at the cut: the pause is now part of its delay
                    hid["mu"] += t - hid["paused_at"]
                    hid["paused_at"] = None
                mu_h = hid["mu"] + hid["err"]
                if in_play(spans[vis["gid"]], t - mu_v, t + f - mu_v):
                    leaves_mid += 1
                    mid_at.append(t)
                if in_play(spans[hid["gid"]], t + f - mu_h, t + f + 0.5 - mu_h):
                    joins_mid += 1
                    mid_at.append(t)
                cut_at.append(t)
                segs.append((vis["gid"], seg0, t + f, mu_v))
                seg0 = t + f
                left_at[vis["gid"]] = t
                vis, hid = hid, vis
                landed_at = t + f
                cuts += 1
                snap_cuts += c.why in ("snap_next", "parked_snap")
                t += f
                continue
        why = bj.rotate_due(H, t, t - pair_since, cfg) if t >= hid["ready_at"] and hid["paused_at"] is None else None
        if why:
            # the rotation's next game: live now, not on a stick, longest away
            prep = float(bj.cfg_get(cfg, "prep_s"))
            pool = [g for g in plays if g not in (vis["gid"], hid["gid"])
                    and first[g] <= t - EDGE_S - depth() and last[g] > t + 600
                    and bj.next_snap_in(bj.Pic(belief[g], EDGE_S + depth(), args.sigma), t + prep)
                    < float(bj.cfg_get(cfg, "rotate_dead_s"))]   # round-robin, skipping a game in a break
            if pool:
                g = max(pool, key=lambda z: t - left_at[z])
                hid = new_stick(g, t + float(bj.cfg_get(cfg, "prep_s")))
                pair_since = hid["ready_at"]
                rot += 1
            else:
                pair_since = t
        t += args.step
    segs.append((vis["gid"], seg0, t_end, vis["mu"] + vis["err"]))
    shown = 0
    for gid, a, b, mu in segs:
        shown += sum(1 for s, e in spans[gid] if s >= a - mu and e <= b - mu)
    bar = pass_bar(segs, spans, {g: ad_windows(p) for g, p in plays.items()}, cut_at, mid_at, t0, t_end)
    total = sum(1 for g in plays for s, e in spans[g] if t0 - 95 <= s and e <= t_end - 95)
    mins = (t_end - t0) / 60.0
    return {"games": len(plays), "minutes": round(mins, 1), "cuts": cuts, "cuts_per_min": round(cuts / mins, 2),
            "snap_cuts": snap_cuts, "plays_shown": shown, "plays_shown_per_min": round(shown / mins, 2),
            "plays_all_games_per_min": round(total / mins, 2), "joins_mid_play": joins_mid,
            "leaves_mid_play": leaves_mid, "rotations": rot, "parks": parks, "parks_expired": expired, "holds": dict(reasons),
            **bar}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--league", default="nfl", help="nfl or nba (the archive defaults to data/archive/<league>)")
    ap.add_argument("--archive", default=None)
    ap.add_argument("--min-games", type=int, default=3)
    ap.add_argument("--max-slates", type=int, default=0)
    ap.add_argument("--behind", type=float, default=75.0)
    ap.add_argument("--sigma", type=float, default=4.0, help="stated delay sigma used for margins (s)")
    ap.add_argument("--err-sd", type=float, default=1.5, help="true per-tune delay error sd (s)")
    ap.add_argument("--lag", type=float, default=20.0, help="feed lag mean (s)")
    ap.add_argument("--lag-sd", type=float, default=6.0)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--set", action="append", default=[], help="buffered_jump.KEY=VALUE")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    global LG
    LG = League(a.league)
    a.archive = a.archive or f"data/archive/{a.league}"
    cfg = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        cfg[f"buffered_jump.{k}"] = float(v)
    rng = random.Random(a.seed)
    rows = []
    for n, (day, games) in enumerate(slates(a.archive, a.min_games)):
        if a.max_slates and n >= a.max_slates:
            break
        r = run_slate(games, a, rng, cfg)
        if r:
            r["slate"] = n
            rows.append(r)
            print(f"slate {n:3d}: {r['games']:2d} games {r['minutes']:6.1f} min  cuts/min {r['cuts_per_min']:.2f}  "
                  f"plays shown/min {r['plays_shown_per_min']:.2f} (all games {r['plays_all_games_per_min']:.2f})  "
                  f"mid-play joins {r['joins_mid_play']} leaves {r['leaves_mid_play']}  rotations {r['rotations']}  "
                  f"| 10-min PASS {r['windows_pass']}/{r['windows']} (best {r['best_window_jumps']} jumps, ads "
                  f"{r['ad_s']:.0f} s, cut-off plays {r['partial_plays']})")
    if not rows:
        print("no slates")
        return 1
    mins = sum(r["minutes"] for r in rows)
    summ = {"slates": len(rows), "minutes": round(mins),
            "cuts_per_min": round(sum(r["cuts"] for r in rows) / mins, 2),
            "cuts_per_min_p10_p90": [round(x, 2) for x in statistics.quantiles([r["cuts_per_min"] for r in rows], n=10)[::8]]
            if len(rows) >= 2 else None,
            "snap_cuts_per_min": round(sum(r["snap_cuts"] for r in rows) / mins, 2),
            "plays_shown_per_min": round(sum(r["plays_shown"] for r in rows) / mins, 2),
            "single_game_plays_per_min": round(sum(r["plays_all_games_per_min"] * r["minutes"] / r["games"] for r in rows) / mins, 2),
            "joins_mid_play": sum(r["joins_mid_play"] for r in rows),
            "leaves_mid_play": sum(r["leaves_mid_play"] for r in rows),
            "cuts": sum(r["cuts"] for r in rows),
            "pass_bar": {"rule": f">= {PASS_JUMPS} jumps per 10 min, 0 ad seconds, 0 cut-off plays, 0 mid-play joins/leaves",
                         "windows": sum(r["windows"] for r in rows), "windows_pass": sum(r["windows_pass"] for r in rows),
                         "best_window_jumps": max(r["best_window_jumps"] for r in rows),
                         "median_window_jumps": statistics.median([w["jumps"] for r in rows for w in r["window_rows"]] or [0]),
                         "ad_free_windows": sum(r["ad_free_windows"] for r in rows),
                         "ad_s_per_10min": round(sum(r["ad_s"] for r in rows) / max(1, sum(r["windows"] for r in rows)), 1),
                         "cut_off_plays": sum(r["partial_plays"] for r in rows)},
            "args": {k: v for k, v in vars(a).items() if k != "json"}}
    print(json.dumps(summ, indent=1))
    if a.json:
        json.dump({"summary": summ, "slates": rows}, open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
