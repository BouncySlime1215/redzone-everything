#!/bin/bash
cd analysis_tmp/wthot
run(){ echo "== $*"; nice -n 15 python scripts/buffered_jump_sim.py --maxrate --archive analysis_tmp/queue/nfl_dev --min-games 7 "$@" 2>&1 | tail -6 | cut -c1-230; }
run --mr-sticks 2 --mr-games 4 --mr-prep 60 --mr-seek 40 --mr-fail 0.74
run --mr-sticks 2 --mr-games 4 --mr-prep 45 --mr-seek 15 --mr-fail 0.25
run --mr-sticks 2 --mr-games 4 --mr-prep 45 --mr-seek 8 --mr-fail 0.10
run --mr-sticks 2 --mr-games 4 --mr-prep 20 --mr-seek 8 --mr-fail 0.10
run --mr-sticks 2 --mr-games 2 --mr-prep 110 --mr-seek 30 --mr-fail 0.25
run --mr-sticks 2 --mr-games 4 --mr-prep 110 --mr-seek 30 --mr-fail 0.25
run --mr-sticks 3 --mr-games 4 --mr-prep 45 --mr-seek 8 --mr-fail 0.10
run --mr-sticks 3 --mr-games 4 --mr-prep 20 --mr-seek 8 --mr-fail 0.10
run --mr-sticks 4 --mr-games 4 --mr-prep 20 --mr-seek 8 --mr-fail 0.10
