"""[auto sync] Sync-error scorecard: the delay the app uses vs the screen's real delay.

    .venv/bin/python scripts/eval_sync.py                 # every night in data/recordings, sync on vs off
    .venv/bin/python scripts/eval_sync.py --seeds 5
    .venv/bin/python scripts/eval_sync.py --log data/sync.jsonl   # a real night's filter log, summarized

Feed timing is real (recorded nights); the screen side is simulated (see redzone/lab/syncsim.py)
until data/sync.jsonl holds real nights. Target: error under 2 s, interval misses ~0, synced within
2 minutes of every tune.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab.syncsim import Card, log_summary, nights, simulate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recordings", type=Path, default=ROOT / "data/recordings")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--settle", type=float, default=120.0, help="grade from this long after each tune")
    ap.add_argument("--log", type=Path, default=None)
    a = ap.parse_args()
    if a.log:
        print(json.dumps(log_summary(a.log), indent=1))
        return
    ns = [(n, g) for n, g in nights(a.recordings) if g]
    if not ns:
        sys.exit(f"no recorded nights in {a.recordings}")
    out = {"nights": [n for n, _ in ns], "seeds": a.seeds}
    rows = (("before (no sync)", dict(sync=False)),
            ("sync, score bug matched only at the change", dict(sync=True, defer=False)),
            ("after (sync)", dict(sync=True)),
            ("after (sync) + hidden stick holds each game from before its start", dict(sync=True, preload=True)))
    for name, kw in rows:
        card = Card()
        for seed in range(a.seeds):
            for _, games in ns:
                simulate(games, seed=seed, settle_s=a.settle, card=card, **kw)
        out[name] = card.summary()
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
