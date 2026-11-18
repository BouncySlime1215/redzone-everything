"""Evaluation harness (task F-04): one named arm (a decision policy) on a fixed set of real nights, one JSON verdict.

    arms.py       the arms: planner, director_v1, leverage_only, random_legal, noop_rate (local) and the heavy
                  plug-ins stage6_rerank and q_learner (a hook module plus a fenced model file)
    episode.py    one arm on one night through the show's own replay (scripts/park_replay.py, fake sticks), the
                  frozen grader's counts, the per-night scorecard and the raw per-jump log
    verdict.py    the per-night cards folded into one verdict (V2-PLAN section 8.4), paired against a baseline,
                  with run-registry metadata (git sha, code / config / data manifest hashes, seed, fence status)
    recompute.py  an independent recount of every claimed number from the raw logs and the archive

The command is scripts/arm_verdict.py. Research only: nothing outside redzone/lab/armeval imports this package
(tests/test_armeval.py checks), so the show never loads it.
Why: WORKPLAN v2 task F-04: every arm (A-03, A-06, F-11) needs one scorecard that cannot fool itself.
"""
