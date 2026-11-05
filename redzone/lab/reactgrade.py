"""[EXP-301] Grader for the delay-and-react test (Nick, Nov 5 12:25 AM and 12:28 AM ET).

Four arms run offline on the same past nights (lane 2 builds and runs them, lane 3 grades; the grader
never sees an arm's code):
  A  the predict-live director as it runs today
  B  pure react: cut only after the feed reports a big play, as a catch-up replay
  C  hybrid: predict-live where the situation announces leverage, react-replay for surprises
  D  C plus the hidden stick running some seconds behind live, so a surprise shows from its start

The arm writes one row per (arm, night, league) with `night_row`; `report` turns the rows into the table.

Per league and per arm:
  big_live_pct       big plays watched whole at the SCREEN's edge (the North Star's "big live": the arm's
                     time-shift buffer included; a rewound visit is not live, it counts in time-to-see)
  time-to-see        per big play, the seconds from the end of the play on the BROADCAST's live edge (no
                     time-shift buffer) to the end of the viewer seeing all of it: the arm's buffer for a
                     play watched at the screen's edge, plus o for a visit o s behind that, or (clip end -
                     live end) for a catch-up replay; never if not seen. Measured from the broadcast so an
                     arm held 90 s behind (shipped NBA) and one 20 s behind (D) are on one ruler. Median, p90, and the share seen live,
                     within 30 s, 90 s and 180 s (Nick's ratchet: 3 min now, then 90 s, 30 s, live)
  hard zeros         mid-play entries; mid-play leaves at high leverage; honesty faults (below)
  guardrails         dead air %, switches per hour, crunch live %, your-team crunch live, spoiler
                     (share of screen time behind live, p95 seconds behind)
  oracle             strict big live as a share of the live-only oracle (`oracle.solve(.., "big_live")`);
                     an arm that replays can see more than that ceiling within a window, never more live

Honesty faults (any one zeroes the arm for that league):
  replay_unheld      a replay of a play no stick had tuned when it aired and not on YouTube TV (cloud DVR)
  replay_early       a replay that starts before the play's wall clock + the arm's feed lag (it knew too soon)
  rewind_unbuffered  a visit running o s behind live on a game no stick had held for o s (no buffer to rewind)

Verdict for each arm against A, per league, on paired cluster-bootstrap CIs over nights (the same
resampled nights for every arm): see `verdict`. PENDING below REACT_MIN_NIGHTS nights or MIN_BIG big plays.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np

from redzone.grading import PLAY_LEN, is_big, lag_of
from redzone.lab import oracle as _oracle
from redzone.lab import sportsgrade
from redzone.lab.backtest import offsets_of, on_screen

BASE_ARM = "A"
RATCHET = (180.0, 90.0, 30.0, 0.0)   # seconds: the catch-up bar now, then each tightening (Nick 12:28 AM)
LIVE_EPS = 1.0                       # seconds behind live that still count as live (seek rounding)
STEP = 10.0                          # seconds between samples of the screen for the spoiler line
REACT_MIN_NIGHTS = 8
MIN_BIG = 30
DRAWS = 2000
BOOT_SEED = 301
CRUNCH_PTS = 5.0       # crunch live % may not be down by more than this (bootstrap lower bound)
DEAD_PTS = 2.0         # dead air % may not be up by more than this (bootstrap upper bound)
SWITCH_X = 1.5         # switches per hour at most this times A
SPOILER_P95 = 180.0    # seconds behind live, p95 of screen time
UNSEEN = float("inf")


def _intervals(holds, t1: float) -> dict[str, list[tuple[float, float]]]:
    """holds [(t, stick, gid)] -> gid -> [(from, to)] while some stick had it tuned."""
    by_stick: dict[str, list[tuple[float, str]]] = {}
    for t, stick, gid in holds:
        by_stick.setdefault(str(stick), []).append((float(t), gid))
    out: dict[str, list[tuple[float, float]]] = {}
    for seq in by_stick.values():
        seq.sort(key=lambda x: x[0])
        for i, (t, gid) in enumerate(seq):
            end = seq[i + 1][0] if i + 1 < len(seq) else t1
            if gid:
                out.setdefault(gid, []).append((t, end))
    return out


def _held(iv, gid: str, a: float, b: float) -> bool:
    return any(s <= a and b <= e for s, e in iv.get(gid, ()))


def _completed(windows, watching) -> dict[tuple[str, int], tuple[float, float]]:
    """Replay windows that ran to the end without another tune: (gid, event) -> (start, end)."""
    out: dict[tuple[str, int], tuple[float, float]] = {}
    for gid, event, start, end in windows:
        if not any(start < at < end for at, _ in watching):
            out.setdefault((gid, int(round(event))), (float(start), float(end)))
    return out


def _visits(switches, offs, t1: float):
    """(at, gid, offset, until) for each non-replay visit."""
    out = []
    for i, s in enumerate(switches):
        nxt = switches[i + 1].t if i + 1 < len(switches) else math.inf
        if s.kind != "replay":
            out.append((s.t, s.to, offs[i], nxt))
    return out


def time_to_see(start: float, end: float, gid: str, key, visits, done, tune_s: float, t1: float) -> float:
    """Seconds from the live-edge end of a play to the viewer having seen all of it (UNSEEN if not)."""
    best = UNSEEN
    for at, g, o, until in visits:
        if g == gid and at + tune_s <= start + o and end + o < until and end + o <= t1:
            best = min(best, max(0.0, o))
    if key in done:
        s, e = done[key]
        best = min(best, max(0.0, e - end))
    return best


def oracle_big_live(replays, my_teams, lag_total, tune_s: float, t0: float, t1: float) -> int:
    """The live-only ceiling on big plays watched whole (shared by every arm on the night)."""
    n = _oracle.Night(replays, set(my_teams), lag_total, tune_s, t0, t1)
    return _oracle.grade(n, _oracle.solve(n, "big_live")).big_live


def night_row(arm: str, night: str, split: str, replays, switches, my_teams, lag_total, tune_s: float,
              t0: float, t1: float, windows, feed_lag_s: float, holds, dvr_games, oracle_big: int | None = None,
              buffer_s: float = 0.0, cond: str = "") -> dict:
    """One (arm, night, league) row. windows: [(gid, event_wall, start, end)] replay clips the arm played;
    holds: [(t, stick, gid)] each tune of each stick; dvr_games: gids on YouTube TV (cloud DVR);
    buffer_s: the time-shift buffer the arm held every stream at (timing.behind_live_s; 0 if off), already
    inside lag_total, so the broadcast's own edge is lag_total - buffer_s;
    cond: the run condition (e.g. "real-lag", "feed-15"): conditions are reported apart, never pooled.
    Run seeds of one night are separate rows; `report` merges them into that night's cluster."""
    league = replays[0].league
    watching = [(s.t, f"replay:{s.to}" if s.kind == "replay" else s.to) for s in switches]
    done = _completed(windows, watching)
    card = sportsgrade.grade(replays, switches, set(my_teams), lag_total, tune_s, t0, t1,
                             {k: e for k, (_, e) in done.items()})
    offs = offsets_of(switches)
    visits = _visits(switches, offs, t1)
    iv = _intervals(holds, t1)
    dvr = set(dvr_games)

    faults = {"replay_unheld": 0, "replay_early": 0, "rewind_unbuffered": 0}
    wall_of = {(r.gid, int(round(w))): (r, w) for r in replays for w in r.wall}
    for (gid, ev), (s, _) in done.items():
        r, w = wall_of.get((gid, ev), (None, float(ev)))
        lag = lag_of(lag_total, gid)
        air0 = w + lag - (PLAY_LEN[league] if r else 0.0)
        if gid not in dvr and not _held(iv, gid, air0, w + lag):
            faults["replay_unheld"] += 1
        if s < w + feed_lag_s:
            faults["replay_early"] += 1
    for at, g, o, _ in visits:
        if o > LIVE_EPS and g not in dvr and not _held(iv, g, at - o, at):
            faults["rewind_unbuffered"] += 1

    tts, live = [], 0
    for r in replays:
        lag = lag_of(lag_total, r.gid)
        prev = None
        for w, p in zip(r.wall, r.plays):
            if is_big(league, p, prev):
                end = w + lag
                x = time_to_see(end - PLAY_LEN[league], end, r.gid, (r.gid, int(round(w))), visits, done, tune_s, t1)
                tts.append(x + buffer_s)
                live += x <= LIVE_EPS and any(
                    g == r.gid and o <= LIVE_EPS and at + tune_s <= end - PLAY_LEN[league] and end < u
                    for at, g, o, u in visits)
            prev = p

    behind = []
    clips = sorted(done.items(), key=lambda kv: kv[1][0])
    t = t0
    while t <= t1:
        g, o = on_screen(watching, offs, t, tune_s)
        if g and g.startswith("replay:"):
            clip = next(((k, se) for k, se in clips if se[0] <= t < se[1]), None)
            behind.append(t - (clip[0][1] + lag_of(lag_total, clip[0][0]) - buffer_s) if clip else buffer_s)
        elif g:
            behind.append(buffer_s + max(0.0, o))
        t += STEP

    cs = card.cut.summary()
    return {"arm": arm, "night": night, "split": split, "league": league.value, "cond": cond,
            "big": card.big, "big_live": live, "buffer_s": buffer_s, "tts": [None if math.isinf(x) else round(x, 1) for x in tts],
            "crunch": card.crunch, "crunch_live": card.crunch_live,
            "my_crunch": card.my_crunch, "my_crunch_live": card.my_crunch_live,
            "entries_mid_play": cs["entries_mid_play"], "mid_play_cuts_high": len(card.cuts_high),
            "dead_s": round(card.dead, 1), "watched_s": round(card.watched, 1),
            "switches": card.switches, "replays": card.replays, "hours": round(card.hours, 3),
            "behind": [round(b, 1) for b in behind], "faults": faults,
            "oracle_big_live": oracle_big}


def _tts(rows) -> np.ndarray:
    return np.array([UNSEEN if x is None else x for r in rows for x in r["tts"]], float)


def _q(a: np.ndarray, q: float) -> float:
    return float(np.quantile(a, q, method="inverted_cdf")) if len(a) else UNSEEN


def metrics(rows: list[dict], window: float = RATCHET[0]) -> dict:
    """Pooled metrics of one arm's rows (one league)."""
    def s(k):
        return sum(r[k] for r in rows)

    big, tts = s("big"), _tts(rows)
    beh = np.array([b for r in rows for b in r["behind"]], float)
    ob = [r["oracle_big_live"] for r in rows]
    out = {"nights": len({r["night"] for r in rows}), "big": big,
           "big_live_pct": 100 * s("big_live") / max(1, big),
           "tts_median": _q(tts, 0.5), "tts_p90": _q(tts, 0.9),
           "seen_window_pct": 100 * float((tts <= window).sum()) / max(1, big),
           "entries_mid_play": s("entries_mid_play"), "mid_play_cuts_high": s("mid_play_cuts_high"),
           "faults": sum(sum(r["faults"].values()) for r in rows),
           "dead_air_pct": 100 * s("dead_s") / max(1, s("watched_s")),
           "switches_per_hour": s("switches") / max(0.1, s("hours")),
           "crunch_live_pct": 100 * s("crunch_live") / max(1, s("crunch")),
           "your_team_crunch_live": f"{s('my_crunch_live')}/{s('my_crunch')}",
           "behind_share_pct": 100 * float((beh > LIVE_EPS).sum()) / max(1, len(beh)),
           "behind_p95": _q(beh, 0.95) if len(beh) else 0.0,
           "oracle_pct": (100 * s("big_live") / max(1, sum(ob))) if all(x is not None for x in ob) else None}
    for w in RATCHET:
        out[f"seen_le_{int(w)}_pct"] = 100 * float((tts <= max(w, LIVE_EPS)).sum()) / max(1, big)
    return out


def _paired(by_arm: dict[str, dict[str, list[dict]]], nights: list[str]) -> dict[str, list[dict]]:
    return {a: [x for n in nights for x in rows[n]] for a, rows in by_arm.items()}


def bootstrap(by_arm: dict[str, dict[str, list[dict]]], window: float, draws: int = DRAWS, seed: int = BOOT_SEED) -> dict:
    """Paired cluster bootstrap over nights: (arm, metric) -> (lo, hi) of arm - A and of the arm itself."""
    nights = sorted(set.intersection(*(set(v) for v in by_arm.values())))
    rng = random.Random(seed)
    keys = ("seen_window_pct", "crunch_live_pct", "dead_air_pct", "big_live_pct")
    diffs: dict[tuple[str, str], list[float]] = {}
    for _ in range(draws):
        pick = [rng.choice(nights) for _ in nights]
        m = {a: metrics(rs, window) for a, rs in _paired(by_arm, pick).items()}
        for a in m:
            for k in keys:
                diffs.setdefault((a, k), []).append(m[a][k] - m[BASE_ARM][k])
    return {k: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))) for k, v in diffs.items()}


def verdict(arm: str, m: dict, base: dict, ci: dict) -> str:
    """WIN / LOSE / NO_WIN / FAIL (a hard zero broken) / PENDING for one arm vs A in one league."""
    if m["nights"] < REACT_MIN_NIGHTS or m["big"] < MIN_BIG:
        return "PENDING"
    if m["faults"] or m["entries_mid_play"] or m["mid_play_cuts_high"]:
        return "FAIL"
    lo, hi = ci[(arm, "seen_window_pct")]
    guard = (ci[(arm, "crunch_live_pct")][0] >= -CRUNCH_PTS and ci[(arm, "dead_air_pct")][1] <= DEAD_PTS
             and m["switches_per_hour"] <= SWITCH_X * base["switches_per_hour"] and m["behind_p95"] <= SPOILER_P95)
    if lo > 0 and guard:
        return "WIN"
    if hi < 0:
        return "LOSE"
    return "NO_WIN"


def report(rows: list[dict], split: str, window: float = RATCHET[0]) -> dict:
    """"league|cond" -> {arms: {arm: metrics + verdict}, winner}. Only nights every arm ran are compared;
    a night is one bootstrap cluster whatever the number of seeds run on it."""
    out: dict = {}
    for key in sorted({f"{r['league']}|{r.get('cond', '')}" for r in rows if r["split"] == split}):
        lg = key
        by_arm: dict[str, dict[str, list[dict]]] = {}
        for r in rows:
            if r["split"] == split and f"{r['league']}|{r.get('cond', '')}" == key:
                by_arm.setdefault(r["arm"], {}).setdefault(r["night"], []).append(r)
        if BASE_ARM not in by_arm:
            out[lg] = {"error": "arm A missing"}
            continue
        nights = sorted(set.intersection(*(set(v) for v in by_arm.values())))
        paired = _paired(by_arm, nights)
        ms = {a: metrics(rs, window) for a, rs in paired.items()}
        ci = bootstrap(by_arm, window) if len(nights) >= 2 else {}
        arms, winner, best = {}, BASE_ARM, ms[BASE_ARM]["seen_window_pct"]
        for a, m in ms.items():
            v = "BASE_ARM" if a == BASE_ARM else (verdict(a, m, ms[BASE_ARM], ci) if ci else "PENDING")
            arms[a] = {**m, "verdict": v, **({"ci_vs_A": {k: ci[(a, k)] for (x, k) in ci if x == a}} if a != BASE_ARM and ci else {})}
            if v == "WIN" and m["seen_window_pct"] > best:
                winner, best = a, m["seen_window_pct"]
        out[lg] = {"nights": len(nights), "window_s": window, "arms": arms, "winner": winner}
    return out


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def markdown(rep: dict) -> str:
    def f(x):
        if x is None or (isinstance(x, float) and math.isinf(x)):
            return "never"
        return f"{x:.1f}" if isinstance(x, float) else str(x)

    cols = ("big_live_pct", "tts_median", "tts_p90", "seen_le_0_pct", "seen_le_30_pct", "seen_le_90_pct",
            "seen_le_180_pct", "entries_mid_play", "mid_play_cuts_high", "faults", "dead_air_pct",
            "switches_per_hour", "crunch_live_pct", "your_team_crunch_live", "behind_share_pct", "behind_p95",
            "oracle_pct", "verdict")
    lines = []
    for lg, x in rep.items():
        if "arms" not in x:
            lines.append(f"### {lg}: {x.get('error')}")
            continue
        lines += [f"### {lg} ({x['nights']} nights, window {int(x['window_s'])} s, winner {x['winner']})", "",
                  "| arm | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for a in sorted(x["arms"]):
            lines.append(f"| {a} | " + " | ".join(f(x["arms"][a].get(c)) for c in cols) + " |")
        lines.append("")
    return "\n".join(lines)
