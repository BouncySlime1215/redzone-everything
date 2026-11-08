"""Replay a past NFL Sunday through the app and print the report card.

    .venv/bin/python scripts/backtest.py 20251214 [--focus WSH] [--window 1pm|4pm|all]
Why: Replays a past NFL Sunday through the app for a report card (docstring, RedZone Everything commit).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.config import Config
from redzone.lab import backtest
from redzone.models import League

ROOT = Path(__file__).resolve().parent.parent
p = argparse.ArgumentParser()
p.add_argument("date")
p.add_argument("--focus", help="team abbr to focus on, e.g. WSH")
p.add_argument("--window", default="1pm", choices=["1pm", "4pm", "all"])
p.add_argument("--delay", type=float, default=35.0)
p.add_argument("--out")
a = p.parse_args()

arch = ROOT / "data/archive/nfl"
sb = json.loads((arch / f"scoreboard_{a.date}.json").read_text())
hours = {"1pm": ("18:", "18:"), "4pm": ("21:", "21:")}
ids = [e["id"] for e in sb["events"]
       if a.window == "all" and not e["date"].endswith("01:20Z") or a.window != "all" and hours[a.window][0] in e["date"]]
reps = [r for r in (backtest.Replay.load(League.NFL, arch / f"summary_{i}.json") for i in ids) if r]
cfg = Config(user_path=ROOT / "data/config.yaml")
mine = set(cfg.get("teams.nfl"))
focus = next((r.gid for r in reps if a.focus and a.focus in r.matchup.split()), None)
rep = backtest.run(reps, cfg, mine, focus=focus, delay=a.delay)
title = f"Backtest {a.date} {a.window} slate ({len(reps)} games)" + (f", focus {a.focus}" if focus else "")
md = backtest.timeline_markdown(rep, reps, title)
out = Path(a.out) if a.out else ROOT / f"data/backtest_{a.date}_{a.window}{'_focus' if focus else ''}.md"
out.write_text(md)
print(json.dumps(rep.summary, indent=1))
print("timeline:", out)
