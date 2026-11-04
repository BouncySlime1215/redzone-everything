# Live nights (real nights on Nick's TV)

One folder per night, `<YYYY-MM-DD>/summary.json`: the aggregate numbers the Mac's supervisor
wrote to `data/live_nights/<night>/summary.json` after that night, reduced to an allowlist by
`scripts/publish_live_nights.py` (counts and percentiles only: no raw rows, no LAN addresses).
`.github/workflows/northstar.yml` reads this folder (`--live-dir docs/live-nights`) into the
North Star "Live nights" section.

To add nights: copy the Mac's `data/live_nights/` somewhere you can read (the Thursday pull puts
it under `/mnt/project-files/live-nights/`), then

    python scripts/publish_live_nights.py /mnt/project-files/live-nights

and open a PR with the new folders. Never commit the raw `*.jsonl` files (the repo is public).
