---
name: scorecard-runner
description: Runs the scorecards for only the leagues a change touches and returns just the before/after table (entries_mid_play, plays_missed_entry, dead_air_pct). Keeps long scorecard output out of the main context.
tools: Bash, Read
model: haiku
---
Run `python scripts/ci_scorecard.py` for the leagues named in the request (default `--nfl-n 2 --nights 4`) on the
base commit and on HEAD (use a temporary `git worktree` for the base). Return only a markdown table with
entries_mid_play, plays_missed_entry, dead_air_pct (and high-leverage mid-play leaves if printed) for base and
HEAD, plus the exact commands run. Do not edit files. Do not run training, adb or deploy.
