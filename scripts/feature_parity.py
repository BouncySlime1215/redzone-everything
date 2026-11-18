"""Feature-store parity on real nights: the store's features equal the old builders' for the same sampled states.

    python scripts/feature_parity.py                                   # 250 states, seed 0, nba + cbb dev nights (cbb only when its archive has them)
    python scripts/feature_parity.py --states 250 --seed 0 --reader ../redzone-agents/models/value-v1/stage4_reader_v3.py \
        --old-ref 0fb3da9c --json out.json
    python scripts/feature_parity.py golden --reader PATH      # rewrite tests/fixtures/featurestore_value_v3_golden.json

Why: WORKPLAN v2 F-06 "done when": a parity check on 250 states sampled from real nights with a fixed seed shows a max
|difference| below 1e-6 between redzone/featurestore.py and the builders it replaces. Those builders are
  (1) the notebook reader stage4_reader v3 (outside the repo; sha256-checked), feature set value_v3, all 62 columns;
  (2) the stage 6 arm's Stage6Rows as it was before F-06 (read from git at --old-ref), feature set state4;
  (3) the same reader's 4 state columns, feature set state4 through the grader-facts route.
It also rebuilds each state's features from a cut feed (fence.cut_feed at the last row stamped at or before the
moment) and requires them to equal the full game's: the cut-data test on real games, not just the fixtures.

Dev (`fit`) nights only (redzone/lab/splits.py, read only): no locked night is opened. Grader facts come from
scripts/park_ceiling.truth_of, as the stage 5/6 notebooks use them. Prints numbers only (no game data); exit 1 on any
difference at or above --tol. CPU: about a minute per 20 games at background priority.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import subprocess
import sys
import types
from bisect import bisect_right
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if ROOT.as_posix() not in sys.path:
    sys.path.insert(0, ROOT.as_posix())
if (ROOT / "scripts").as_posix() not in sys.path:
    sys.path.insert(0, (ROOT / "scripts").as_posix())

import numpy as np  # noqa: E402

from redzone import featurestore as fs  # noqa: E402
from redzone.lab import fence, splits  # noqa: E402
from redzone.lab.armeval import stage6 as new_stage6  # noqa: E402
from redzone.lab.backtest import Replay  # noqa: E402
from redzone.models import League  # noqa: E402

PARITY_OLD_REF = "0fb3da9c"     # integration/next just before F-06: the arm's own Stage6Rows
PARITY_READER = "../redzone-agents/models/value-v1/stage4_reader_v3.py"
PARITY_LAGS = (0.0, 8.0, 15.0)


def parity_reader(path: Path):
    """The notebook reader module, refused unless its bytes are the v3 reader the models were fitted with."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != fs.FS_NOTEBOOK_READER_SHA256:
        raise SystemExit(f"{path.name}: sha256 {sha[:12]} is not the v3 reader {fs.FS_NOTEBOOK_READER_SHA256[:12]}")
    spec = importlib.util.spec_from_file_location("parity_stage4_reader_v3", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parity_old_stage6(ref: str):
    """The stage 6 arm module as it was at `ref` (its own Stage6Rows), loaded from git without touching the tree."""
    src = subprocess.run(["git", "-C", ROOT.as_posix(), "show", f"{ref}:redzone/lab/armeval/stage6.py"],
                         check=True, capture_output=True, text=True).stdout
    if "featurestore" in src:
        raise SystemExit(f"{ref} already builds Stage6Rows from the feature store: pick the commit before F-06")
    mod = types.ModuleType("parity_old_stage6")
    exec(compile(src, f"{ref}:stage6.py", "exec"), mod.__dict__)
    return mod


def parity_games(league: str, rng: random.Random, cap: int) -> list[Path]:
    d = ROOT / "data" / "archive" / league
    if not d.exists():
        return []
    keep = splits.nights_for(d, "fit") or set()
    games = splits.archive_nights(d)
    files = sorted(p for n in sorted(keep) for g in games.get(n, ()) if (p := d / f"summary_{g}.json").exists())
    rng.shuffle(files)
    return files[:cap]


def parity_lev_at(facts: dict, te: float) -> float:
    lt = [x[0] for x in facts.get("lev") or ()]
    if not lt:
        return 1.0
    k = bisect_right(lt, te) - 1
    return float(facts["lev"][k][1]) if k >= 0 else 1.0


def parity_maxdiff(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    both_nan = np.isnan(a) & np.isnan(b)
    d = np.where(both_nan, 0.0, np.abs(a - b))
    return float(np.nanmax(np.where(np.isnan(d), np.inf, d))) if d.size else 0.0


def parity_run(states: int, seed: int, reader_path: Path, old_ref: str, games_cap: int) -> dict:
    from park_ceiling import truth_of   # scripts/park_ceiling.py: the grader's facts per game
    RD = parity_reader(reader_path)
    old = parity_old_stage6(old_ref)
    rng = random.Random(seed)
    pool = []
    for lg in ("nba", "cbb"):
        for f in parity_games(lg, rng, games_cap):
            r = Replay.load(League(lg), f)
            if r is None or len(r.plays) < 10:
                continue
            pool.append((lg, r, truth_of(r)))
    if not pool:
        raise SystemExit("no dev-night summaries under data/archive/{nba,cbb} (symlink the archive first)")
    worst = {"value_v3_vs_notebook": 0.0, "state4_vs_old_arm": 0.0, "state4_new_arm_vs_old_arm": 0.0,
             "state4_vs_notebook": 0.0, "cut_feed_vs_full_value_v3": 0.0, "cut_feed_vs_full_state4": 0.0}
    cache, per_league, control = {}, {}, 0.0
    s4_cols = [list(RD.FEATURES).index(k) for k in fs.FS_STATE4_NAMES]
    for _ in range(states):
        lg, r, facts = pool[rng.randrange(len(pool))]
        lag = rng.choice(PARITY_LAGS)
        t = rng.uniform(r.wall[0] - 20.0, r.wall[-1] + 20.0)
        te = t - lag
        post = int(int(((r.data.get("header") or {}).get("season") or {}).get("type") or 2) != 2)
        key = id(r)
        if key not in cache:
            cache[key] = (fs.fs_parse(lg, r.data), RD.parse(lg, r.data),
                          fs.fs_parse(lg, {"plays": r.plays}, wall=r.wall, min_rows=1),
                          old.Stage6Rows(lg, r.plays, r.wall), new_stage6.Stage6Rows(lg, r.plays, r.wall))
        P, PR, Pw, old_rows, new_rows = cache[key]
        ours = fs.fs_rows("value_v3", lg, P, [t], facts, post, lag, dtype=np.float32)[0]
        theirs = np.asarray(RD.game_features(lg, PR, [t], facts, post, lag))[0]
        worst["value_v3_vs_notebook"] = max(worst["value_v3_vs_notebook"], parity_maxdiff(ours, theirs))
        s4 = fs.fs_rows("state4", lg, P, [t], facts, post, lag, dtype=np.float32)[0]
        worst["state4_vs_notebook"] = max(worst["state4_vs_notebook"], parity_maxdiff(s4, theirs[s4_cols]))
        lev = parity_lev_at(facts, te)
        o = old_rows.features(te, lev)
        o_vec = [o[k] for k in fs.FS_STATE4_NAMES]
        s4w = fs.fs_rows("state4", lg, Pw, [te], lev=[lev])[0]
        n = new_rows.features(te, lev)
        worst["state4_vs_old_arm"] = max(worst["state4_vs_old_arm"], parity_maxdiff(s4w, o_vec))
        worst["state4_new_arm_vs_old_arm"] = max(worst["state4_new_arm_vs_old_arm"],
                                                 parity_maxdiff([n[k] for k in fs.FS_STATE4_NAMES], o_vec))
        k = max(0, min(bisect_right(r.wall, te) - 1, len(r.plays) - 1))
        if k + 1 >= 2:
            cut = fence.cut_feed(League(lg), r.data, k)
            Pc = fs.fs_parse(lg, cut)
            for name, w in (("value_v3", "cut_feed_vs_full_value_v3"), ("state4", "cut_feed_vs_full_state4")):
                a = fs.fs_rows(name, lg, Pc, [t], facts, post, lag)[0]
                b = fs.fs_rows(name, lg, P, [t], facts, post, lag)[0]
                worst[w] = max(worst[w], parity_maxdiff(a, b))
        # control: the same comparison 30 s later must differ somewhere, or the zeros above prove nothing
        late = fs.fs_rows("value_v3", lg, P, [t + 30.0], facts, post, lag, dtype=np.float32)[0]
        control = max(control, parity_maxdiff(late, theirs))
        per_league[lg] = per_league.get(lg, 0) + 1
    return {"states": states, "seed": seed, "games": len(pool), "states_by_league": per_league, "max_abs_diff": worst,
            "control_30s_shift_max_diff": control,
            "reader_sha256": fs.FS_NOTEBOOK_READER_SHA256[:12], "old_ref": old_ref,
            "feature_sets": {n: fs.fs_tag(n) for n in fs.FS_SETS}}


PARITY_GOLDEN = "tests/fixtures/featurestore_value_v3_golden.json"
PARITY_GOLDEN_GAMES = ("espn_nba_summary_401909860.json", "espn_cbb_summary_401820788.json")
PARITY_GOLDEN_TIMES = 48


def parity_fixture_facts(r) -> dict:
    """Deterministic stand-in grader facts for a fixture game (the CI test reads them back from the golden file):
    leverage from margin and row index, value at scoring rows, big plays at 3s, a high-leverage stretch per close row."""
    lev, v2, big, hl, ph, pa = [], [], [], [], 0, 0
    for i, (w, p) in enumerate(zip(r.wall, r.plays)):
        try:
            h, a = int(p.get("homeScore")), int(p.get("awayScore"))
        except (TypeError, ValueError):
            continue
        pts = max(0, h - ph) + max(0, a - pa)
        ph, pa = h, a
        lev.append([w, round(1.0 + 3.0 / (1.0 + abs(h - a)) + i / 1000.0, 6)])
        if pts:
            v2.append([w, float(pts), 0.0])
        if pts == 3:
            big.append([w, 5.0])
        if abs(h - a) <= 3 and i % 7 == 0:
            hl.append([w - 30.0, w])
    return {"lev": lev, "v2": v2, "big": big, "hl": hl}


def parity_golden(reader_path: Path, out: Path) -> dict:
    """The notebook reader's value_v3 rows on the repo's own fixture games, for the CI parity test (no reader in CI)."""
    RD = parity_reader(reader_path)
    games = []
    for name in PARITY_GOLDEN_GAMES:
        lg = name.split("_")[1]
        r = Replay.load(League(lg), ROOT / "tests" / "fixtures" / name)
        facts = parity_fixture_facts(r)
        times = np.linspace(r.wall[0] - 10.0, r.wall[-1] + 10.0, PARITY_GOLDEN_TIMES).tolist()
        P = RD.parse(lg, r.data)
        rows = []
        for i, t in enumerate(times):
            lag, fx, post = PARITY_LAGS[i % 3], (facts if i % 4 else None), i % 2
            x = np.asarray(RD.game_features(lg, P, [t], fx, post, lag))[0]
            rows.append({"t": t, "lag": lag, "facts": fx is not None, "post": post, "x": [float(v) for v in x]})
        games.append({"file": name, "league": lg, "facts": facts, "rows": rows})
    doc = {"made_by": "scripts/feature_parity.py golden", "reader_sha256": fs.FS_NOTEBOOK_READER_SHA256,
           "features": list(RD.FEATURES), "games": games}
    out.write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    return doc


def parity_main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["golden"]:
        g = argparse.ArgumentParser(description="write the CI golden file from the notebook reader")
        g.add_argument("--reader", default=PARITY_READER)
        g.add_argument("--out", default=PARITY_GOLDEN)
        b = g.parse_args(argv[1:])
        rp, op = Path(b.reader), Path(b.out)
        doc = parity_golden(rp if rp.is_absolute() else ROOT / rp, op if op.is_absolute() else ROOT / op)
        print(f"golden: {sum(len(x['rows']) for x in doc['games'])} rows x {len(doc['features'])} features")
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--states", type=int, default=250)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--games", type=int, default=30, help="at most this many games per league")
    ap.add_argument("--reader", default=PARITY_READER, help="the notebook's stage4_reader v3 (path from the repo root)")
    ap.add_argument("--old-ref", default=PARITY_OLD_REF)
    ap.add_argument("--tol", type=float, default=1e-6)
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    rp = Path(a.reader)
    rp = rp if rp.is_absolute() else ROOT / rp
    res = parity_run(a.states, a.seed, rp, a.old_ref, a.games)
    res["tol"] = a.tol
    res["pass"] = all(v < a.tol for v in res["max_abs_diff"].values()) and res["control_30s_shift_max_diff"] >= a.tol
    out = json.dumps(res, indent=2, sort_keys=True)
    print(out)
    if a.json:
        Path(a.json).write_text(out + "\n")
    print("PARITY " + ("PASS" if res["pass"] else "FAIL"))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(parity_main())
