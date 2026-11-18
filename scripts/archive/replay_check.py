"""Replay one exact archived sports date without connecting to feeds or devices.

    .venv/bin/python scripts/replay_check.py 20261101 --leagues nba nhl

Outputs a manifest, honest scorecards, timing-permission audit and manual replay cues.
Why: Replays one archived sports date without feeds or devices (docstring, service runtime commit).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config
from redzone.lab.replaycheck import cues, load_date, run_session
from redzone.models import League


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date", help="Exact scoreboard date YYYYMMDD, America/New_York")
    ap.add_argument("--leagues", nargs="+", choices=[x.value for x in League], default=[x.value for x in League])
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    ap.add_argument("--step", type=float, default=5.0, help="Historical grid seconds; this is not device polling")
    ap.add_argument("--horizon", type=float, default=8.0, help="Required exit window in seconds")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--archive", type=Path, default=ROOT / "data/archive")
    a = ap.parse_args()
    if not 0 < a.step <= 30 or not 0 <= a.horizon <= 60:
        ap.error("step must be (0,30] and horizon [0,60]")
    out = a.out or ROOT / "data/lab" / f"replay_{a.date}"
    out.mkdir(parents=True, exist_ok=True)
    # Reproducible shipped settings, not today's learned latency or device configuration.
    cfg = Config(user_path=out / "unused_default_config.yaml")
    if cfg.user_path.exists():
        ap.error(f"{cfg.user_path} must not exist: this run uses shipped defaults")
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    manifest = {"requested_date": a.date, "date_timezone": "America/New_York", "revision": revision,
                "settings": "shipped defaults; isolated from live engine/device configuration", "leagues": []}
    reports, anchor_cues = {}, {}
    for name in a.leagues:
        league = League(name)
        replays, entry = load_date(a.archive, league, a.date)
        manifest["leagues"].append(entry)
        if entry["status"] != "complete":
            print(f"{name}: {entry['status']}; missing={entry['missing']}; no date substitution", flush=True)
            continue
        sessions = [run_session(replays, cfg, step=a.step, horizon=a.horizon)]
        for seed in a.seeds:
            sessions.append(run_session(replays, cfg, stress=True, seed=seed, step=a.step, horizon=a.horizon))
        reports[name] = sessions
        anchor_cues[name] = cues(replays)
        (out / f"{name}.json").write_text(json.dumps(sessions, indent=2)+"\n")
        for s in sessions:
            audit = s["exit_audit"]
            print(f"{name} {s['scenario']} seed={s['seed']}: "
                  f"cuts={s['scorecard']['mid_play_cuts']}, seen={s['scorecard']['big_seen_pct']}%, "
                  f"unsafe-permission-episodes={audit['unsafe_permission_episodes']}, "
                  f"allowed={audit['counts']['allowed']}/{audit['counts']['samples']} grid samples", flush=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    (out / "manual_replay_cues.json").write_text(json.dumps(anchor_cues, indent=2)+"\n")
    print(f"Reports: {out}")
    print("Device latency, visual play detection and VOD alignment were NOT measured.")
    return 0 if reports else 2


if __name__ == "__main__":
    raise SystemExit(main())
