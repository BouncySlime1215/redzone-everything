"""The North Star table: where we are against "perfect", in one place, with honest error bars.

    python scripts/northstar.py                       # dev + locked, writes docs/northstar.md + history row
    python scripts/northstar.py --split dev --json ns.json --no-write          # what PR CI runs (head and base)
    python scripts/northstar.py --delta head_ns.json base_ns.json --md ns.md   # the PR comment's delta table
    python scripts/northstar.py --night-seeds 8       # also replay the e2e fault night 8 times (recovery line)

Every line comes from code that already exists: the scorecards (scripts/eval.py, eval_sports.py, run
as their own processes through scripts/ci_scorecard.py, always with the MEASURED feed lag, `--real-lag`),
the release gate's night-level cluster bootstrap (scripts/release_gate.py: PASS needs >= 8 nights, >= 60
decisions and the 95 % interval's lower bound at the bar; a point under the bar FAILs; else PENDING), the
e2e fault night (`python -m redzone.lab.night`) and the TV's own input-change log (data/tv_input.jsonl).

Two splits (redzone/lab/splits.py):
  dev     = `fit` (train + valid nights): what work may be measured and tuned against. PR deltas use it.
  locked  = `test`, the LOCKED newest >= 25 % of nights. Reported by the nightly job only, never in a PR
            delta, and nothing may be tuned on it (the no-overfitting rule, docs/architecture.md §7).

Status: PASS / FAIL / PENDING exactly as the release gate means them (pending never counts as passing),
plus INFO for a line with no target (the feed lag in use). A line whose value is an upper bound on the
real thing (live-or-replayed from a scorecard that does not grade replay age) can FAIL but not PASS.

No new dependencies: standard library + the repo's own scripts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

import ci_scorecard  # noqa: E402
import release_gate as rg  # noqa: E402

PASS, FAIL, PENDING, INFO = rg.PASS, rg.FAIL, rg.PENDING, "INFO"
SPLITS = {"dev": "fit", "locked": "test"}
LEAGUES = ("nfl", "nba", "nhl")
MD = ROOT / "docs/northstar.md"
HISTORY = ROOT / "docs/northstar-history.jsonl"
ROADMAP_SRC = ROOT / "docs/roadmap.json"
ROADMAP_MD = ROOT / "docs/roadmap.md"

# Measured on Nick's TV, Nov 3 2026 (redzone/lab/night/smartthings.py): 10 of 10 confirmed.
ST_MEASURED = {"n": 10, "median_s": 1.98, "max_s": 2.2}
MIN_SWITCH_SAMPLES = 60      # the same evidence bar as a rate gate's decisions

# Which e2e-night check proves each injected fault was recovered without Nick (tests/test_e2e_night.py).
FAULT_CHECK = {"stick_drop": "two_stick_recovers", "stick_wedge": "wedged_stick_reconnected",
               "still_watching": "still_watching_cleared", "feed_outage": "feed_outage_handled",
               "flip_ignored": "tv_shows_games", "api_down": "no_crash"}

ORACLE_LEAGUES = ("nfl", "nba", "nhl")   # NFL: the app re-graded by sportsgrade beside the oracle (oracle_app)

# key, label, target text, direction (+1 higher is better, -1 lower), bar, scale (for the roadmap's gap
# normalisation: the size of a "whole" gap in this line's unit)
METRICS = [
    # Nick, Nov 4 3:15 PM: never JOIN mid-play (hard 0); leaving mid-play is allowed at low leverage only
    ("mid_play", "Mid-play entries (per 100 landings)", "0", -1, 0.0, 5.0),
    ("high_leverage_leaves", "High-leverage mid-play leaves (close + late or OT; per 100 landings)", "0", -1, 0.0, 5.0),
    ("low_leverage_leaves", "Low-leverage mid-play leaves (allowed; per 100 landings)", "info", -1, None, None),
    # [EXP-001] suspense at entry: S = E[(dWP)^2] over the 8 plays after each entry (WP points^2), beside mid-play
    ("suspense_entry", "Suspense at entry (mean dWP^2 over the next 8 plays, WP pts^2)", "info", 1, None, None),
    ("landings_dead", "Landings in dead time", ">= 98%", 1, 98.0, 100.0),
    ("possession_start", "Entries at a possession start (dead ball, no play missed)", ">= 95%", 1, 95.0, 100.0),
    # Nick, Nov 4 10:55 AM: gates are honest against the hindsight ceiling: >= 95% OF ORACLE VALUE where the
    # oracle ran (big_live_nfl/nba/nhl); leagues without an oracle keep the absolute 95% bar.
    *[(f"big_live_{lg}", f"Big plays seen live, {lg.upper()}", ">= 95% of oracle" if lg in ("nfl", "nba", "nhl") else ">= 95%",
       1, 95.0, 100.0) for lg in LEAGUES],
    ("your_team_big", "Your-team big plays seen (live or within 3 min)", "100%", 1, 100.0, 100.0),
    *[(f"crunch_live_{lg}", f"Crunch live, {lg.upper()}", ">= 95%", 1, 95.0, 100.0) for lg in LEAGUES],
    ("your_team_crunch", "Your-team crunch live", "100%", 1, 100.0, 100.0),
    # Nick, Nov 4 4:15 PM: crunch is a learned state with an entry AND an exit (hysteresis); the exit is graded
    # against the hindsight oracle. PENDING until a card carries crunch_exits / crunch_exits_ok per night.
    ("crunch_exit_acc", "Crunch exit accuracy vs the hindsight oracle", ">= 95%", 1, 95.0, 100.0),
    ("league_order", "League order respected (no lower-league non-crunch over a higher-league crunch)", "100%", 1, 100.0, 100.0),
    # Nick, Nov 4 10:55 AM: the Noggin is fully self-taught, so no line is graded by Nick's taps.
    ("pct_of_oracle", "% of hindsight-oracle value achieved (mean of NFL, NBA, NHL)", ">= 95%", 1, 95.0, 100.0),
    # Nick, Nov 4 11:00 AM: a missed key moment must air within 2-3 min (the next dead moment), never 10.
    ("big_seen_3min", "Big plays live or replayed within 3 min", ">= 99%", 1, 99.0, 100.0),
    ("big_seen_2min", "Big plays live or replayed within 2 min (stretch)", ">= 99%", 1, 99.0, 100.0),
    ("switch_p95", "Switch time p95 (SmartThings input change)", "<= 3 s", -1, 3.0, 3.0),
    ("flips_settled", "Flips PLAYING within 3 s", ">= 99%", 1, 99.0, 100.0),
    ("delay_err_p95", "Per-stick delay error p95", "<= 2 s", -1, 2.0, 2.0),
    ("press_to_tv_p95", "Phone press to TV p95 (\"Watch this\" + \"Go now\" until the game plays)", "<= 3 s", -1, 3.0, 3.0),
    ("dead_air", "Dead air (commercials/breaks on screen)", "0%", -1, 0.0, 100.0),
    # Nick, Nov 4 3:30 PM: Heat 2.0, one learned Watch Value per game (0-100, 50 = league-median live moment,
    # 90+ = top 5%), self-taught from what happened next. Gates TBD; PENDING until a card carries `watch_value`.
    ("wv_calibration_err", "Watch Value calibration error (reliability curve, locked; points)", "gate TBD", -1, None, None),
    ("wv_rank_agree", "Watch Value rank agreement with the oracle (pairwise, %)", "gate TBD", 1, None, None),
    # Nick, Nov 4 3:40 PM: the city map's contract checker (lane 4). PENDING until it writes docs/citymap.json.
    ("contract_violations", "Contract violations (city map checker)", "0", -1, 0.0, 10.0),
    ("recovery", "Faults recovered without Nick (e2e night)", ">= 95%", 1, 95.0, 100.0),
    *[(f"feed_lag_{lg}", f"Honest feed lag in use, {lg.upper()} (p50)", "info", -1, None, None) for lg in LEAGUES],
    # [oracle] hindsight ceiling - app, same nights, same grader (redzone/lab/oracle.py; --oracle)
    *[(f"oracle_gap_{m}_{lg}", f"Oracle gap: {lbl}, {lg.upper()} (ceiling - app, points)", "info", -1, None, None)
      for m, lbl in (("big_live", "big plays live"), ("crunch_live", "crunch live"), ("dead_air", "dead air"))
      for lg in ORACLE_LEAGUES],
    # [oracle] the app as a share of the hindsight ceiling, worst of big live / crunch live / live air
    *[(f"pct_of_oracle_{lg}", f"% of oracle, {lg.upper()} (worst of big live, crunch live, live air)", ">= 95%", 1,
       95.0, 100.0) for lg in ORACLE_LEAGUES],
    *[(f"ceiling_{m}_{lg}", f"Ceiling: {lbl}, {lg.upper()} (hindsight, live-only, 0 mid-play)", "info", d, None, None)
      for m, lbl, d in (("big_live", "big plays live", 1), ("dead_air", "dead air", -1), ("crunch_live", "crunch live", 1))
      for lg in ORACLE_LEAGUES],
]
META = {k: dict(label=lb, target=t, dir=d, bar=b, scale=s) for k, lb, t, d, b, s in METRICS}


# ---- running the evidence ---------------------------------------------------------------------

def run_cards(root: Path, split: str, timeout: float = 1800, profile: str | None = None,
              oracle: bool = False) -> dict:
    """The three league scorecards on one split, every night in it, measured feed lag. With `profile`
    (redzone/noggin/profile.py), the same cards with that profile's flags and buffer on."""
    sp = SPLITS[split]
    argvs = {"nfl": ["scripts/eval.py", "--n", "999", "--split", sp, "--real-lag"],
             **{lg: ["scripts/eval_sports.py", lg, "--nights", "999", "--split", sp, "--real-lag"] for lg in ("nba", "nhl")}}
    if profile:
        from redzone.noggin import profile as P
        argvs = {lg: a + P.argv(profile, lg) for lg, a in argvs.items()}
    if oracle:
        argvs = {lg: a + (["--oracle"] if lg in ORACLE_LEAGUES else []) for lg, a in argvs.items()}
    out = {}
    for lg, argv in argvs.items():
        if not (root / f"data/archive/{lg}").exists():
            out[lg] = {"error": f"no data/archive/{lg} (run scripts/fetch_eval_data.py)"}
            continue
        card, err, took = ci_scorecard.run_one(root, argv, timeout)
        out[lg] = {"metrics": card, "seconds": round(took)} if card is not None else {"error": err}
        print(f"  {split}/{lg}{'/' + profile if profile else ''}: {'ok' if card else err} ({took:.0f} s)", flush=True)
    return out


def run_nights(root: Path, seeds: int, timeout: float = 900) -> list[dict]:
    """The e2e fault night (SmartThings TV path) once per seed; each report as the night wrote it."""
    reps = []
    for seed in range(7, 7 + seeds):
        with tempfile.TemporaryDirectory(prefix="rz-ns-night-") as tmp:
            out = Path(tmp) / "night.json"
            env = {**os.environ, "PYTHONPATH": str(root), "RZ_NIGHT_HOSTILE_HOST": "1"}
            env.pop("RZ_NIGHT_TRACE", None)
            try:
                subprocess.run([sys.executable, "-m", "redzone.lab.night", "--tv", "smartthings", "--seed", str(seed),
                                "--json", str(out), "--work", str(Path(tmp) / "w")],
                               cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                reps.append({"seed": seed, "error": f"timed out after {timeout:.0f} s"})
                continue
            rep = json.loads(out.read_text()) if out.exists() else {"error": "the night wrote no report"}
            rep["seed"] = seed
            reps.append(rep)
            print(f"  night seed {seed}: {'ok' if rep.get('ok') else 'not ok'}", flush=True)
    return reps


def run_drill(root: Path, seeds: int, hours: float = 3.0, timeout: float = 1800) -> dict | None:
    """The controls drill (scripts/controls_drill.py, taps lost and duplicated on purpose) with the
    checkout's shipped defaults, so press_to_tv_p95 has a sim value on every run. None if it failed."""
    if seeds <= 0 or not (root / "scripts/controls_drill.py").exists():
        return None
    with tempfile.TemporaryDirectory(prefix="rz-ns-drill-") as tmp:
        out = Path(tmp) / "controls.json"
        env = {**os.environ, "PYTHONPATH": str(root)}
        try:
            subprocess.run([sys.executable, "scripts/controls_drill.py", "--seeds", str(seeds), "--hours", str(hours),
                            "--flaky", "--json", str(out)], cwd=root, env=env, capture_output=True, text=True,
                           timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"  controls drill: timed out after {timeout:.0f} s", flush=True)
            return None
        if not out.exists():
            print("  controls drill: wrote no report", flush=True)
            return None
        drill = json.loads(out.read_text())
        print(f"  controls drill: {seeds} nights done", flush=True)
        return drill


def switch_samples(paths: list[Path]) -> list[float]:
    """Command -> TV-confirmed input change, seconds, from the live service's data/tv_input.jsonl."""
    xs = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("verified") and isinstance(r.get("total_s"), (int, float)):
                xs.append(float(r["total_s"]))
    return xs


# ---- the table --------------------------------------------------------------------------------

def _per(cards: dict, lg: str) -> list[dict]:
    m = rg._m(cards.get(lg, {}))
    return [x for x in m.get("per_night") or [] if isinstance(x, dict)]


def _row(key, value, lo, hi, status, detail, n_nights=None) -> dict:
    return {"key": key, "value": value, "lo": lo, "hi": hi, "status": status, "detail": detail, "nights": n_nights}


def suspense_row(per: dict, note: str = "") -> dict:
    """[EXP-001] Suspense at entry, an INFO line beside mid-play entries: mean S over every graded entry with
    ESPN win probability (NFL, NBA; NHL's feed has none, so it is left out until EXP-002's WP model)."""
    s = {lg: (sum(float(x.get("suspense_sum") or 0) for x in per[lg]), sum(int(x.get("suspense_n") or 0) for x in per[lg]))
         for lg in LEAGUES if per[lg]}
    n = sum(k for _, k in s.values())
    if not n:
        return _row("suspense_entry", None, None, None, INFO, "no entry with feed win probability in this slice" + note)
    by = ", ".join(f"{lg.upper()} {v / k:.1f} ({k})" for lg, (v, k) in s.items() if k)
    return _row("suspense_entry", round(sum(v for v, _ in s.values()) / n, 2), None, None, INFO,
                f"{n} entries [{by}]; higher = we entered games whose next plays swung the result more" + note,
                sum(1 for lg in s for x in per[lg] if x.get("suspense_n")))


def _rate(key, rows, bar, upper_bound_only=False, note="") -> dict:
    rows = [(g, n) for g, n in rows if n]
    if not rows:
        return _row(key, None, None, None, PENDING, "no decisions in this slice" + note)
    st, det = rg.rate_gate(rows, bar)
    point, lo, hi = rg.night_ci(rows)
    if upper_bound_only and st == PASS:
        st, det = PENDING, det + "; an upper bound (replay age not graded), so it can't PASS yet"
    return _row(key, round(point, 2), round(lo, 2), round(hi, 2), st, det + note, len(rows))


def _boot_ratio(rows: list[tuple[float, float]], draws: int = rg.BOOT_DRAWS, seed: int = 7):
    """Pooled ratio sum(a)/sum(b) in %, with a 95 % cluster bootstrap over the rows (nights)."""
    import random
    rows = [(a, b) for a, b in rows if b]
    if not rows:
        return None, None, None
    point = 100.0 * sum(a for a, _ in rows) / sum(b for _, b in rows)
    rng = random.Random(seed)
    rs = sorted(100.0 * sum(x[0] for x in pick) / sum(x[1] for x in pick)
                for pick in ([rows[rng.randrange(len(rows))] for _ in rows] for _ in range(draws)))
    return point, rs[int(0.025 * draws)], rs[min(draws - 1, int(0.975 * draws))]


def table(cards: dict, nights: list[dict] | None = None, switch_s: list[float] | None = None,
          lag_profile: dict | None = None, controls: dict | None = None, contracts: dict | None = None) -> list[dict]:
    rows: list[dict] = []
    per = {lg: _per(cards, lg) for lg in LEAGUES}
    broken = [lg for lg in LEAGUES if not rg._m(cards.get(lg, {}))]
    note = f" (missing: {', '.join(broken)})" if broken else ""
    every = [x for lg in LEAGUES for x in per[lg]]

    # 1. mid-play ENTRIES: hard 0 (Nick, Nov 4 3:15 PM: never join mid-play). 0 events in N landings bounds
    # the rate at 3/N (rule of three). Leaves are split by leverage below.
    ev = [(int(x.get("entries_mid_play") or 0), int(x.get("landings") or 0)) for x in every]
    n_land = sum(n for _, n in ev)
    n_ev = sum(e for e, _ in ev)
    if not n_land:
        rows.append(_row("mid_play", None, None, None, PENDING, "no landings counted (old scorecard?)" + note))
    else:
        point, lo, hi = _boot_ratio(ev)
        by = ", ".join(f"{lg.upper()} {sum(int(x.get('entries_mid_play') or 0) for x in per[lg])}"
                       for lg in LEAGUES if per[lg])
        if n_ev:
            st, det = FAIL, f"{n_ev} in {n_land} landings ({by})"
        else:
            det = f"0 in {n_land} landings; true rate {rg.zero_bound(n_land)}"
            st = PASS if len(ev) >= rg.MIN_NIGHTS and n_land >= 600 else PENDING
            if st == PENDING:
                det += "; needs >= 8 nights and >= 600 landings to prove < 0.5%"
            lo = hi = 0.0
            hi = round(300.0 / n_land, 2)
        rows.append(_row("mid_play", round(point, 2), round(lo, 2), round(hi, 2), st, det + note, len(ev)))
    rows += leave_rows(per, every, n_land, note)
    rows.append(suspense_row(per, note))

    # 2. landings (both ends of every switch) in on-screen dead time
    rows.append(_rate("landings_dead", [(int(x.get("landings_dead") or 0), int(x.get("landings") or 0)) for x in every],
                      98.0, note=note))
    # 2b. [possession start] entries that came up on a dead ball with no play missed (older cards: pending)
    rows.append(_rate("possession_start", [(int(x["possession_starts"]), int(x.get("entries") or 0))
                                           for x in every if "possession_starts" in x], 95.0, note=note))
    # 3. big plays seen live, per league
    for lg in LEAGUES:
        rows.append(_rate(f"big_live_{lg}", [(int(x.get("big_live") or 0), int(x.get("big") or 0)) for x in per[lg]],
                          95.0, note="" if per[lg] else f" ({lg.upper()} scorecard missing)"))
    # 4. your-team big plays, live or replayed within 3 min where the scorecard grades it (my_big_seen_3m),
    # else live only (older scorecards: NFL's my_big_live)
    yt = [(int(x["my_big_seen_3m"]), int(x.get("my_big") or 0)) if "my_big_seen_3m" in x
          else (int(x.get("my_big_live") or 0), int(x.get("my_big") or 0)) for x in every]
    r = _rate("your_team_big", yt, 100.0, note=note)
    if not all("my_big_seen_3m" in x for x in every):
        r["detail"] += "; some scorecards don't grade the 3-min replay window: live only there"
    rows.append(r)
    # 4b. crunch live per league. Under the league order a lower-priority overlap that gets a replay is
    # not a miss, and that feasible ceiling isn't computed yet: a miss under 95 % reads PENDING (as in
    # scripts/release_gate.py), never PASS. Your-team crunch is a hard 100 % (Nick's L1 rule).
    for lg in LEAGUES:
        r = _rate(f"crunch_live_{lg}", [(int(x.get("crunch_live") or 0), int(x.get("crunch") or 0)) for x in per[lg]],
                  95.0, note="" if per[lg] else f" ({lg.upper()} scorecard missing)")
        if r["status"] == FAIL:
            r["status"] = PENDING
            r["detail"] += "; under 95 but the league-order feasible ceiling isn't computed yet"
        rows.append(r)
    rows.append(_rate("your_team_crunch", [(int(x.get("my_crunch_live") or 0), int(x.get("my_crunch") or 0))
                                           for x in every], 100.0, note=note))
    # 4c. Noggin priority line (docs/noggin.md § Priority): needs a mixed-league night grade
    rows.append(_row("league_order", None, None, None, PENDING,
                     "needs a mixed-league night graded for overlaps (Noggin Priority module; the per-league "
                     "scorecards can't see a cross-league overlap)"))
    # 5. live or replayed within 3 min (gate) and 2 min (stretch). With the replay age graded
    # (big_seen_3m / _2m) they can PASS; an older scorecard only has big_seen (a replay of any age), an
    # upper bound that can FAIL but never PASS.
    for key, fld_ok in (("big_seen_3min", "big_seen_3m"), ("big_seen_2min", "big_seen_2m")):
        graded = bool(every) and all(fld_ok in x for x in every)
        fld = fld_ok if graded else "big_seen"
        sub = ", ".join(f"{lg.upper()} {100.0 * sum(int(x.get(fld) or 0) for x in per[lg]) / max(1, sum(int(x.get('big') or 0) for x in per[lg])):.1f}%"
                        for lg in LEAGUES if per[lg])
        rows.append(_rate(key, [(int(x.get(fld) or 0), int(x.get("big") or 0)) for x in every], 99.0,
                          upper_bound_only=not graded, note=f" [{sub}]" + note))
    # 6. switch time p95: real TV samples if there are any, else the Nov 3 measurement (too few to prove p95)
    rows.append(switch_row(switch_s or []))
    # 6b. [sync plan, TIMING] flips that reach PLAYING within 3 s: Controller.settle_book {n, ok} as the
    # e2e night reports it (a switch whose picture was never checked is not in n)
    rows.append(settle_row(nights))
    rows.append(_row("delay_err_p95", None, None, None, PENDING,
                     "not measured: needs the break-anchor delay estimator (on-screen break start/end vs feed "
                     "event time) and Wednesday's probe logs"))
    rows.append(press_row(controls))
    # 7. dead air
    d = [(float(x.get("dead_s") or 0), float(x.get("watched_s") or 0)) for x in every]
    point, lo, hi = _boot_ratio(d)
    if point is None:
        rows.append(_row("dead_air", None, None, None, PENDING, "no watched time counted" + note))
    else:
        by = ", ".join(f"{lg.upper()} {rg._m(cards[lg]).get('dead_air_pct')}%" for lg in LEAGUES if per[lg])
        rows.append(_row("dead_air", round(point, 2), round(lo, 2), round(hi, 2), FAIL if lo > 0 else PENDING,
                         f"{point:.1f}% of watched time [{by}]; ceiling (all games in break, no replay) not computed" + note,
                         len(d)))
    # 8. recovery without Nick
    rows.append(recovery_row(nights))
    # 9. the honest feed lag in use (what the --real-lag scorecards draw from)
    prof = (lag_profile or {}).get("leagues", {})
    for lg in LEAGUES:
        p = prof.get(lg)
        if not p:
            rows.append(_row(f"feed_lag_{lg}", None, None, None, INFO, "no measured profile"))
            continue
        qs = dict(zip(lag_profile.get("quantiles", []), p["q"]))
        extra = " ; NHL api-web wsc measured 16.7 s p50 (not wired yet, #58)" if lg == "nhl" else ""
        rows.append(_row(f"feed_lag_{lg}", qs.get(50), qs.get(10), qs.get(90), INFO,
                         f"{lag_profile.get('source')} p10-p90 {qs.get(10)}-{qs.get(90)} s, n={p['n']}, {p.get('basis')}{extra}"))
    rows += system_rows(cards, contracts)
    rows += oracle_rows(cards)
    apply_oracle_gates(rows)
    return rows


def load_contracts(root: Path) -> dict | None:
    """The contract kernel's verdict (scripts/contract_check.py, lane 4, Nick Nov 4 3:40 PM): every hard
    violation now, and how many are new against docs/contract_baseline.json. None if the kernel isn't there."""
    try:
        import contract_check as kc
    except ImportError:
        return None
    try:
        found, _ = kc.check(root)
        res = kc.compare(found, kc.read_baseline(root / kc.BASELINE))
    except (OSError, ValueError):
        return None
    return {"violations": [{"rule": v.rule, "where": v.path} for v in res["violations"]],
            "new": len(res["new"]), "baseline": res["baseline"]}


def system_rows(cards: dict, contracts: dict | None = None) -> list[dict]:
    """Watch Value (Heat 2.0) and the city map: lines Nick asked for on Nov 4 before their builders landed.
    Each reads PENDING until its evidence exists, never PASS by default."""
    out = []
    wv = {lg: rg._m(cards.get(lg, {})).get("watch_value") for lg in LEAGUES}
    wv = {lg: v for lg, v in wv.items() if v}
    for key, field_, lbl in (("wv_calibration_err", "calibration_err", "calibration error"),
                             ("wv_rank_agree", "rank_agree_pct", "rank agreement")):
        vals = {lg: v.get(field_) for lg, v in wv.items() if v.get(field_) is not None}
        if not vals:
            out.append(_row(key, None, None, None, PENDING, "no card carries watch_value yet (Heat 2.0, lanes 1+2)"))
            continue
        worst = max(vals.values()) if META[key]["dir"] < 0 else min(vals.values())
        out.append(_row(key, round(worst, 2), None, None, INFO,
                        "worst league; " + ", ".join(f"{lg.upper()} {v:.1f}" for lg, v in vals.items()) + f" ({lbl})"))
    if all("crunch_exits" in x for lg in LEAGUES for x in _per(cards, lg)) and any(_per(cards, lg) for lg in LEAGUES):
        out.append(_rate("crunch_exit_acc", [(int(x.get("crunch_exits_ok") or 0), int(x.get("crunch_exits") or 0))
                                             for lg in LEAGUES for x in _per(cards, lg)], 95.0))
    else:
        out.append(_row("crunch_exit_acc", None, None, None, PENDING,
                        "no card grades crunch exits yet (learned crunch state, lanes 1+2)"))
    if contracts is None or "violations" not in contracts:
        out.append(_row("contract_violations", None, None, None, PENDING, "contract kernel not found (scripts/contract_check.py)"))
    else:
        v = contracts["violations"] or []
        rules: dict[str, int] = {}
        for x in v:
            rules[str(x.get("rule", "?"))] = rules.get(str(x.get("rule", "?")), 0) + 1
        base = (f"; {contracts['new']} new vs baseline {contracts['baseline']}"
                if contracts.get("baseline") is not None else "")
        out.append(_row("contract_violations", len(v), None, None, PASS if not v else FAIL,
                        ("none" if not v else ", ".join(f"{k} {n}" for k, n in sorted(rules.items()))) + base))
    return out


def leave_rows(per: dict, every: list[dict], n_land: int, note: str = "") -> list[dict]:
    """Mid-play LEAVES split by leverage (redzone/lab/leverage.py, Nick Nov 4 3:15 PM): a high-leverage leave
    (close + late, or overtime) is a hard 0; a low-leverage one (after a score, in transition, early or not
    close) is allowed and shown as info. Older cards without `cuts_high` read PENDING, never PASS."""
    if not n_land:
        return [_row("high_leverage_leaves", None, None, None, PENDING, "no landings counted" + note),
                _row("low_leverage_leaves", None, None, None, INFO, "no landings counted" + note)]
    if not all("cuts_high" in x for x in every):
        cuts = sum(int(x.get("cuts") or 0) for x in every)
        return [_row("high_leverage_leaves", None, None, None, PENDING,
                     f"{cuts} mid-play leaves, leverage not graded (older scorecard: no cuts_high)" + note),
                _row("low_leverage_leaves", None, None, None, INFO, f"{cuts} mid-play leaves, not split" + note)]
    hi_ev = [(int(x.get("cuts_high") or 0), int(x.get("landings") or 0)) for x in every]
    n_hi = sum(e for e, _ in hi_ev)
    n_lo = sum(int(x.get("cuts") or 0) for x in every) - n_hi
    by = ", ".join(f"{lg.upper()} {sum(int(x.get('cuts_high') or 0) for x in per[lg])}" for lg in LEAGUES if per[lg])
    if n_hi:
        point, lo, hi = _boot_ratio(hi_ev)
        r = _row("high_leverage_leaves", round(point, 2), round(lo, 2), round(hi, 2), FAIL,
                 f"{n_hi} in {n_land} landings ({by})" + note, len(hi_ev))
    else:
        st = PASS if len(hi_ev) >= rg.MIN_NIGHTS and n_land >= 600 else PENDING
        r = _row("high_leverage_leaves", 0.0, 0.0, round(300.0 / n_land, 2), st,
                 f"0 in {n_land} landings; true rate {rg.zero_bound(n_land)}" + note, len(hi_ev))
    lo_r = _row("low_leverage_leaves", round(100.0 * n_lo / n_land, 2), None, None, INFO,
                f"{n_lo} in {n_land} landings (allowed: after a score, in transition, early or not close)" + note)
    return [r, lo_r]


def oracle_rows(cards: dict) -> list[dict]:
    """[oracle] Each line's hindsight ceiling on the same nights (live-only, no rewinds, every L1 rule and
    0 mid-play), and the gap to what the app did. Only when the cards ran with --oracle."""
    out = []
    for lg in ORACLE_LEAGUES:
        m = rg._m(cards.get(lg, {}))
        orc = m.get("oracle") or {}
        shares = {}
        for key, obj, app_key in (("big_live", "big_live", "big_live_pct"), ("crunch_live", "crunch_live", "crunch_live_pct"),
                                  ("dead_air", "dead_air", "dead_air_pct")):
            o = (orc.get(obj) or {}).get(f"{key}_pct" if key != "dead_air" else "dead_air_pct")
            # NFL's card has its own grader: compare against the app re-graded by the oracle's (oracle_app)
            app = (m.get("oracle_app") or {}).get(app_key, m.get(app_key)) if m.get("oracle_app") else m.get(app_key)
            if o is None or app is None:
                continue
            gap = round((app - o) if key == "dead_air" else (o - app), 1)
            out.append(_row(f"oracle_gap_{key}_{lg}", gap, None, None, INFO,
                            f"ceiling {o}% vs app {app}% ({orc[obj].get('switches')} oracle switches, "
                            f"{orc[obj].get('entries_mid_play')} mid-play entries)"))
            out.append(_row(f"ceiling_{key}_{lg}", o, None, None, INFO, f"app {app}% on the same nights"))
            # live air = 100 - dead air, so every share is 'more is better'
            share = ((100 - app) / (100 - o) if key == "dead_air" else (app / o if o else None))
            if share is not None:
                shares[key] = round(100 * min(1.0, share), 1)
                out[-1]["share"] = shares[key]          # read by apply_oracle_gates
        if len(shares) == 3:
            worst = min(shares, key=shares.get)
            v = shares[worst]
            out.append(_row(f"pct_of_oracle_{lg}", v, None, None, PASS if v >= 95.0 else FAIL,
                            ", ".join(f"{k} {x}%" for k, x in shares.items()) + f" of the ceiling (worst: {worst})"))
    out.append(pct_of_oracle_row(out))
    return out


ORACLE_GATE = 95.0   # % of the hindsight ceiling (Nick, Nov 4 10:55 AM)


def apply_oracle_gates(rows: list[dict]) -> None:
    """Gate big_live_<lg> on >= 95% OF ORACLE VALUE (the share of the hindsight ceiling on the same nights)
    and add the gap to 100% of oracle to big live, crunch live and dead air. Rows without an oracle keep
    their absolute gate; a slice too thin for the release gate stays PENDING."""
    by = {r["key"]: r for r in rows}
    for lg in ORACLE_LEAGUES:
        for key in ("big_live", "crunch_live", "dead_air"):
            row = by.get(f"{key}_{lg}" if key != "dead_air" else "dead_air")
            ceil = by.get(f"ceiling_{key}_{lg}")
            if row is None or ceil is None or ceil.get("share") is None:
                continue
            share = ceil["share"]
            gap = f"{lg.upper()} {share}% of oracle (ceiling {ceil['value']}%), gap to 100% of oracle {round(100 - share, 1)} pts"
            row["detail"] = (row.get("detail") or "") + "; " + gap
            if key != "big_live" or row.get("value") in (None, 0):
                continue
            row["oracle_share"] = share
            thin = row["status"] == PENDING
            share_lo = share * (row["lo"] / row["value"]) if row.get("lo") is not None else None
            if share < ORACLE_GATE:
                row["status"] = FAIL
            elif not thin and share_lo is not None and share_lo >= ORACLE_GATE:
                row["status"] = PASS
            else:
                row["status"] = PENDING


def pct_of_oracle_row(rows: list[dict]) -> dict:
    """% of hindsight-oracle value achieved: the mean of the per-league pct_of_oracle rows (each the worst
    of big live, crunch live and live air vs the DP ceiling). Self-taught by construction: graded against
    the oracle on archived nights, never against Nick's taps (Nick, Nov 4 10:55 AM)."""
    per = {r["key"][len("pct_of_oracle_"):]: r["value"] for r in rows
           if r["key"].startswith("pct_of_oracle_") and r.get("value") is not None}
    if set(per) != set(ORACLE_LEAGUES):
        missing = ", ".join(lg.upper() for lg in ORACLE_LEAGUES if lg not in per)
        return _row("pct_of_oracle", None, None, None, PENDING, f"needs the oracle for {missing} (--oracle)")
    v = round(sum(per.values()) / len(per), 1)
    return _row("pct_of_oracle", v, None, None, PASS if v >= 95.0 else FAIL,
                ", ".join(f"{lg.upper()} {per[lg]}%" for lg in ORACLE_LEAGUES) + " (mean of the per-league rows)")


def switch_row(xs: list[float]) -> dict:
    if not xs:
        m = ST_MEASURED
        return _row("switch_p95", m["max_s"], None, None, PENDING,
                    f"Nov 3 measurement: {m['n']} of {m['n']} confirmed, median {m['median_s']} s, max {m['max_s']} s "
                    f"(p95 <= max); needs >= {MIN_SWITCH_SAMPLES} logged switches (data/tv_input.jsonl)")
    import random
    xs = sorted(xs)
    q = lambda v: v[min(len(v) - 1, math.ceil(0.95 * len(v)) - 1)]  # noqa: E731
    rng = random.Random(7)
    bs = sorted(q(sorted(rng.choice(xs) for _ in xs)) for _ in range(rg.BOOT_DRAWS))
    p95, lo, hi = q(xs), bs[int(0.025 * len(bs))], bs[int(0.975 * len(bs)) - 1]
    det = f"{len(xs)} confirmed switches, median {xs[len(xs) // 2]:.2f} s, max {xs[-1]:.2f} s"
    if p95 > 3.0:
        st = FAIL
    elif len(xs) >= MIN_SWITCH_SAMPLES and hi <= 3.0:
        st = PASS
    else:
        st = PENDING
        det += f"; needs >= {MIN_SWITCH_SAMPLES} samples and the upper bound <= 3 s"
    return _row("switch_p95", round(p95, 2), round(lo, 2), round(hi, 2), st, det)


def settle_row(nights: list[dict] | None) -> dict:
    per = []
    for rep in nights or []:
        b = rep.get("settle_book") or (rep.get("last_flip") or {}).get("settle_book")
        if isinstance(b, dict) and b.get("n"):
            per.append((int(b.get("ok") or 0), int(b["n"])))
    if not per:
        return _row("flips_settled", None, None, None, PENDING,
                    "not measured: the e2e night doesn't report Controller.settle_book yet (#70)")
    r = _rate("flips_settled", per, 99.0)
    r["detail"] += "; e2e fault nights (fake TV), picture-checked flips only"
    return r
def press_row(drill: dict | None) -> dict:
    """press_to_tv_p95 from the controls drill (scripts/controls_drill.py: phone presses in the replayed
    night, fake sticks and TV timed like the real ones). The sim can FAIL the line, since a real press
    is never faster than the sim's; it can't PASS it: that needs real presses on Nick's TV."""
    s = (drill or {}).get("summary") or {}
    xs = sorted(x for r in (drill or {}).get("runs", []) for x in r.get("go_now_s", []) + r.get("open_go_s", []))
    if not xs:
        return _row("press_to_tv_p95", None, None, None, PENDING,
                    "controls drill not run here (scripts/controls_drill.py --json, then --controls-json)")
    q = lambda v: v[min(len(v) - 1, math.ceil(0.95 * len(v)) - 1)]  # noqa: E731
    p95 = q(xs)
    det = (f"sim drill: {len(xs)} presses over {s.get('nights', '?')} nights, median {xs[len(xs) // 2]:.2f} s, "
           f"{s.get('go_now_within_3s_pct')}% within 3 s; dropped {s.get('dropped', 0)}, ran-not-once "
           f"{s.get('not_once', 0)} of {s.get('taps', 0)} taps ({s.get('taps_retried', 0)} lost/duplicated on purpose)")
    for k, name in (("warm", "pre-tuned"), ("cold", "cold")):
        w = s.get(k) or {}
        if w.get("n"):
            det += f"; {name} {w['n']}: p50 {w['p50_s']} s, p95 {w['p95_s']} s"
    if p95 > 3.0 or s.get("dropped") or s.get("not_once"):
        return _row("press_to_tv_p95", round(p95, 2), None, None, FAIL, det)
    return _row("press_to_tv_p95", round(p95, 2), None, None, PENDING, det + "; PENDING until real presses are logged")


def recovery_row(nights: list[dict] | None) -> dict:
    if not nights:
        return _row("recovery", None, None, None, PENDING,
                    "e2e fault night not run here (nightly job runs it: --night-seeds)")
    per = []
    missing = []
    for rep in nights:
        if rep.get("error") or not rep.get("checks"):
            per.append((0, 1))
            missing.append(f"seed {rep.get('seed')}: {rep.get('error', 'no checks')}")
            continue
        ok = {c["name"]: c["ok"] for c in rep["checks"]}
        faults = [f["name"] for f in rep.get("faults", []) if f.get("name") in FAULT_CHECK]
        good = sum(bool(ok.get(FAULT_CHECK[f])) for f in faults)
        per.append((good, len(faults)))
        missing += [f"seed {rep['seed']}: {f}" for f in faults if not ok.get(FAULT_CHECK[f])]
    r = _rate("recovery", per, 95.0)
    r["detail"] += "; one replayed night per seed (same fixture games, different fault draws)"
    if missing:
        r["detail"] += "; not recovered: " + ", ".join(missing[:6])
    return r


# ---- output -----------------------------------------------------------------------------------

def _fmt(key, v) -> str:
    if v is None:
        return "n/a"
    if key in ("switch_p95", "press_to_tv_p95") or key.startswith("feed_lag"):
        return f"{v:.2f} s" if key in ("switch_p95", "press_to_tv_p95") else f"{v:.1f} s"
    if key == "contract_violations":
        return str(int(v))
    if key == "wv_calibration_err":
        return f"{v:.1f} pts"
    if key == "suspense_entry":
        return f"{v:.1f} pts²"
    return f"{v:.2f}" if key in ("mid_play", "high_leverage_leaves", "low_leverage_leaves") else f"{v:.1f}%"


def _delta(key, now, before) -> str:
    if now is None or before is None:
        return "new" if before is None and now is not None else "–"
    d = now - before
    if abs(d) < 1e-9:
        return "0"
    better = (d > 0) == (META[key]["dir"] > 0)
    return f"{'+' if d > 0 else ''}{d:.2f}{' s' if key in ('switch_p95', 'press_to_tv_p95') else ''} {'🟢' if better else '🔴'}"


def last_run(history: Path, split: str) -> dict | None:
    if not history.exists():
        return None
    last = None
    for line in history.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("split") == split:
            last = r
    return last


ICON = {PASS: "✅ PASS", FAIL: "❌ FAIL", PENDING: "⏳ PENDING", INFO: "ℹ️ INFO"}


def md_table(rows: list[dict], before: dict | None, candidate: list[dict] | None = None,
             cand_name: str = "candidate") -> list[str]:
    """The table; with `candidate` rows (a --profile run) a column with that profile's value, status and
    its change against current, so a flag decision reads the combined numbers."""
    prev = (before or {}).get("values", {})
    cand = {r["key"]: r for r in candidate or []}
    extra_h = f" {cand_name} | {cand_name} status | {cand_name} vs current |" if candidate is not None else ""
    out = ["| metric | target | current | 95% CI (nights) | status | Δ vs last run |" + extra_h + " evidence |",
           "|---|---|---:|---|---|---:|" + ("---:|---|---:|" if candidate is not None else "") + "---|"]
    for r in rows:
        k = r["key"]
        ci = "–" if r["lo"] is None else f"{_fmt(k, r['lo'])} – {_fmt(k, r['hi'])}"
        if r.get("nights"):
            ci += f" ({r['nights']})"
        extra = ""
        if candidate is not None:
            c = cand.get(k) or {"value": None, "status": PENDING}
            extra = f" {_fmt(k, c['value'])} | {ICON[c['status']]} | {_delta(k, c['value'], r['value'])} |"
        out.append(f"| {META[k]['label']} | {META[k]['target']} | {_fmt(k, r['value'])} | {ci} | {ICON[r['status']]} "
                   f"| {_delta(k, r['value'], prev.get(k))} |{extra} {r['detail']} |")
    n = {s: sum(r["status"] == s for r in rows) for s in (PASS, FAIL, PENDING)}
    out.append(f"\n{n[PASS]} pass, {n[FAIL]} fail, {n[PENDING]} pending (pending never counts as passing).")
    return out


def history_row(split: str, rows: list[dict], commit: str, when: str) -> dict:
    return {"ts": when, "commit": commit, "split": split, "eval_data": "eval-data-v1",
            "values": {r["key"]: r["value"] for r in rows}, "status": {r["key"]: r["status"] for r in rows},
            "lo": {r["key"]: r["lo"] for r in rows}}


def load_live_nights(dirs: list[Path]) -> list[dict]:
    """[live nights] Every live night the app collected itself (redzone/supervisor.py
    collect_live_night -> <dir>/<night>/summary.json), oldest first."""
    out = []
    for d in dirs:
        for f in sorted(Path(d).glob("*/summary.json")) if Path(d).is_dir() else []:
            try:
                out.append({"night": f.parent.name, **json.loads(f.read_text())})
            except (OSError, ValueError):
                continue
    return out


def _buf(n: dict) -> str:
    """[STK-06] buffer drift recovered: rebuilt / detected that night (data/buffer.jsonl)."""
    b = n.get("buffer") or {}
    return f"{b.get('recovered', 0)}/{b['detected']}" if b.get("detected") else "–"


def live_section(nights: list[dict]) -> list[str]:
    """Real nights on Nick's TV next to the sims: PENDING until one exists. Not graded yet: a line
    moves to PASS/FAIL only through release_gate.py once there are enough nights."""
    out = ["## Live nights (Nick's TV, logged by the app itself)", ""]
    if not nights:
        return out + ["⏳ PENDING: no live night collected yet (data/live_nights/<night>/summary.json, written "
                      "after each night by the supervisor).", ""]
    out += ["| night | landings (dead ball) | came up into it p50 | before play resumed p50 / min | entries mid-play | "
            "switch p95 | press-to-TV p95 (n) | sync anchor error p95 by stick | buffer drift recovered |",
            "|---|---:|---:|---:|---:|---:|---:|---|---:|"]
    for n in nights[-14:]:
        la = n.get("landings") or {}
        sy = ", ".join(f"{k} {v.get('p95_s')} s (n={v.get('n')})" for k, v in (n.get("sync_anchor_err") or {}).items()) or "–"
        out.append(f"| {n['night']} | {la.get('n', 0)} | {la.get('after_dead_p50_s', '–')} s | "
                   f"{la.get('before_play_p50_s', '–')} / {la.get('before_play_min_s', '–')} s | "
                   f"{n.get('entries_mid_play', '–')} | {n.get('switch_p95_s', '–')} s | "
                   f"{n.get('press_to_tv_p95_s', '–')} s ({n.get('press_samples', 0)}) | {sy} | {_buf(n)} |")
    return out + [""]


def render(results: dict, commit: str, when: str, history: Path, live: list[dict] | None = None) -> str:
    out = ["# North Star", "",
           f"Generated by `python scripts/northstar.py` on `{commit}` at {when} (pinned eval-data-v1, measured feed lag).",
           "Do not edit by hand: the nightly workflow (`.github/workflows/northstar.yml`) rewrites it and appends",
           "`docs/northstar-history.jsonl`. Ranked work against these gaps: [roadmap.md](roadmap.md).", "",
           "PASS needs >= 8 nights, >= 60 decisions and the 95% cluster-bootstrap lower bound at the bar "
           "(scripts/release_gate.py); a point under the bar FAILs; anything else is PENDING.", ""]
    for split, res in results.items():
        title = {"dev": "Dev-test nights (`fit` = train + valid): what PRs are measured on",
                 "locked": "LOCKED unseen nights (`test`): report only, never tuned on"}[split]
        nn = ", ".join(f"{lg.upper()} {len(_per(res['cards'], lg))}" for lg in LEAGUES)
        out += [f"## {title}", "", f"Nights: {nn}.", ""]
        cand = res.get("candidate")
        if cand:
            out += [f"Candidate profile `{cand['profile']}`: {cand.get('about', '')}.", ""]
        out += md_table(res["rows"], last_run(history, split), cand["rows"] if cand else None,
                        cand["profile"] if cand else "candidate") + [""]
    out += live_section(live or [])
    return "\n".join(out)


def delta_md(head: dict, base: dict) -> str:
    """The PR comment section: head vs base on dev nights, same data."""
    hb = {r["key"]: r for r in head["rows"]}
    bb = {r["key"]: r for r in base["rows"]}
    out = ["#### North Star (dev-test nights, measured feed lag): PR vs base", "",
           "| metric | target | base | PR | Δ | status |", "|---|---|---:|---:|---:|---|"]
    moved = []
    for k in META:
        h, b = hb.get(k), bb.get(k)
        if not h:
            continue
        hv, bv = h["value"], (b or {}).get("value")
        d = _delta(k, hv, bv)
        if d not in ("0", "–"):
            moved.append(f"{META[k]['label']} {d}")
        out.append(f"| {META[k]['label']} | {META[k]['target']} | {_fmt(k, bv)} | {_fmt(k, hv)} | {d} | {ICON[h['status']]} |")
    out += ["", "Moved: " + ("; ".join(moved) if moved else "nothing (every North Star line is unchanged)")]
    return "\n".join(out) + "\n"


# ---- roadmap ----------------------------------------------------------------------------------

def gap(key: str, row: dict | None) -> float:
    """Distance to target in the line's unit, from the conservative end of the CI (so PENDING lines that
    need evidence still have a gap). 0 when the line passes."""
    m = META[key]
    if not row or m["bar"] is None or row["status"] == PASS:
        return 0.0
    v = row["value"]
    if v is None:
        return m["scale"]          # not measured at all: a whole gap
    if m["dir"] > 0:
        edge = row["lo"] if row["lo"] is not None else v
        return max(0.0, m["bar"] - edge)
    edge = row["hi"] if row["hi"] is not None else v
    return max(0.0, edge - m["bar"])


def rank(items: list[dict], rows: list[dict]) -> list[dict]:
    """score = (gap to target x expected impact) / effort, both normalised by the line's scale; the
    rule-#1 line (mid-play) counts double. Order: the director's `priority` first (the morning re-rank
    wins), then the rest by score; done items last. An item with no metric (process work) scores 0."""
    by = {r["key"]: r for r in rows}
    out = []
    for it in items:
        k = it.get("metric")
        if k is not None and k not in META:
            raise ValueError(f"roadmap item {it['id']}: unknown metric {k!r} (one of {', '.join(META)})")
        if k is None or META[k]["bar"] is None:
            out.append({**it, "gap": None, "score": 0.0})
            continue
        g = gap(k, by.get(k))
        sc = META[k]["scale"]
        impact = min(abs(float(it.get("expected_delta") or 0)), g)
        score = (g / sc) * (impact / sc) / max(0.25, float(it["effort_days"])) * (2.0 if k == "mid_play" else 1.0)
        out.append({**it, "gap": round(g, 2), "score": round(1000 * score, 1)})
    done = [x for x in out if x.get("status") == "done"]
    live = [x for x in out if x.get("status") != "done"]
    pri = sorted((x for x in live if x.get("priority") is not None), key=lambda x: (x["priority"], -x["score"]))
    rest = sorted((x for x in live if x.get("priority") is None), key=lambda x: -x["score"])
    return pri + rest + done


def render_roadmap(src: dict, rows: list[dict], commit: str, when: str) -> str:
    items = rank(src["items"], rows)
    out = ["# Roadmap", "",
           f"Ranked by `python scripts/northstar.py` from [roadmap.json](roadmap.json) against "
           f"[northstar.md](northstar.md) (dev-test nights) on `{commit}` at {when}. Edit roadmap.json, not this file.", "",
           "**Score** = (gap to target × expected impact) / effort, gap and impact in units of the line's scale "
           "(100 points for a rate, 5 per 100 landings for mid-play, 3 s for switch time), ×2 on rule #1 (mid-play); "
           "the gap is measured from the conservative end of the CI, so a PENDING line still has one. "
           "Expected deltas are estimates until the PR measures them. **Order:** the director's priority "
           "(`priority` in roadmap.json, set each morning) first, then everything else by score.", "",
           "| rank | prio | id | work | moves | gap now | expected Δ | effort (days) | owner | status | score |",
           "|---:|---:|---|---|---|---:|---:|---:|---|---|---:|"]
    for i, it in enumerate(items, 1):
        k = it.get("metric")
        lab = META[k]["label"] if k else "– (process)"
        g = "–" if it["gap"] is None else it["gap"]
        out.append(f"| {i if it.get('status') != 'done' else '–'} | {it.get('priority') or ''} | {it['id']} | {it['work']} "
                   f"| {lab} | {g} | {it.get('expected_delta', '–')} | {it['effort_days']} | {it['owner']} "
                   f"| {it['status']} | {it['score']} |")
    out += ["", *src.get("notes", [])]
    return "\n".join(out) + "\n"


# ---- main -------------------------------------------------------------------------------------

def _git(root: Path, *args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT, help="checkout to grade (its own scorecard code runs)")
    ap.add_argument("--split", choices=("dev", "locked", "both"), default="both")
    ap.add_argument("--night-seeds", type=int, default=0, help="replay the e2e fault night this many times")
    ap.add_argument("--night-json", type=Path, action="append", default=[], help="e2e night report(s) to reuse")
    ap.add_argument("--reuse-nights", type=Path, help="take the e2e nights from an earlier --json output")
    ap.add_argument("--switch-log", type=Path, action="append", default=None,
                    help="tv_input.jsonl file(s) with real switches (default: <root>/data/tv_input.jsonl)")
    ap.add_argument("--controls-json", type=Path, help="a scripts/controls_drill.py --json report (press_to_tv_p95)")
    ap.add_argument("--live-dir", type=Path, action="append", default=None,
                    help="folders of live nights (<night>/summary.json); default <root>/data/live_nights")
    ap.add_argument("--controls-seeds", type=int, default=2,
                    help="without --controls-json: run the controls drill this many nights (0 = skip)")
    ap.add_argument("--json", type=Path, help="write the raw results here (PR CI compares two of these)")
    ap.add_argument("--no-write", action="store_true", help="don't touch docs/ (PR CI)")
    ap.add_argument("--delta", nargs=2, type=Path, metavar=("HEAD", "BASE"), help="print the PR delta table")
    ap.add_argument("--md", type=Path, help="with --delta: write the table here")
    ap.add_argument("--profile", default=None,
                    help="also score this Noggin profile (redzone/noggin/profile.py, e.g. candidate) as its own column")
    ap.add_argument("--oracle", action="store_true",
                    help="also solve the hindsight oracle per night (NBA/NHL): the oracle_gap lines")
    a = ap.parse_args(argv)

    if a.delta:
        text = delta_md(*(json.loads(p.read_text())["dev"] for p in a.delta))
        (a.md.write_text(text) if a.md else sys.stdout.write(text))
        return 0

    root = a.root.resolve()
    prof_p = root / "redzone/lab/feed_lag_profile.json"
    prof = json.loads(prof_p.read_text()) if prof_p.exists() else None
    nights = [json.loads(p.read_text()) | {"seed": p.name} for p in a.night_json]
    if a.reuse_nights:
        nights += json.loads(a.reuse_nights.read_text()).get("nights", [])
    if a.night_seeds:
        nights += run_nights(root, a.night_seeds)
    # keep only what the recovery line reads, so the raw file stays small
    nights = [{k: n.get(k) for k in ("seed", "ok", "error", "checks", "faults", "settle_book") if k in n}
              for n in nights]
    drill = json.loads(a.controls_json.read_text()) if a.controls_json and a.controls_json.exists() else None
    if drill is None and not a.delta:
        drill = run_drill(root, a.controls_seeds)
    xs = switch_samples(a.switch_log if a.switch_log is not None else [root / "data/tv_input.jsonl"])
    contracts = load_contracts(root)
    results = {}
    for split in (("dev", "locked") if a.split == "both" else (a.split,)):
        cards = run_cards(root, split, oracle=a.oracle)
        results[split] = {"cards": cards, "rows": table(cards, nights, xs, prof, drill, contracts)}
        if a.profile:
            from redzone.noggin import profile as P
            cc = run_cards(root, split, profile=a.profile, oracle=a.oracle)
            results[split]["candidate"] = {"profile": a.profile, "about": P.PROFILES[a.profile].get("about", ""),
                                           "cards": cc, "rows": table(cc, nights, xs, prof, drill, contracts)}
    live = load_live_nights(a.live_dir if a.live_dir is not None else [root / "data/live_nights"])
    commit = _git(root, "rev-parse", "--short", "HEAD")
    when = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if a.json:
        a.json.write_text(json.dumps({"commit": commit, "ts": when, **results, "nights": nights, "live_nights": live},
                                     indent=1, default=str))
    if not a.no_write:
        MD.write_text(render(results, commit, when, HISTORY, live))
        with HISTORY.open("a") as f:
            for split, res in results.items():
                f.write(json.dumps(history_row(split, res["rows"], commit, when)) + "\n")
        if ROADMAP_SRC.exists() and "dev" in results:
            ROADMAP_MD.write_text(render_roadmap(json.loads(ROADMAP_SRC.read_text()), results["dev"]["rows"], commit, when))
    for split, res in results.items():
        cand = res.get("candidate")
        print(f"\n## {split}\n" + "\n".join(md_table(res["rows"], None, cand["rows"] if cand else None)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
