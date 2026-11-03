# Brain v3: work in progress (stopped Nov 3, 17:10, usage limit)

Done: training tables (redzone/lab/brain3data.py, scripts/build_training3.py), trainer + live-path
head-to-head (redzone/lab/brain3fit.py, scripts/train_brain3.py), numpy runtime (redzone/scoring/brain3.py),
Brain(version=3) with v2 fallback, wiring into breaks.blend (LEN table), cuts.DeadTracker.learn
(expected restart only, never the cut gates) and Decider._learn_entries. Models: nfl/nba/nhl .v3.npz
(~0.87 MB total, ~0.6 ms per state). MLB v3 lost to v2 live, so MLB stays v2. tests/test_brain3.py passes.

Live-path results (our ESPN archive, unseen): see metrics_v3.json "live". v3 beats rules and v2 on
stoppage-in-60s AUC (NFL 0.76 vs 0.68/0.71, NBA 0.68 vs 0.56/0.65, NHL 0.70 vs 0.63/0.67) and big plays.

NOT ready: scorecards with everything on regressed two lines (NFL your-team big seen 100 -> 93.1,
NBA entries mid-play 38 -> 43). Next step: ablate (brain.v3_entry off; v2 key points with v3 breaks)
and keep only the parts that don't regress; then PR into integration/next with before/after tables.
Raw data lives only in the cloud container (/home/claude/raw); rebuild with build_training3.py.
