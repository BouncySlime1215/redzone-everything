"""[timing, Nov 9] How well do we know where a stick is? Replay the recorded nights offline, app by app.

    python scripts/timing_replay.py --data /path/to/redzone/data [--json out.json]

Why: the join-after-a-bucket rule needs the stick's position known to about 2 s. This measures what each app really
achieves from the app's own logs, and grades the delay estimators on whole nights the estimators never saw.

What it reads (read-only, nothing raw is ever printed: only counts and seconds, per app and night):
- Prime truth: score-bug clock reads (scorebug_reads.jsonl, livelab/*/ocrclock.jsonl) against ESPN's public play clocks
  (archive/nba/summary_<game>.json): the frame's request time minus the wall time the feed shows at that game clock.
  Only reads where the bug's clock really ran between two neighbouring frames count (a frozen clock timing a replay
  graphic is not truth). The anchors' own scatter is a floor on every number below: what's reported is an upper bound.
- Meter: position reads per tune (sync_truth/edge/<night>.jsonl), how E = wall - position behaves with age.
- YouTube TV has no independent absolute truth in the logs (say so; do not invent one).

Estimators replayed through the real filter (redzone/decider/sync.py), in time order, one prediction before each
anchor:  today / robust / meter (robust + position reads) / clock_fit / midpoint frame times. Errors are per whole
night: the K prior (arena - frame clock) is fitted on the other night only; the rest is forward-only within the night.
"""
from __future__ import annotations

import argparse
import calendar
import json
import statistics as st
import sys
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.decider.sync import ChannelSync, fit_clock_window, fit_delay  # noqa: E402

PERIODS = {"1ST": 1, "2ND": 2, "3RD": 3, "4TH": 4, "OT": 5}
CLOCK_SD = 1.5   # the filter's sd for a single clock sample (sync.SIGMA["clock"])
TOL_S = 1.5      # a feed bracket counts when its wall gap matches its clock gap this close


def clock_of(s):
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if ":" in s:
        m, x = s.split(":")
        return int(m) * 60 + float(x)
    return float(s)


def _wall(s: str) -> int:
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


def feed_clocks(data: Path, game: str) -> dict[int, list[tuple[float, float]]]:
    """period -> [(wall, clock s)] from ESPN's public summary, oldest first."""
    out: dict[int, set] = defaultdict(set)
    path = data / "archive" / "nba" / f"summary_{game}.json"
    if not path.exists():
        return {}
    for p in json.load(path.open()).get("plays") or []:
        if not p.get("wallclock") or not (p.get("clock") or {}).get("displayValue"):
            continue
        try:
            out[p["period"]["number"]].add((_wall(p["wallclock"]), clock_of(p["clock"]["displayValue"])))
        except (KeyError, ValueError):
            continue
    return {k: sorted(v) for k, v in out.items()}


def bracket(c: float, per: int | None, plays: dict, t: float, tol: float = TOL_S) -> float | None:
    """delay = t - (wall at which the feed's clock read c), only inside a bracket where the clock ran."""
    for p in ([per] if per else list(plays)):
        for (w0, c0), (w1, c1) in zip(plays.get(p, ()), plays.get(p, ())[1:]):
            if c0 <= c1 or not c1 <= c <= c0 or abs((w1 - w0) - (c0 - c1)) > tol:
                continue
            d = t - (w0 + (c0 - c))
            if 2.0 <= d <= 180.0:
                return d
    return None


def load_reads(data: Path) -> list[dict]:
    """Every bug read with a clock, both nights, as (night, game, t0, t1, clock, per, E or None)."""
    reads = []
    f = data / "scorebug_reads.jsonl"
    if f.exists():
        for r in map(json.loads, f.open()):
            c = clock_of(r.get("clock"))
            if c is not None:
                reads.append({"night": "2026-11-07", "game": r["game"], "t0": r["t0"], "t1": r["t1"], "clock": c,
                              "per": PERIODS.get((r.get("period") or "").strip().upper()), "E": None})
    f = data / "livelab" / "2026-11-04" / "ocrclock.jsonl"
    if f.exists():
        for r in map(json.loads, f.open()):
            c = clock_of(r.get("clock"))
            if c is not None:
                reads.append({"night": "2026-11-04", "game": "401909923", "t0": r["t_cap"] - r["cap_s"] / 2.0,
                              "t1": r["t_cap"] + r["cap_s"] / 2.0, "clock": c,
                              "per": PERIODS.get((r.get("period") or "").strip().upper()),
                              "E": float(r["prime_behind_wall_s"])})
    return sorted(reads, key=lambda r: r["t0"])


def running(reads: list[dict], i: int) -> bool:
    """A neighbouring read (same game, within 15 s) shows the bug's clock advanced by the wall gap: it really ran."""
    r = reads[i]
    for j in (i - 1, i + 1):
        if 0 <= j < len(reads) and reads[j]["game"] == r["game"] and abs(reads[j]["t0"] - r["t0"]) <= 15.0:
            dt, dc = reads[j]["t0"] - r["t0"], r["clock"] - reads[j]["clock"]
            if abs(dt) >= 2.0 and abs(dc - dt) <= max(1.2, 0.15 * abs(dt)):
                return True
    return False


def anchors(data: Path, reads: list[dict]) -> list[dict]:
    """The truth set: running-clock reads bracketed by the feed. z is against the frame's request time (see capture_slope)."""
    plays = {g: feed_clocks(data, g) for g in {r["game"] for r in reads}}
    out = []
    for i, r in enumerate(reads):
        z = bracket(r["clock"], r["per"], plays[r["game"]], r["t0"]) if running(reads, i) else None
        if z is not None:
            out.append({**r, "z": z, "cap": r["t1"] - r["t0"], "idx": i})
    return out


def raw_anchors(data: Path, reads: list[dict]) -> list[dict]:
    """The reads the live code really sends the filter: scorebug._clock's own test (the clock advanced by the wall gap
    since the previous frame, within 2.5 s), then a feed bracket. truth=True for the reads also running on a neighbour."""
    plays = {g: feed_clocks(data, g) for g in {r["game"] for r in reads}}
    out = []
    for i, r in enumerate(reads):
        prev = reads[i - 1] if i and reads[i - 1]["game"] == r["game"] else None
        if prev is None or abs((prev["clock"] - r["clock"]) - (r["t0"] - prev["t0"])) > 2.5 or prev["clock"] <= r["clock"]:
            continue
        z = bracket(r["clock"], r["per"], plays[r["game"]], r["t0"])
        if z is not None:
            out.append({**r, "z": z, "cap": r["t1"] - r["t0"], "truth": running(reads, i)})
    return out


def capture_slope(anc: list[dict]) -> dict:
    """z (request-time convention) regressed on the capture's duration: slope -0.5 would mean the frame is really taken
    midway through the capture (the midpoint flag's claim), 0 means right at the request (today's convention)."""
    out = {}
    for night in sorted({a["night"] for a in anc}):
        rows = [a for a in anc if a["night"] == night]
        med = st.median(a["z"] for a in rows)
        rows = [a for a in rows if abs(a["z"] - med) < 3.0]
        if len(rows) < 8:
            continue
        xs, ys = [a["cap"] for a in rows], [a["z"] for a in rows]
        mx, my = st.fmean(xs), st.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        res = [y - (my + b * (x - mx)) for x, y in zip(xs, ys)]
        se = (sum(r * r for r in res) / (len(rows) - 2) / den) ** 0.5
        out[night] = {"slope": round(b, 3), "se": round(se, 3), "n": len(rows), "z_sd": round(st.pstdev(ys), 3)}
    return out


def edge_series(data: Path, app: str) -> dict[str, list[dict]]:
    """target -> time-ordered 'playing' edge rows of this app."""
    by: dict[str, list[dict]] = defaultdict(list)
    for f in sorted((data / "sync_truth" / "edge").glob("*.jsonl")):
        for r in map(json.loads, f.open()):
            if r.get("app") == app and r.get("E_s") is not None and (r.get("ms") or {}).get("state") == "playing":
                by[r["target"]].append(r)
    return {k: sorted(v, key=lambda r: r["t"]) for k, v in by.items()}


def meter_stats(data: Path, app: str) -> dict:
    """How flat E is inside one tune, by age, and uncommanded E steps, per stick role. A step over 300 s is the position
    changing form (epoch vs a counter), not the picture moving. Numbers only."""
    out = {}
    series = edge_series(data, app)
    for role in ("hidden", "visible"):
        by_age: dict[int, list[float]] = defaultdict(list)
        steps, hours, stalls, tunes = 0, 0.0, 0, 0
        for rows in series.values():
            by_tune = defaultdict(list)
            for r in rows:
                if r.get("role") == role:
                    by_tune[r.get("tune_t")].append(r)
            for rs in by_tune.values():
                rs = [r for r in rs if r.get("played_s", 0) >= 3.0]
                if len(rs) < 5:
                    continue
                tunes += 1
                base = st.median(r["E_s"] for r in rs[:5])
                for r0, r1 in zip(rs, rs[1:]):
                    de, dt = r1["E_s"] - r0["E_s"], r1["t"] - r0["t"]
                    moved = abs(r1.get("pred_behind_s", 0) - r0.get("pred_behind_s", 0))
                    if 0.5 < abs(de) <= 300.0 and moved < 0.5:
                        steps += 1
                        stalls += 1 if de > 0 else 0
                    hours += dt / 3600.0
                for r in rs:
                    x = r["E_s"] - base
                    if abs(x) <= 300.0 and abs(r.get("pred_behind_s", 0) - rs[0].get("pred_behind_s", 0)) < 0.5:
                        by_age[min(int((r["t"] - rs[0]["t"]) // 300) * 300, 1500)].append(x)
        age = {}
        for a, v in sorted(by_age.items()):
            v2 = sorted(abs(x) for x in v)
            age[a] = {"n": len(v2), "p50": round(v2[len(v2) // 2], 3), "p90": round(v2[int(len(v2) * 0.9)], 3),
                      "p99": round(v2[min(len(v2) - 1, int(len(v2) * 0.99))], 2)}
        out[role] = {"tunes": tunes, "hours": round(hours, 2), "steps_per_h": round(steps / hours, 2) if hours else None,
                     "stall_steps": stalls, "age_s": age}
    return out


def k_forms(data: Path) -> dict:
    """Prime: how often the position is the frame's wall clock (epoch) vs a counter (relative meter only)."""
    series = edge_series(data, "prime")
    n = Counter()
    for rows in series.values():
        for r in rows:
            n["epoch" if r.get("epoch") else "counter"] += 1
    return dict(n)


def yttv_landing(data: Path) -> dict:
    """YouTube TV: how repeatable a fresh tune's landing is on its channel (edge rows' landing_off_s)."""
    off = []
    for f in sorted((data / "sync_truth" / "edge").glob("*.jsonl")):
        for r in map(json.loads, f.open()):
            if r.get("app") == "youtubetv" and r.get("landing_off_s") is not None and abs(r["landing_off_s"]) < 30:
                off.append(float(r["landing_off_s"]))
    v = sorted(abs(x) for x in off)
    if not v:
        return {"n": 0}
    return {"n": len(v), "mean": round(st.fmean(off), 3), "sd": round(st.pstdev(off), 3), "p50_abs": round(v[len(v) // 2], 3),
            "p90_abs": round(v[int(len(v) * 0.9)], 3)}


def burst_anchor_spread(data: Path) -> dict:
    """The burst anchors' z: not a delay (a burst onset follows a feed dead ball by an unknown spell). Spread only."""
    by = defaultdict(list)
    for f in sorted((data / "recordings").glob("*/sync_anchors.jsonl")):
        for r in map(json.loads, f.open()):
            if r.get("z") is not None and str(r.get("target", "")).startswith("youtubetv"):
                by[f.parent.name].append(float(r["z"]))
    return {n: {"n": len(v), "p10": round(sorted(v)[max(0, int(len(v) * .1))], 1), "p50": round(st.median(v), 1),
                "p90": round(sorted(v)[int(len(v) * .9)], 1)} for n, v in sorted(by.items())}


# ---------------------------------------------------------------------- estimators (one prediction before each anchor)
def filter_prediction(game_anc: list[dict], robust: bool, midpoint: bool, meter: list[tuple[float, float]] | None,
                      prior_mu: float = 40.0, prior_sd: float = 15.0) -> list[dict]:
    """Replay anchors (and position reads) through ChannelSync in time order. Returns one row per anchor with the error
    of the prediction made just before it."""
    f = ChannelSync("prime:replay", game_anc[0]["t0"] - 5.0, robust=robust)
    f.P[2][2] = 1e8
    f.reset(game_anc[0]["t0"] - 5.0, prior_mu, prior_sd, new_stream=True)
    seq: list[tuple[float, str, object]] = [(a["t0"], "a", a) for a in game_anc]
    if meter:
        seq += [(t, "m", p) for t, p in meter]
    seq.sort(key=lambda x: x[0])
    out = []
    seen = 0
    for t, kind, x in seq:
        if kind == "m":
            f.position(t, x, 0.3)
            continue
        a = x
        tt = (a["t0"] + a["t1"]) / 2.0 if midpoint else a["t0"]
        z = a["z"] + (tt - a["t0"])  # the same bracket, timed against the frame's assumed time
        mu, sd = f.mu(tt), f.sd(tt)
        out.append({"err": z - mu, "stated": sd, "n_prior": seen, "t": t, "night": a["night"], "game": a["game"]})
        f.delay(tt, z, "clock", CLOCK_SD)
        seen += 1
    return out


def raw_prediction(raw: list[dict], robust: bool) -> list[dict]:
    """The filter fed every bracketed read (misreads and frozen clocks included), graded only on the truth reads."""
    f = ChannelSync("prime:replay", raw[0]["t0"] - 5.0, robust=robust)
    f.P[2][2] = 1e8
    f.reset(raw[0]["t0"] - 5.0, 40.0, 15.0, new_stream=True)
    out, seen = [], 0
    for a in raw:
        mu, sd = f.mu(a["t0"]), f.sd(a["t0"])
        if a["truth"]:
            out.append({"err": a["z"] - mu, "stated": sd, "n_prior": seen, "t": a["t0"], "night": a["night"],
                        "game": a["game"]})
        f.delay(a["t0"], a["z"], "clock", CLOCK_SD)
        seen += 1
    return out


def clock_fit_prediction(reads: list[dict], game_anc: list[dict], plays: dict, robust: bool = True) -> list[dict]:
    """[clock fit] Windows over the bug's frames (the running stretches), into the filter, graded on the same anchors."""
    f = ChannelSync("prime:replay", game_anc[0]["t0"] - 5.0, robust=robust)
    f.P[2][2] = 1e8
    f.reset(game_anc[0]["t0"] - 5.0, 40.0, 15.0, new_stream=True)
    samples = []
    by_per: dict[int, list[dict]] = defaultdict(list)
    for r in reads:
        if r["per"] is not None:
            by_per[r["per"]].append(r)
    for per, rs in by_per.items():
        start = 0
        while start < len(rs):
            pts = [(r["t0"], r["clock"]) for r in rs[start:] if r["t0"] - rs[start]["t0"] <= 30.0]
            fit = fit_clock_window(pts)
            if fit is None:
                start += 1
                continue
            d = fit_delay(fit, plays.get(per, []))
            if d is not None:
                samples.append((fit.wall_ref, d, fit.sd))
            start += max(1, len(pts))
    seq = sorted([(a["t0"], "a", a) for a in game_anc] + [(t, "s", (d, sd)) for t, d, sd in samples], key=lambda x: x[0])
    out, seen = [], 0
    for t, kind, x in seq:
        if kind == "a":
            mu, sd = f.mu(t), f.sd(t)
            out.append({"err": x["z"] - mu, "stated": sd, "n_prior": seen, "t": t, "night": x["night"], "game": x["game"]})
            continue
        f.delay(t, x[0], "clock_fit", x[1])
        seen += 1
    return out


def k_prediction(game_anc: list[dict], meter_at, k_prior: tuple[float, float] | None) -> list[dict]:
    """E + K: K fused from this game's earlier anchors (inverse variance over the pair sd), E read just before the anchor.
    k_prior (mean, sd): what the other nights say about K, only until this game has its first anchor."""
    out = []
    ks: list[tuple[float, float]] = []
    for a in game_anc:
        e = meter_at(a)
        if e is None:
            continue
        if ks:
            w = [1 / s ** 2 for _, s in ks]
            kh = sum(wi * k for wi, (k, _) in zip(w, ks)) / sum(w)
            sd = sum(w) ** -0.5
            out.append({"err": a["z"] - (e + kh), "stated": sd, "n_prior": len(ks), "t": a["t0"], "night": a["night"],
                        "game": a["game"]})
        elif k_prior is not None:
            out.append({"err": a["z"] - (e + k_prior[0]), "stated": k_prior[1], "n_prior": 0, "t": a["t0"],
                        "night": a["night"], "game": a["game"]})
        ks.append((a["z"] - e, 0.5))
    return out


def summarize(rows: list[dict], min_prior: int = 1, max_prior: int | None = None) -> dict:
    rs = [r for r in rows if r["n_prior"] >= min_prior and (max_prior is None or r["n_prior"] <= max_prior)]
    if not rs:
        return {"n": 0}
    es = [r["err"] for r in rs]
    ab = sorted(abs(e) for e in es)
    rmse = (sum(e * e for e in es) / len(es)) ** 0.5
    return {"n": len(es), "mean": round(st.fmean(es), 2), "rmse": round(rmse, 2), "p50_abs": round(ab[len(ab) // 2], 2),
            "p90_abs": round(ab[min(len(ab) - 1, int(len(ab) * 0.9))], 2),
            "stated_mean": round(st.fmean(r["stated"] for r in rs), 2)}


def by_night(rows: list[dict], min_prior: int = 1, max_prior: int | None = None) -> dict:
    out = {n: summarize([r for r in rows if r["night"] == n], min_prior, max_prior) for n in sorted({r["night"] for r in rows})}
    rmses = [v["rmse"] for v in out.values() if v.get("n")]
    out["_mean_rmse"] = round(st.fmean(rmses), 2) if rmses else None
    out["_worst_rmse"] = round(max(rmses), 2) if rmses else None
    return out


# ---------------------------------------------------------------------- what the numbers earn, and what they can't
TV_LIST = """Hidden-stick measurements that close the gap. Each runs through scripts/tv_test_runner.py as its own Spec
(docs/agents/patches/timing.patch adds them; the runner's Stick.measure returns the numbers).

1. Prime capture offset (T2-cap, 3 min). Which moment of the screencap does the frame really come from? 30 alternating
   raw-band and PNG captures of a running clock on the hidden Prime stick: offset (PNG time convention) - (band time).
   Closes the only unmeasured Prime term (the logs only bound it: slope -0.05 +-0.17 of the capture length).
2. Prime K per game (T2-k, 12 min). 20 bug reads per game across 3 games, two sticks: K stable within a game to 0.5 s?
   K across games (seen 9.3 and 23.2 s) and across HDMI inputs. Settles how soon a game earns its doubt.
3. Prime epoch form (T2-pos, 5 min). Which content gives the wall-clock position and which the counter, and does the
   meter survive a pause and a seek in each. Settles when E is usable.
4. YouTube TV live edge (T2-edge, 15 min). Nick's label probe (tv_test_runner 3b) on 5 channels, 3 rounds, an hour
   apart, both sticks. Is D_edge one number (34.9 s), and how stable? Settles the app's stated doubt.
Not on the hidden stick alone: a truth for YouTube TV's absolute delay against the game itself (DRM blanks its frames,
no bug OCR). That needs a timed reference on the picture Nick sees (the tap, or a Prime overlay of the same game)."""

CANT_KNOW = """What the recorded data cannot tell (no TV):
- Prime: which moment of the 5 s screencap the frame comes from, a fixed offset every number here shares (bounded, not
  measured). K on a game with no anchor: it differs per game, so nothing predicts it.
- YouTube TV: its absolute delay. Its screen is DRM-blanked, so no bug OCR and no absolute anchors; the only absolute
  figures are a 28-read label probe (34.9 +-0.4 s) and old show-flip samples (20-26 s) about 10 s away from it. Nothing
  in the logs grades either against a game clock.
- Any stick's error on a tune with a stall right before a join: the meter sees it, one tick late."""


def per_app_table(r: dict) -> list[list]:
    """Per-app position error, honestly: what was measured, on how much, what it would state."""
    est = r["estimators"]["meter"]["after_1_anchor"]
    nights = [n for n in r["nights"] if est.get(n, {}).get("n")]
    a = [r["estimators"]["today"]["after_1_anchor"], r["estimators"]["E+K"]["first_anchor_prior"]]
    return [["app / case", "mean rmse s", "worst night rmse s", "n (graded)", "note"],
            ["Prime, first read of a game (K carried from the other night)", a[1].get("_mean_rmse"), a[1].get("_worst_rmse"),
             sum(v.get("n", 0) for k, v in a[1].items() if not k.startswith("_")), "K differs per game: why it sat at 13 s"],
            ["Prime, >=1 anchor this game + meter (hold-out)", est.get("_mean_rmse"), est.get("_worst_rmse"),
             sum(est[n]["n"] for n in nights), "estimators tie after the first anchor"],
            ["YouTube TV, stick vs its live edge (meter)", "-", "-", r["yttv_landing"]["n"],
             f"landing p90 {r['yttv_landing'].get('p90_abs')} s; absolute delay: no truth in the logs"]]


def recommended(r: dict) -> dict:
    """The config that the numbers back (delay.timing.app_sigma). Prime only; YouTube TV has no absolute truth to earn one."""
    est = r["estimators"]["meter"]["after_1_anchor"]
    nights = [{"night": n, "n": est[n]["n"], "rmse": est[n]["rmse"]} for n in r["nights"] if est.get(n, {}).get("n")]
    worst = max((n["rmse"] for n in nights), default=None)
    return {"prime": {"sigma_s": 1.0, "min_anchors": 2, "meter_s": 15.0, "worst_night_rmse_s": worst,
                      "why": "worst night rmse + the capture offset no log can measure (T2-cap replaces this margin)",
                      "replay": {"method": "whole_night_holdout", "app": "prime", "nights": nights}}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--table", action="store_true", help="per-app table only")
    ap.add_argument("--tv-list", action="store_true", help="what the logs cannot tell, and the hidden-stick tests")
    a = ap.parse_args(argv)
    if a.tv_list:
        print(CANT_KNOW + "\n\n" + TV_LIST)
        return 0
    data = a.data
    reads = load_reads(data)
    anc = anchors(data, reads)
    report: dict = {"truth": {"reads": len(reads), "anchors": len(anc),
                              "by_night": dict(Counter(x["night"] for x in anc))}}
    report["capture_slope"] = capture_slope(anc)
    report["meter"] = {"prime": meter_stats(data, "prime"), "youtubetv": meter_stats(data, "youtubetv")}
    report["prime_position_forms"] = k_forms(data)
    report["yttv_landing"] = yttv_landing(data)
    report["yttv_burst_z"] = burst_anchor_spread(data)
    # K per game (z - E), for the cross-night prior and the honest per-game finding
    plays = {g: feed_clocks(data, g) for g in {r["game"] for r in reads}}
    edge = edge_series(data, "prime")
    all_edge = sorted((r["t"], r) for rs in edge.values() for r in rs)
    tt = [t for t, _ in all_edge]

    def meter_at(x, max_age: float = 30.0):
        if x.get("E") is not None:
            return x["E"]
        i = bisect_right(tt, x["t0"])
        if i == 0:
            return None
        r = all_edge[i - 1][1]
        return None if x["t0"] - r["t"] > max_age or abs(r["E_s"]) > 1000 else r["E_s"]

    k_by: dict[str, list[float]] = defaultdict(list)
    for x in anc:
        e = meter_at(x)
        if e is not None:
            k_by[f"{x['night']}"].append(x["z"] - e)
    report["k_by_night"] = {n: {"n": len(v), "median": round(st.median(v), 2),
                                "sd": round(st.pstdev(v), 2) if len(v) > 1 else None} for n, v in k_by.items()}
    nights = sorted({x["night"] for x in anc})
    games = defaultdict(list)
    for x in anc:
        games[(x["night"], x["game"])].append(x)
    sequences: dict[str, list[dict]] = defaultdict(list)
    raw_by_game: dict[tuple, list[dict]] = defaultdict(list)
    for r in raw_anchors(data, reads):
        raw_by_game[(r["night"], r["game"])].append(r)
    for (night, g), ga in games.items():
        sequences["today"] += filter_prediction(ga, robust=False, midpoint=False, meter=None)
        sequences["robust"] += filter_prediction(ga, robust=True, midpoint=False, meter=None)
        sequences["midpoint"] += filter_prediction(ga, robust=False, midpoint=True, meter=None)
        rawg = raw_by_game.get((night, g), [])
        if len(rawg) > 1:
            sequences["today_on_raw"] += raw_prediction(rawg, robust=False)
            sequences["robust_on_raw"] += raw_prediction(rawg, robust=True)
        gr = [r for r in reads if r["game"] == g and r["night"] == night]
        sequences["clock_fit"] += clock_fit_prediction(gr, ga, plays[g])
        # the K prior for this game's night comes from the OTHER nights only (whole-night hold-out)
        others = [v for n, vals in k_by.items() if n != night for v in vals]
        kp = (st.median(others), max(1.0, st.pstdev(others))) if len(others) > 1 else None
        sequences["E+K"] += k_prediction(ga, meter_at, kp)
        # the meter into the robust filter: the position reads of that game, a read every 2 s where the log has one
        m = [(x["t0"] - 1.0, x["t0"] - 1.0 - meter_at(x)) for x in ga if meter_at(x) is not None]
        if m:
            sequences["meter"] += filter_prediction(ga, robust=True, midpoint=False, meter=m)
    for name, rows in sequences.items():
        report.setdefault("estimators", {})[name] = {"first_anchor_prior": by_night(rows, 0, 0) if name == "E+K" else None,
                                                     "after_1_anchor": by_night(rows, 1),
                                                     "after_3_anchors": by_night(rows, 3)}
    report["nights"] = nights
    report["table"] = per_app_table(report)
    report["recommended"] = recommended(report)
    if a.table:
        for row in report["table"]:
            print(" | ".join(str(c) for c in row))
    else:
        print(json.dumps(report, indent=1))
    if a.json:
        a.json.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
