"""Scorecards with the live game reasoner on, cross-fitted: each night is scored by a head that
never saw that night (data/training/reasoner_folds, from scripts/train_reasoner.py).

    python scripts/eval_reasoner.py nfl                       # NFL scorecard (scripts/eval.py), reasoner on
    python scripts/eval_reasoner.py nba --set reasoner.nba.must_points=4
    python scripts/eval_reasoner.py nhl --shipped             # the shipped head (in-sample: don't report)
    python scripts/eval_reasoner.py nfl --off                 # same harness, reasoner off (= the baseline)

Every other setting is data/eval_config.yaml, exactly as the normal scorecards read it.
"""
import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import config as C  # noqa: E402
from redzone.lab.reasoner_data import fold_of, nights_of  # noqa: E402
from redzone.reasoner import head as H  # noqa: E402
from redzone.reasoner import reasoner as R  # noqa: E402

FOLDS = ROOT / "data/training/reasoner_folds"


class FoldHead(H.Head):
    """Picks, per game, the fold model whose training never saw that game's night."""

    def __init__(self):
        super().__init__(FOLDS)
        self.night = {}
        for lg in ("nfl", "nba", "nhl", "mlb"):
            self.night.update({(lg, g): n for g, n in nights_of(ROOT / "data/archive" / lg).items()})
        self._folds: dict[str, H.Head] = {}
        self.gid = None

    def _sub(self, league: str) -> H.Head | None:
        base = H.brief.BASE.get(league, league)
        n = self.night.get((base, self.gid))
        if n is None:
            return None
        import json

        import numpy as np
        k = None
        for p in sorted(FOLDS.glob(f"{base}_f*.npz")):
            with np.load(p, allow_pickle=False) as z:
                info = json.loads(bytes(z["meta"]).decode())["info"]
            k = info["k"]
            break
        if k is None:
            return None
        f = fold_of(n, k)
        key = f"{base}_f{f}"
        if key not in self._folds:
            hd = H.Head(FOLDS)
            hd._m[league] = hd.model(key)
            self._folds[key] = hd
        return self._folds[key]

    def odds(self, league, facts):
        sub = self._sub(league)
        return sub.odds(league, facts) if sub else None

    def cuts(self, league):
        sub = self._sub(league)
        return sub.cuts(league) if sub else (1.0, 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--shipped", action="store_true")
    ap.add_argument("--set", action="append", default=[], help="dotted.key=value (numbers/true/false)")
    a, rest = ap.parse_known_args()
    over = {"reasoner.enabled": not a.off}
    for kv in a.set:
        k, v = kv.split("=", 1)
        over[k] = {"true": True, "false": False}.get(v.lower(), None)
        if over[k] is None:
            try:
                over[k] = float(v)
            except ValueError:
                over[k] = v
    init = C.Config.__init__

    def patched(self, *args, **kw):
        init(self, *args, **kw)
        for k, v in over.items():
            node = self.data
            parts = k.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = v
    C.Config.__init__ = patched

    if not a.shipped:
        fh = FoldHead()
        orig_read = R.Reasoner.read

        def read(self, g, row, reading=None, ctx=None):
            if g is not None:
                fh.gid = g.game_id
            return orig_read(self, g, row, reading, ctx)
        R.Reasoner.read = read
        orig_init = R.Reasoner.__init__

        def rinit(self, cfg=None, head=None, client=None, clock=None):
            import time
            orig_init(self, cfg, fh, client, clock or time.time)
        R.Reasoner.__init__ = rinit

    if a.league == "nfl":
        sys.argv = ["eval.py", *rest]
        runpy.run_path(str(ROOT / "scripts/eval.py"), run_name="__main__")
    else:
        sys.argv = ["eval_sports.py", a.league, *rest]
        runpy.run_path(str(ROOT / "scripts/eval_sports.py"), run_name="__main__")


if __name__ == "__main__":
    main()
