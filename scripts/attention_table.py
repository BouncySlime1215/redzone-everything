#!/usr/bin/env python3
"""Data G: the joined attention-label table (Nick 11:30 AM Nov 5): one row per play, every engagement source
joined by team and time, plus the WP swing label. Labels and evaluation only, never model inputs.

    python scripts/attention_table.py nba nfl --spec wiki_edits=spec_edits.json --spec bsky=spec_bsky.json \
        --out /mnt/project-files/attention-g/table

Each --spec is the same JSON the trainer's --aux-source takes (scripts/attention_labels.load_spec). For each play
and source, the cell is the RAW window value (max team z over the source's window after the play), NaN when the
source does not cover it. No ranking happens here: that stays in the frozen label code (EXP-1704).

Output: <out>/<league>.parquet (private, never committed: the repo is public) and <out>/counts.json, which has
labeled moments per source and split, the numbers the planner's 12 / 3 / 6 PM tables carry.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import attention_labels as AL  # noqa: E402

TAG = "personal-use, not for commercial release"


def league_table(lg: str, archive: Path, cfg, specs: dict[str, list]) -> tuple[list[dict], dict]:
    import train_watch_value as TW
    from redzone.lab import reasoner_data as RD
    from redzone.lab import splits
    from redzone.models import League
    rows = [r.as_dict() for r in RD.league_rows(League(lg), archive / lg, cfg, {"WSH", "UNC"}, every=1)]
    horizon = float(cfg.get("scoring.watch_horizon_s")[lg])
    by_game: dict = {}
    for k, r in enumerate(rows):
        by_game.setdefault(r["gid"], []).append(k)
    _split = getattr(splits, "league_split", None)
    nights = {r["night"] for r in rows}
    sp = _split(lg, nights) if _split else splits.split_nights(nights)
    part = {n: s for s, ns in (("train", sp.train), ("valid", sp.valid), ("test", sp.test)) for n in ns}
    out = []
    for gid, ks in by_game.items():
        g = [rows[k] for k in ks]
        wv, pb = TW.labels(g, horizon)
        teams = TW._teams(g[0].get("matchup", ""))
        cols = {name: AL.play_attention(g, teams, series) for name, series in specs.items()}
        for j, r in enumerate(g):
            rec = {"league": lg, "night": r["night"], "split": part[r["night"]], "gid": gid, "i": r["i"],
                   "wall": r.get("wall"), "wp_home": r.get("wp_home"), "wv_swing": float(wv[j]),
                   "big_play_next": float(pb[j]), "teams": "/".join(teams), "license": TAG}
            for name, v in cols.items():
                rec[f"{name}_z"] = float(v[j]) if np.isfinite(v[j]) else None
            out.append(rec)
    counts = {"plays": len(out), "games": len(by_game),
              "by_split": {s: sum(1 for x in out if x["split"] == s) for s in ("train", "valid", "test")},
              "wp_swing_labeled": sum(1 for x in out if x["wp_home"] is not None)}
    for name in specs:
        c = [x for x in out if x[f"{name}_z"] is not None]
        counts[name] = {"labeled": len(c), "games": len({x["gid"] for x in c}),
                        "by_split": {s: sum(1 for x in c if x["split"] == s) for s in ("train", "valid", "test")}}
    return out, counts


def main(argv=None) -> int:
    from redzone.config import Config
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="+")
    ap.add_argument("--archive", type=Path, default=ROOT / "data" / "archive")
    ap.add_argument("--config", type=Path, default=ROOT / "data" / "eval_config.yaml")
    ap.add_argument("--spec", action="append", default=[], help="name=spec.json (one per source)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    specs = {}
    for s in a.spec:
        name, path = s.split("=", 1)
        specs[name] = AL.load_spec(json.loads(Path(path).read_text()))
    text = (a.config.read_text() if a.config.exists() else "") + "\nbrain:\n  enabled: true\n"
    tmp = Path(tempfile.mkdtemp()) / "cfg.yaml"
    tmp.write_text(text)
    cfg = Config(user_path=tmp)
    a.out.mkdir(parents=True, exist_ok=True)
    allc = {}
    import pandas as pd
    for lg in a.leagues:
        rows, counts = league_table(lg, a.archive, cfg, specs)
        pd.DataFrame(rows).to_parquet(a.out / f"{lg}.parquet", index=False)
        allc[lg] = counts
    (a.out / "counts.json").write_text(json.dumps(allc, indent=1) + "\n")
    print(json.dumps(allc, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
