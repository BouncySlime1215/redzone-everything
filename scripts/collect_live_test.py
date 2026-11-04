#!/usr/bin/env python3
"""Collect a live test's logs into one folder, with a summary the North Star harness reads (cycle 01
plan, task 4: "the sync-probe log and data/tv_input.jsonl upload automatically after the test").

    python scripts/collect_live_test.py --data <mac repo>/data --since 2026-11-04T22:00 \
        --out /mnt/project-files/live-test/2026-11-04

Copies only rows at or after --since (default: the start in /tmp/rz_test_start, else 18 h ago):
  sync_probe.jsonl      recordings/<day>/sync_probe.jsonl of every day in range (the read-only probe, #71)
  tv_input.jsonl        every TV input command + its verify latency (redzone/tv.py)  -> switch p95
  supervisor_cuts.jsonl where each switch landed (enter/leave play/dead)            -> mid-play entries
  traces.jsonl          per-switch timing marks (cut -> picture, flip)              -> press-to-TV
and writes summary.json + summary.md: rows per file, switch p95 (scripts/northstar.switch_samples rule:
verified rows' total_s), mid-play entries, picture-within-3s share.

Private LAN addresses and MACs are masked in the copies, so the folder can be shared (the repo is
public: never commit these files). Read-only on the source: it never edits or deletes the Mac's data.
Feed it to the harness with: python scripts/northstar.py --switch-log <out>/tv_input.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_IP = re.compile(r"\b(?:10|127|172\.(?:1[6-9]|2\d|3[01])|192\.168)(?:\.\d{1,3}){2,3}\b")
_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
TS_KEYS = ("ts", "cut_at", "t", "at", "time")


def redact(line: str) -> str:
    return _MAC.sub("xx:xx:xx:xx:xx:xx", _IP.sub(lambda m: "lan." + m.group(0).rsplit(".", 1)[-1], line))


def _ts(row: dict) -> float | None:
    for k in TS_KEYS:
        v = row.get(k)
        if isinstance(v, (int, float)) and v > 1e9:
            return float(v)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return None


def copy_rows(srcs: list[Path], dst: Path, since: float) -> list[dict]:
    rows, out = [], []
    for p in srcs:
        if not p.exists():
            continue
        with p.open(errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = _ts(r) if isinstance(r, dict) else None
                if t is None or t >= since:
                    rows.append(r)
                    out.append(redact(line.rstrip("\n")))
    if out:
        dst.write_text("\n".join(out) + "\n")
    return rows


def p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(0.95 * len(s) + 0.999999) - 1)], 2)


def summarize(got: dict[str, list[dict]]) -> dict:
    tv = [float(r["total_s"]) for r in got["tv_input.jsonl"]
          if r.get("verified") and isinstance(r.get("total_s"), (int, float))]
    cuts = got["supervisor_cuts.jsonl"]
    tr = got["traces.jsonl"]
    pics = [(r.get("picture_at") or 0) - (r.get("cut_at") or 0) for r in tr if r.get("picture_at") and r.get("cut_at")]
    return {
        "rows": {k: len(v) for k, v in got.items()},
        "switch_samples": len(tv), "switch_p95_s": p95(tv), "switch_max_s": max(tv) if tv else None,
        "tv_unverified": sum(1 for r in got["tv_input.jsonl"] if not r.get("verified")),
        "cuts_graded": len(cuts), "entries_mid_play": sum(1 for r in cuts if r.get("enter") == "play"),
        "leaves_mid_play": sum(1 for r in cuts if r.get("leave") == "play"),
        "picture_within_3s": f"{sum(1 for x in pics if x <= 3)}/{len(pics)}" if pics else None,
        "picture_p95_s": p95(pics),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="the service's data/ folder")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--since", help="ISO time or epoch seconds (default /tmp/rz_test_start, else 18 h ago)")
    a = ap.parse_args(argv)
    if a.since:
        since = float(a.since) if a.since.replace(".", "").isdigit() else datetime.fromisoformat(a.since).timestamp()
    else:
        try:
            since = float(Path("/tmp/rz_test_start").read_text().strip())
        except (OSError, ValueError):
            since = time.time() - 18 * 3600
    a.out.mkdir(parents=True, exist_ok=True)
    day0 = datetime.fromtimestamp(since).date()
    days = [(day0 + timedelta(days=i)).isoformat() for i in range((datetime.now().date() - day0).days + 1)]
    got = {
        "sync_probe.jsonl": copy_rows([a.data / "recordings" / d / "sync_probe.jsonl" for d in days],
                                      a.out / "sync_probe.jsonl", since),
        "tv_input.jsonl": copy_rows([a.data / "tv_input.jsonl"], a.out / "tv_input.jsonl", since),
        "supervisor_cuts.jsonl": copy_rows([a.data / "supervisor_cuts.jsonl"], a.out / "supervisor_cuts.jsonl", since),
        "traces.jsonl": copy_rows([a.data / "traces.jsonl"], a.out / "traces.jsonl", since),
    }
    s = summarize(got)
    s["since"] = datetime.fromtimestamp(since).isoformat(timespec="seconds")
    (a.out / "summary.json").write_text(json.dumps(s, indent=1))
    md = [f"# Live test logs since {s['since']}", "",
          f"- Switches (TV-confirmed): {s['switch_samples']}, p95 {s['switch_p95_s']} s, max {s['switch_max_s']} s, "
          f"unverified {s['tv_unverified']}",
          f"- Cuts graded: {s['cuts_graded']}; entries mid-play {s['entries_mid_play']}, leaves mid-play {s['leaves_mid_play']}",
          f"- Picture within 3 s: {s['picture_within_3s']}; cut -> picture p95 {s['picture_p95_s']} s",
          f"- Rows: {', '.join(f'{k} {v}' for k, v in s['rows'].items())}", ""]
    (a.out / "summary.md").write_text("\n".join(md))
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
