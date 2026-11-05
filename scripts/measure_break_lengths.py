"""[Data F] Measured break lengths by league x network x break kind, from the pinned archive.

    python scripts/fetch_eval_data.py                     # once
    python scripts/measure_break_lengths.py               # writes redzone/scoring/broadcast/break_lengths.json
    python scripts/measure_break_lengths.py --split valid # the EXP-1500 read (dev only, never test)

A break row = a feed stoppage (espn.stoppage_for) to the real restart (cutgrade.resume_after),
the same definition as breaks.LEN. Network = the game's national broadcast if it has one, else
"local" (ESPN's broadcasts field). Only TRAIN nights feed the table (redzone.lab.splits); valid
nights are for grading it; the test (locked) nights are never read.
License: derived numbers from ESPN game summaries (personal-use under data plan B, not for
commercial release); the wallclock facts are ours to aggregate, no raw rows are published.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import cutgrade, splits  # noqa: E402
from redzone.lab.backtest import Replay  # noqa: E402
from redzone.models import League, Stoppage  # noqa: E402
from redzone.sources import espn  # noqa: E402

OUT = ROOT / "redzone" / "scoring" / "broadcast" / "break_lengths.json"
QS = (0.1, 0.25, 0.5, 0.75, 0.9)
MIN_N = 8            # fewer rows than this: the cell falls back to the league x kind row
MAX_LEN_S = 1500.0   # longer than this is a delay / feed gap, not a break


def network(event: dict) -> str:
    comp = (event.get("competitions") or [{}])[0]
    names, national = espn._broadcasts(comp)
    if not national:
        return "local"
    for n in names:   # first national name, normalised
        return n.upper().replace(" ", "")
    return "national"


def rows(league: League, which: str) -> list[dict]:
    arch = ROOT / "data" / "archive" / league.value
    sp = splits.league_split(league.value, splits.archive_nights(arch))
    want = set(sp.train) if which == "train" else set(sp.valid)
    out = []
    for sb in sorted(arch.glob("scoreboard_*.json")):
        night = sb.stem.split("_")[-1]
        if night not in want:
            continue
        for e in json.loads(sb.read_text()).get("events", []):
            f = arch / f"summary_{e['id']}.json"
            r = Replay.load(league, f) if f.exists() else None
            if not r:
                continue
            net, prev = network(e), Stoppage.NONE
            for i, p in enumerate(r.plays):
                k = espn.stoppage_for(league, p)
                if k not in (Stoppage.NONE, Stoppage.UNKNOWN) and k != prev:
                    res = cutgrade.resume_after(league, r.wall, r.plays, i)
                    if res != float("inf") and r.wall[i] is not None and 0 < res - r.wall[i] <= MAX_LEN_S:
                        out.append({"league": league.value, "night": night, "game": e["id"], "network": net,
                                    "kind": k.value, "len_s": round(res - r.wall[i], 1)})
                prev = k
    return out


def _q(v: list[float]) -> dict:
    v = sorted(v)
    n = len(v)
    return {"n": n, **{f"q{int(q * 100)}": v[min(n - 1, int(q * n))] for q in QS}}


def table(rs: list[dict]) -> dict:
    cells: dict = defaultdict(list)
    for x in rs:
        cells[(x["league"], x["kind"], "*")].append(x["len_s"])
        cells[(x["league"], x["kind"], x["network"])].append(x["len_s"])
    out: dict = {}
    for (lg, kind, net), v in sorted(cells.items()):
        if net != "*" and len(v) < MIN_N:
            continue
        out.setdefault(lg, {}).setdefault(kind, {})[net] = _q(v)
    return out


def predict(tab: dict, league: str, kind: str, net: str, per_network: bool = True) -> float | None:
    k = tab.get(league, {}).get(kind, {})
    cell = (k.get(net) if per_network else None) or k.get("*")
    return cell["q50"] if cell else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=["nfl", "nba", "nhl"])
    ap.add_argument("--split", choices=["train", "valid"], default="train")
    a = ap.parse_args()
    train = [x for lg in a.leagues for x in rows(League(lg), "train")]
    tab = table(train)
    if a.split == "train":
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"source": "eval-data-v1 archive, train nights only (redzone.lab.splits)",
                                   "license": "derived from ESPN summaries; personal-use (plan B), not for commercial release",
                                   "definition": "feed stoppage to real restart (cutgrade.resume_after), seconds",
                                   "min_n_per_network": MIN_N, "table": tab}, indent=1))
        print(f"wrote {OUT} from {len(train)} train breaks")
        return 0
    # EXP-1500 read: per-network median vs league-only median on valid nights
    val = [x for lg in a.leagues for x in rows(League(lg), "valid")]
    rep = {}
    for lg in a.leagues:
        v = [x for x in val if x["league"] == lg]
        errs = defaultdict(lambda: [[], []])
        for x in v:
            pn, pl = predict(tab, lg, x["kind"], x["network"]), predict(tab, lg, x["kind"], x["network"], False)
            if pn is None or pl is None:
                continue
            errs[x["night"]][0].append(abs(pn - x["len_s"]))
            errs[x["night"]][1].append(abs(pl - x["len_s"]))
        nights = sorted(errs)
        a_n = sum(sum(e[0]) for e in errs.values())
        a_l = sum(sum(e[1]) for e in errs.values())
        cnt = sum(len(e[0]) for e in errs.values())
        import random
        rnd, diffs = random.Random(0), []
        for _ in range(2000):
            pick = [rnd.choice(nights) for _ in nights]
            n_ = sum(sum(errs[k][0]) for k in pick)
            l_ = sum(sum(errs[k][1]) for k in pick)
            diffs.append((n_ - l_) / max(1e-9, l_))
        diffs.sort()
        rep[lg] = {"breaks": cnt, "nights": len(nights), "mae_network": round(a_n / max(1, cnt), 2),
                   "mae_league": round(a_l / max(1, cnt), 2),
                   "rel_change": round((a_n - a_l) / max(1e-9, a_l), 4),
                   "ci95": [round(diffs[50], 4), round(diffs[1949], 4)]}
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
