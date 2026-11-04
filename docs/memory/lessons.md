# Lessons

Bugs we hit and why, so nobody hits them twice. Newest first. **Add one after every fix**; the
auto-fix brain and the watchdog read this file before acting
([memory hook](runbooks/README.md#memory-hook)).

Each lesson is a `## ` heading plus four lines. `Signals:` holds the log words that point to it
(comma separated); the watchdog matches them against error signatures, so keep them literal.

## Your team's missed TD lost every replay slot to other games' last chances
- **Symptom:** release gate `nfl_stale` (10 s stale feed): your-team big plays seen 95.2 % (20/21). The TD was queued for a replay and never shown.
- **Cause:** `Decider._best_replay` ranked any urgent play (in its last `replay.urgent_s`) above everything else, so with a stale feed's extra misses there was always another game's last chance ahead of your team's TD until its window closed.
- **Fix:** rank your team's missed plays first (`replay.yours_first`), then earliest deadline, then value.
- **Signals:** your_team_big_seen, nfl_stale, replay queue, yours_first

## Kaggle auto-extracts uploaded archives
- **Symptom:** round-2 notebook can't find `rz_code.tar.gz`; the files sit under `code/` instead.
- **Cause:** Kaggle unpacks .zip and .tar.gz datasets on upload.
- **Fix:** notebooks look for the extracted folder first, the archive second.
- **Signals:** rz_code.tar.gz, No such file, kaggle/input

## Chrome upload tool caps files at 10 MB
- **Symptom:** a dataset upload from the browser silently fails or truncates.
- **Cause:** the Chrome upload tool's 10 MB per-file limit.
- **Fix:** split big files into `.bin` parts (`split -b 9m`) and join them in the notebook.
- **Signals:** upload, 10 MB, .bin

## Kaggle teacher read a .gz as text
- **Symptom:** Kaggle round 1 Version 1 died at 255 s.
- **Cause:** `run_teacher.py` opened `teacher_requests.jsonl.gz` with `open()`.
- **Fix:** open `.gz` with `gzip.open(..., "rt")`; the CPU smoke test (`tests/test_kaggle_e2e.py`) now runs the notebook in CI.
- **Signals:** UnicodeDecodeError, gzip, .gz, invalid start byte

## Brain trained on locked nights (leakage)
- **Symptom:** brain scores looked too good on the release nights.
- **Cause:** the NFL brain trained on 2025 (locked) and the NHL break heads on locked archive nights.
- **Fix:** retrain on nights before `test_start` only (`agent/distill/split.py`); a CI leakage check; round 2 excludes brain features until the retrain lands.
- **Signals:** leakage, locked, test_start

## A broken config fell back to defaults (dry run)
- **Symptom:** the app kept running but stopped controlling the TV.
- **Cause:** an unreadable `data/config.yaml` fell back to the built-in defaults, and the defaults are dry run.
- **Fix:** #24 restores the newest `config_history` version instead, and sets a corrupt `redzone.db` aside (never deletes it).
- **Signals:** config.yaml, YAMLError, dry run, OperationalError, database disk image is malformed

## Tailscale steals the home routes
- **Symptom:** sticks and TV unreachable from the Mac while at home; network TV control "failed".
- **Cause:** Tailscale's 100.64/10 routes overlap the home LAN 100.110.165.x.
- **Fix:** quit Tailscale, or `python -m redzone.network --fix`. `redzone.network` flags it.
- **Signals:** utun, No route to host, route_issue, 100.110.165

## Sticks "offline" because the Mac is at school
- **Symptom:** both sticks look offline during the day.
- **Cause:** the MacBook travels to campus (10.23.x); the sticks are fine.
- **Fix:** check the Mac's network before blaming stick power. The watchdog sets `away` and stays quiet.
- **Signals:** away, 10.23., Connection refused, unable to connect

## deploy.py refused the Mac's checkout
- **Symptom:** the deploy stopped on "working tree not clean".
- **Cause:** untracked local data (`data/`, `agent/model/`, `.venv-agent/`, plans) counted as changes.
- **Fix:** #34: refuse only when *tracked* files are modified. The Mac repo is `/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone`, not `~/redzone-everything`.
- **Signals:** working tree, not clean, untracked

## HOME + CEC input switch is slow
- **Symptom:** 5–50 s switches showing the Fire TV launcher; Prime reloads.
- **Cause:** HOME is the only proven switch on these sticks, and it kills Prime's player.
- **Fix:** TV-side input switching (`scripts/tv_setup.py`) and the YouTube TV Multiview path (flag off until probed).
- **Signals:** HOME, launcher, Prime, switch slow

## Cloud sessions: GitHub GraphQL and parallel git are blocked
- **Symptom:** `gh pr view`/`gh pr list` return HTTP 403; a second clone returns 429.
- **Cause:** the session proxy allows only the REST API and 2 concurrent git transfers per repo.
- **Fix:** use `gh api repos/BouncySlime1215/redzone-everything/...`; one clone at a time.
- **Signals:** GraphQL, 403, 429, too many concurrent
