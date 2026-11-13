# Decisive-Q dataset (task A-02)

Q for every legal option at sampled decision points of training-night slates, with the decisive flag (best beats second by at least 0.10 value2 points) and the tie flags. It feeds A1 (tie share against supply), A2 (Q-learner against stage 6), A3 (filtered behaviour cloning) and A4 (fitted-Q / IQL with the mask). Library: `redzone/lab/gym2/qdata.py` (its docstring defines the world, the options, Q and the mask); job entry: `redzone/lab/gym2/qdata_job.py`; CLI: `scripts/qdata_build.py`. Research only: nothing in the app imports it and no config key reads it.

## What a row is

One decision point: slate index, night, games, scenario seed, padding (49, 10, 8 or 3 s, A1's settings), feed-lag scenario (per game, Gym v2's `scenario.sample`), second `t` on the night's grid, the lag and the feed cutoff `te`, features (featurestore `value_v3` per game read at `te`, causal; the show's stick state), the legal options (STAY plus each distinct first jump planned within 10 s) with Q, must-see points, live seconds, the core's Rule #1 count and whether the forced jump was made, the masked options with the strict mask's reason, `gap`, `decisive`, `tie`, `single`.

Q = value2 graded value seen over the next 90 s: the option forced, then the plain planner (`park_plan.Planner`) as the continuation, on `park_core.run` (the show's step function) over the games' recorded truth. Not the full Show replay (60 s of CPU per night, no action channel).

## Fence

Only training nights: each league's dev (`fit`) nights, minus the locked window (2025-12-12..2026-01-05), the tuning window (October 2026) and every night at or after the cutoff (default 2025-10-01, track A section 4). An explicitly named fenced night is refused with `FenceError`; every slate's night is checked again before its games are opened. Football scope: at most one NFL game per slate by default (`--max-nfl 1`, Monday and Thursday nights).

On this Mac (Nov 13) the pool is EMPTY at the default: the only unfenced nights before the cutoff are NFL Sundays in September 2025. `--max-nfl 6` opens 4 of them (out of RedZone's scope; used only to measure cost). A1 and A2 need NBA (and college) nights before 2025-10-01, which are not on the Mac: they must come from the history fetch (B-01, Nick's call) or a Kaggle history dataset.

## Commands

    python scripts/qdata_build.py pool --data-root data/archive [--max-nfl 6]
    python scripts/qdata_build.py volume --data-root data/archive [--max-nfl 6]
    python scripts/qdata_build.py build --shard-range 0 2 --master-seed 7 --data-root data/archive --out OUT
    python scripts/qdata_build.py ties OUT [--eps 1e-6]
    python scripts/qdata_build.py verify OUT
    python scripts/qdata_build.py cost --data-root data/archive --max-nfl 6 --cpu 45 --out SCRATCH

`build` writes `OUT/shards/qdata_sNNNNN_<night>.jsonl.gz` (gzip with no clock: the same shard always gives the same bytes), `OUT/summaries/qdata_sNNNNN.json` (the shard's done mark), `OUT/qdata.manifest.json` (F-05 `redzone-data-manifest/1`, kind `history`: path, sha256, bytes, rows, night, fence status, licence per file) and `OUT/qdata_summary.json` (counts and the tie table). A run started again skips finished shards.

## On Colab (the F-03 worker)

Steps are shard indexes. The shard files go to `<work>/qdata/` outside the worker's checkpoints (they are not copied or re-hashed at each checkpoint); each checkpoint holds `state.json` (each done shard's file hashes), `qdata.manifest.json` and `qdata_summary.json`. A resumed unit re-hashes the listed files before it trusts them. Upload the archive files (see `volume`) to `<work>/archive/` and give `data_root` relative to the work folder (a spec may not hold an absolute path).

    # config.json
    {"master_seed": 7, "slates": 60000, "per_shard": 200, "data_root": "archive", "out": "qdata",
     "max_nfl": 1, "cutoff": "20251001", "episode_min": 10, "points": 12, "tag": "qdata-m7-v1"}

    python scripts/job.py spec --job-id qdata-m7-a --module redzone.lab.gym2.qdata_job --commit HEAD --seed 7 \
        --steps 0 150 --ckpt-every 10 --config config.json --metrics-file none \
        --output qdata.manifest.json --output qdata_summary.json --output state.json --out spec_a.json
    # a second spec with --job-id qdata-m7-b --steps 150 300 runs on the second vCPU in its own work folder

Then download `<work>/qdata/` and `out/`, and run `python scripts/qdata_build.py verify <folder>`.

## Cost (measured Nov 13 on the Mac, toy, NFL Sundays, `--max-nfl 6`)

`cost --cpu 45`: 364 slates, 2,536 decision points (101 slates had none, all of them at padding 49), 45.1 s CPU (4.9 s loading games), 0.124 s per slate, 0.016 s per decision point, 1.85 legal options and 0.16 masked per point, 89 gzip bytes per point. ESTIMATE, linear: 60,000 slates = 2.1 CPU-hours, about 1 hour on 2 Colab vCPUs as two processes, about 37 MB of shards. NBA slates will cost more (more legal spots, real feature rows instead of missing ones): ESTIMATE 2 to 4 times, still under 12 CPU-hours. Plan: 300 shards of 200 slates, two specs of 150 shards, a checkpoint every 10 shards.
