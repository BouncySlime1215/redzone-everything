#!/usr/bin/env python3
"""Publish live-night summaries into the repo so the North Star workflow grades real nights
(cycle 04, lane 4: "live nights into northstar").

    python scripts/publish_live_nights.py /mnt/project-files/live-nights        # copies of the Mac's data/live_nights
    python scripts/publish_live_nights.py <mac repo>/data/live_nights --check   # what would be published, no writes

The Mac's supervisor writes data/live_nights/<night>/summary.json after each night (#118). That folder
never reaches GitHub, so northstar.yml (which runs on a runner) would show "Live nights: PENDING"
forever. This copies ONLY each summary.json, reduced to an allowlist of aggregate numbers, into
docs/live-nights/<night>/summary.json, which northstar.yml reads with --live-dir docs/live-nights.

The repo is public: the raw rows (tv_input, traces, sync, cuts) are never copied, only counts and
percentiles, and any string value that looks like a LAN address, MAC or path is refused. A night
already published is left alone unless --force.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "live-nights"
NIGHT = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Aggregate fields from scripts/collect_live_test.summarize. Anything else is dropped.
NUM_KEYS = ("switch_samples", "switch_p95_s", "switch_max_s", "tv_unverified", "cuts_graded",
            "entries_mid_play", "leaves_mid_play", "picture_p95_s", "press_samples", "press_to_tv_p95_s",
            "press_no_picture", "press_to_tv_p50_s", "landings_dead", "landings_graded", "switch_p50_s")
STR_KEYS = {"picture_within_3s": re.compile(r"^\d+/\d+$"),
            "since": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$"),
            "until": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")}
LANDING_KEYS = ("n", "after_dead_p50_s", "before_play_min_s", "before_play_p50_s")
STICK = re.compile(r"^[A-Za-z0-9_-]{1,16}$")      # HDMI1 / HDMI2 / unknown
ANCHOR_KEYS = ("n", "p50_s", "p95_s")
# [night health] a miss: when (ET HH:MM), which game (a matchup label), which end, the grade's reason.
MISS_PATS = {"at": re.compile(r"^\d{2}:\d{2}$"), "game": re.compile(r"^[A-Za-z0-9 @.&'()/:_-]{1,48}$"),
             "end": re.compile(r"^(enter|leave)$"), "why": re.compile(r"^[A-Za-z0-9 _.,:;()/+%<>=~-]{0,100}$")}
RULE = re.compile(r"^[a-z0-9_.-]{1,40}$")
IPLIKE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def clean(summary: dict) -> dict:
    """The publishable part of one summary.json: aggregate numbers only."""
    out: dict = {}
    for k in NUM_KEYS:
        if _num(summary.get(k)) is not None:
            out[k] = summary[k]
    for k, pat in STR_KEYS.items():
        v = summary.get(k)
        if isinstance(v, str) and pat.match(v):
            out[k] = v
    la = summary.get("landings")
    if isinstance(la, dict):
        out["landings"] = {k: la[k] for k in LANDING_KEYS if k in la and (la[k] is None or _num(la[k]) is not None)}
    sy = summary.get("sync_anchor_err")
    if isinstance(sy, dict):
        out["sync_anchor_err"] = {
            str(st): {k: v[k] for k in ANCHOR_KEYS if _num(v.get(k)) is not None}
            for st, v in sy.items() if STICK.match(str(st)) and isinstance(v, dict)}
    ms = summary.get("misses")
    if isinstance(ms, list):
        keep = []
        for m in ms[:50]:
            if isinstance(m, dict):
                c = {k: str(m.get(k) or "") for k in MISS_PATS}
                c = {k: (v if MISS_PATS[k].match(v) and not IPLIKE.search(v) else "?") for k, v in c.items()}
                keep.append(c)
        out["misses"] = keep
    ff = summary.get("faults_fixed")
    if isinstance(ff, dict):
        out["faults_fixed"] = {str(r): {k: v[k] for k in ("n", "failed") if _num(v.get(k)) is not None}
                               for r, v in ff.items() if RULE.match(str(r)) and isinstance(v, dict)}
    ps = summary.get("press_spans")
    if isinstance(ps, dict):
        out["press_spans"] = {str(k): {kk: v[kk] for kk in ANCHOR_KEYS if _num(v.get(kk)) is not None}
                              for k, v in ps.items() if RULE.match(str(k)) and isinstance(v, dict)}
    bf = summary.get("buffer")     # [STK-06] hidden-stick buffer watchdog counts (#136)
    if isinstance(bf, dict):
        out["buffer"] = {k: bf[k] for k in ("detected", "lost", "recovered", "rebuild_failed", "err_p95_s")
                         if _num(bf.get(k)) is not None}
    rows = summary.get("rows")
    if isinstance(rows, dict):
        out["rows"] = {str(k): v for k, v in rows.items()
                       if re.match(r"^[a-z_]+\.jsonl$", str(k)) and _num(v) is not None}
    return out


def find(srcs: list[Path]) -> dict[str, Path]:
    """night -> its summary.json, from folders laid out <night>/summary.json (newest copy wins)."""
    got: dict[str, Path] = {}
    for s in srcs:
        for f in sorted(s.glob("*/summary.json")) if s.is_dir() else []:
            if NIGHT.match(f.parent.name):
                got[f.parent.name] = f
    return got


def publish(srcs: list[Path], out: Path = OUT, force: bool = False, check: bool = False) -> list[dict]:
    log = []
    for night, f in sorted(find(srcs).items()):
        dst = out / night / "summary.json"
        if dst.exists() and not force:
            log.append({"night": night, "status": "already published"})
            continue
        try:
            s = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            log.append({"night": night, "status": f"unreadable: {e}"})
            continue
        c = clean(s if isinstance(s, dict) else {})
        if not c:
            log.append({"night": night, "status": "empty after cleaning"})
            continue
        if not check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(c, indent=1, sort_keys=True) + "\n")
        log.append({"night": night, "status": "would publish" if check else "published",
                    "dropped": sorted(set(s) - set(c)) if isinstance(s, dict) else []})
    return log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, nargs="+", help="folder(s) of <night>/summary.json")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--force", action="store_true", help="replace a night already published")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    a = ap.parse_args(argv)
    log = publish(a.src, a.out, a.force, a.check)
    for x in log:
        extra = f" (dropped {', '.join(x['dropped'])})" if x.get("dropped") else ""
        print(f"{x['night']}: {x['status']}{extra}")
    if not log:
        print("no live nights found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
