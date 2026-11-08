"""Brain vs the hand-written heuristics, through the live code path, on our own recorded ESPN games
(data/archive: after every training game).

    .venv/bin/python scripts/eval_brain.py               # nfl nba nhl mlb
    .venv/bin/python scripts/eval_brain.py nba --games 30
    .venv/bin/python scripts/eval_brain.py --save        # also store it in brain_models/metrics.json
Why: Compares the learned brain with the hand-written heuristics on recorded games (docstring, learned brain commit).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import brainfit  # noqa: E402
from redzone.scoring import brain  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=["nfl", "nba", "nhl", "mlb"])
    ap.add_argument("--games", type=int, default=None)
    ap.add_argument("--weight", type=float, default=0.5, help="brain share of the blended break forecast")
    ap.add_argument("--save", action="store_true", help="store the result in brain_models/metrics.json")
    a = ap.parse_args()
    path = brain.MODEL_DIR / "metrics.json"
    reports = json.loads(path.read_text()) if path.exists() else {}
    print("league games rows | break AUC rules/brain/blend | Brier rules/brain/blend | "
          "key AUC rules/brain | flow rho rules/brain | ms/state")
    for lg in a.leagues:
        r = brainfit.live_eval(lg, max_games=a.games, weight=a.weight)
        if not r.get("games"):
            print(f"{lg:5s} no archive")
            continue
        b, k, h = r["break"], r["key"], r["hot"]
        print(f"{lg:5s} {r['games']:4d} {r['rows']:7d} | "
              f"{b['rules']['auc']:.3f}/{b['brain']['auc']:.3f}/{b['blend']['auc']:.3f} | "
              f"{b['rules']['brier']:.3f}/{b['brain']['brier']:.3f}/{b['blend']['brier']:.3f} | "
              f"{k['rules_auc']:.3f}/{k['brain']['auc']:.3f} | "
              f"{h['rules_spearman']:.3f}/{h['brain']['spearman']:.3f} | {r['brain_ms_per_state']:.2f}", flush=True)
        if a.save:
            reports.setdefault(lg, {})["live"] = r
    if a.save:
        brainfit.write_metrics(reports, path)


if __name__ == "__main__":
    main()
