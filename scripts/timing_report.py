"""[switch timing] Dead-ball detection lag per source and league, and the on-screen lead it leaves.

    python scripts/timing_report.py                                  # data/recordings/*/plays.jsonl
    python scripts/timing_report.py --rec path/to/recordings --json out.json
    python scripts/timing_report.py --delay youtubetv:espn=26.4 --delay peacock:x=48.4
    python scripts/timing_report.py --write-profile                  # refresh redzone/lab/feed_lag_profile.json

Read-only on the recordings and data/redzone.db. See redzone/lab/timing.py for the definitions.
Why: Dead-ball detection lag per source and league (docstring, timing-sim commit, memory 2026-11-04).
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import timing  # noqa: E402


def db_delays(db: Path) -> dict[str, float]:
    """Latest measured stream delay per channel (taps and show changes; set-aside taps skipped)."""
    if not db.exists():
        return {}
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = c.execute("select target, sample, kind from calibrations order by ts").fetchall()
    except sqlite3.Error:
        return {}
    return {t: float(s) for t, s, k in rows if k in ("tap", "boundary", "scorebug")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rec", type=Path, default=ROOT / "data/recordings")
    ap.add_argument("--db", type=Path, default=ROOT / "data/redzone.db")
    ap.add_argument("--delay", action="append", default=[], metavar="CHANNEL=SECONDS")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--write-profile", action="store_true")
    a = ap.parse_args(argv)
    files = sorted(a.rec.glob("*/plays.jsonl"))
    if not files:
        print(f"no recordings under {a.rec}")
        return 2
    days = [f.parent.name for f in files]
    rows = timing.load_rows(files)
    rep = timing.detection_report(rows, days)
    delays = db_delays(a.db)
    for kv in a.delay:
        k, _, v = kv.partition("=")
        delays[k] = float(v)
    out = rep.to_json()
    out["delays"] = delays
    out["lead"] = timing.lead_table(rep, delays)
    print(f"recordings {days[0]}..{days[-1]}: {rep.kept}/{rep.rows} rows while tracking")
    print(f"{'league':6} {'source':14} {'dead balls':>10} {'p10':>6} {'p50':>6} {'p90':>6} {'<=3s':>6}  newest-in-batch p50  poll gap p50")
    for lg, srcs in sorted(rep.by_league.items()):
        for src, s in srcs.items():
            d = s["dead_ball"]
            if not d.get("n"):
                continue
            print(f"{lg:6} {src:14} {d['n']:>10} {d['p10']:>6} {d['p50']:>6} {d['p90']:>6} {d['within_3s_pct']:>5}%"
                  f"  {s['newest_in_batch'].get('p50', '-'):>19}  {s['poll_gap_s'].get('p50', '-'):>12}")
    if out["lead"]:
        print("\non-screen lead = stream delay - dead-ball detection lag (negative: it aired before we knew)")
        for k, v in out["lead"].items():
            print(f"  {k:40} delay {v['delay_s']:>5}  lead at lag p10 {v['lead_p10_lag']:>6}  p50 {v['lead_median_lag']:>6}  p90 {v['lead_p90_lag']:>6}")
    races = sorted(a.rec.glob("*/race.jsonl"))
    if races:
        league_of = {r.game: r.league for r in rows}
        out["race"] = timing.race_report(races, league_of)
        print("\nsource race (A2): first = reported it first; gain = seconds ahead of the fastest ESPN copy")
        for lg, kinds in sorted(out["race"].items()):
            for kind, b in sorted(kinds.items()):
                for src, d in sorted(b["sources"].items(), key=lambda kv: -kv[1]["first"]):
                    g = d["gain_s"]
                    gain = f"p10 {g['p10']:>6} p50 {g['p50']:>6} p90 {g['p90']:>6} (n {g['n']})" if g["n"] else "-"
                    print(f"  {lg:5} {kind:5} {src:16} first {d['first']:>5}/{b['n']:<5} ({d['first_pct']:>5}%)  gain {gain}")
    out["fastest"] = timing.fastest_feeds(rep, out.get("race"))
    if out["fastest"]:
        print("\nfastest feed per league (sync 5): lowest dead-ball lag p50; race = first on stoppages")
        for lg, row in sorted(out["fastest"].items()):
            lag, race = row.get("by_lag"), row.get("by_race")
            txt = f"lag {lag['source']} p50 {lag['p50']} s (n {lag['n']})" if lag else "lag -"
            txt += f"   race {race['source']} first {race['first_pct']}% (n {race['n']})" if race else ""
            print(f"  {lg:6} {txt}")
    if a.json:
        a.json.write_text(json.dumps(out, indent=1))
    if a.write_profile:
        prof = timing.build_profile(rows, days)
        # [sim lag in use] the faster feeds' own profiles: measured once the recorder has their rows,
        # else the derived ones already in the file are kept (see "note" there).
        old = timing.load_profile().get("sources") or {}
        prof["sources"] = dict(old)
        for name, (src, lg) in {"nhl_wsc": ("nhl", "nhl"), "nba_cdn": ("nba_cdn", "nba")}.items():
            got = timing.build_profile(rows, days, source=src)["leagues"].get(lg)
            if got:
                prof["sources"][name] = {lg: got}
        timing.PROFILE_PATH.write_text(json.dumps(prof, indent=1) + "\n")
        print(f"wrote {timing.PROFILE_PATH.relative_to(ROOT)} ({', '.join(prof['leagues'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
