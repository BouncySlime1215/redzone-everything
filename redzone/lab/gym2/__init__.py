"""Gym v2: the practice world for the AI director (task A-01), research only and never imported by the show.

It wraps the PARK replay (`scripts/park_replay.py`: the app's own `park_show.Show`, stepping `park_core.run` with the
planner, on fake sticks and a recorded night), so legality and physics are the show's own: there is no second physics.
`env.GymV2` steps it one tick at a time. With the identity scenario its decisions are byte for byte the replay's
(`tests/test_gym2_parity.py`, `scripts/gym2_parity.py`).

Why: the old teacher's world had its own transition model and too little variation (EXP1b FAIL-A: 89% of oracle choices
were ties); Gym v2 starts from the show's own step function and must first reproduce it exactly.
"""
