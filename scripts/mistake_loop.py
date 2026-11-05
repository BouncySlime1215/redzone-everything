"""[WV loop] Forward-chained mistake loop over FIT nights (train + valid), oldest first. Per night:
  1. FIRST PASS: replay the current brain (backtest.run) and grade it against the windowed oracle (% of oracle,
     combined objective). That score is the only evidence and is logged before anything else touches the night.
  2. Mistake ledger: every step the app showed vs the windowed oracle, value lost by cause.
  3. The night joins the training pool (ledger rows + weights) for the next arm.
Locked (test) nights are never read: the split comes from splits.league_split and forward_chain raises on a leak.
Runs where the pinned eval archive lives (CI / Kaggle), never on the Mac. Output: derived numbers only.
personal-use, not for commercial release."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))

from redzone.config import Config  # noqa: E402
from redzone.lab import forward_chain as FC, mistake_ledger as ML, oracle as O, oracle_window as OW, splits  # noqa: E402
from redzone.lab.backtest import feed_lag_knobs, run  # noqa: E402
from redzone.models import League  # noqa: E402


def main(argv=None) -> int:
    import eval_sports as ES
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="nba")
    ap.add_argument("--buffer", type=float, default=90.0)
    ap.add_argument("--feed-lag", type=float, default=None, help="s; default PK.FEED_LAG_P90 via buffer_sweep")
    ap.add_argument("--max-nights", type=int, default=0)
    ap.add_argument("--oracle", choices=("window", "full"), default="window",
                    help="target: the windowed oracle (buffered shows) or full hindsight. With the v1 past-mean beyond value the "
                         "windowed oracle can score BELOW the app (Nov 5 run), so it is not yet a ceiling")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    lg = League(a.league)
    import buffer_sweep as BS
    fl = a.feed_lag if a.feed_lag is not None else BS.feed_lag_for(a.league, "p90")
    arch = ROOT / "data/archive" / a.league
    sp = splits.league_split(a.league, splits.archive_nights(arch, None))
    locked = set(sp.test)
    cfg = Config(user_path=ROOT / "data/eval_config.yaml")
    mine = {"WSH", "UNC"}
    a.out.mkdir(parents=True, exist_ok=True)
    cache: dict[str, tuple] = {}
    all_rows: list = []

    def night_data(day):
        if day not in cache:
            reps, targets = next((r, t) for d, r, t in ES.nights(lg, 10_000, None, "fit") if d == day)
            rep = run(reps, cfg, mine, targets=targets, **feed_lag_knobs(fl, a.buffer))
            n = O.Night(reps, mine, rep.lag_true, 5.0, rep.t0, rep.t1)
            steps = np.arange(n.t0, n.t1, O.STEP_S)
            tab = O.tables(n, steps)
            cache[day] = (reps, rep, n, steps, tab)
        return cache[day]

    def score(day):
        reps, rep, n, steps, tab = night_data(day)
        orc = OW.solve_window(n, fl, "combined", tab) if a.oracle == "window" else O.solve(n, "combined", tab)
        v_app = O.schedule_value(n, tab, "combined", rep.switches)
        v_orc = O.schedule_value(n, tab, "combined", orc)
        cache[day] += (orc,)
        return {"app_value": round(v_app, 3), "oracle_value": round(v_orc, 3),
                "oracle": a.oracle, "pct_of_oracle": round(100 * v_app / v_orc, 1) if v_orc > 0 else None, "games": len(reps)}

    def analyze(day):
        reps, rep, n, steps, tab, orc = cache[day]
        wb, wc, wm, wd, ws = O.OBJECTIVES["combined"]
        R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
        gids = [r.gid for r in reps]; gi = {g: i for i, g in enumerate(gids)}
        blk = max(1, int(math.ceil(n.tune_s / O.STEP_S)))
        K = len(steps)
        app_on = ML.on_screen(K, n.t0, O.STEP_S, rep.switches, gi, blk)
        orc_on = ML.on_screen(K, n.t0, O.STEP_S, orc, gi, blk)
        sw = lambda ss: {int(round((s.t - n.t0) / O.STEP_S)) for s in ss if s.frm is not None}
        rows = ML.ledger(day, a.league, R, tab, gids, app_on, orc_on, n.t0, O.STEP_S, locked=locked,
                         app_switch_steps=sw(rep.switches), oracle_switch_steps=sw(orc), blk=blk)
        (a.out / f"ledger_{a.league}_{a.oracle}_{day}.jsonl").write_text(ML.to_jsonl(rows))
        all_rows.extend(rows)
        cache.pop(day, None)

    ch = FC.Chain(locked=locked)
    fit_nights = sorted(sp.train + sp.valid)
    if a.max_nights:
        fit_nights = fit_nights[:a.max_nights]
    ch.run(fit_nights, score, analyze, lambda seen: None)   # arm hook: the next arm trains on seen + ledger weights
    (a.out / f"first_pass_{a.league}_{a.oracle}.jsonl").write_text(ch.jsonl())
    summ = {"league": a.league, "oracle": a.oracle, "buffer_s": a.buffer, "feed_lag_s": fl, "nights": len(ch.log),
            "causes": ML.summarize(all_rows), "locked_nights_read": 0,
            "tag": "personal-use, not for commercial release"}
    (a.out / f"summary_{a.league}_{a.oracle}.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
