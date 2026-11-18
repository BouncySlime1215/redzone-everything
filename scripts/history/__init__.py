"""History fetchers: polite, resumable growth of the play-by-play corpus (Track B, task B-01).

Standalone sidecar tooling. The show never imports it, so flag-off behaviour is untouched. Modules:
  net.py       one HTTP client for every source: <= 1 request/second overall, honest User-Agent, backoff, loud failures
  leagues.py   league registry (ESPN paths, season windows) and the evaluation fences
  licences.py  source registry: terms class, licence note, finding, status; writes the licence manifest
  store.py     local layout under data/history/: atomic writes, per-game ledger, run lock, report, verify
  espn.py      ESPN public JSON: calendar -> scoreboard -> summary -> one compact record per game
  probe.py     how far back each ESPN league goes and how complete older seasons are (numbers only)
  datasets.py  hoopR / wehoop / nflverse files (licence-gated whole-file downloads with sha256)
  pack.py      per-game records -> deterministic per-league-season shards plus the manifest
CLI: python scripts/fetch_history.py --help. Runbook: docs/memory/runbooks/history-fetch.md.
Why: B-01 (WORKPLAN, tracks/B-foundation-model.md B0): the corpus holds about 6% of the planned events, and the fetches must be safe to leave running unattended.
"""
