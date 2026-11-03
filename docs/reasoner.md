# Live game reasoner: status (Nov 3, 2026, stopped early for the usage limit)

## What's built
- `redzone/reasoner/brief.py`: per-game brief (numbers for the head, compact text for the model), rebuilt each play.
- `redzone/lab/reasoner_data.py` + `scripts/build_reasoner_data.py`: 136k archived plays (NFL 51k, NBA 54k, NHL 31k) with hindsight labels (scorecard big/crunch play in the next 180/120/180 s, not garbage).
- `redzone/reasoner/head.py` + `scripts/train_reasoner.py`: fast lock-in head (LightGBM -> numpy trees, 1.5 MB RAM, 0.09 ms/state), cross-fitted by whole night.
- `redzone/reasoner/reasoner.py`: bounded lock_in points (cap 15, none in breaks, none in garbage, none for other games while your team is on), event-driven model narration client for `agent/llm_server.py` (adapter `game-reasoner`), never blocks a poll.
- Phone: storyline + MUST SEE/WATCH line on game cards.
- `scripts/export_reasoner_llm.py`: briefs + targets for the LoRA (handed to the teacher/student thread, PR #13).
- 18 tests in `tests/test_reasoner.py`.

## Lift over the brain alone (out-of-fold, same alert rate)
| League | AUC head | AUC brain | must_see precision head / brain | big plays preceded by must_see head / brain |
|---|---|---|---|---|
| NFL | 0.890 | 0.717 | 59.8 / 45.7 | 81.3 / 68.5 |
| NBA | 0.884 | 0.911 | 15.0 / 16.8 | 100 / 100 |
| NHL | 0.649 | 0.575 | 47.3 / 44.5 | 22.3 / 21.5 |

## Scorecards, reasoner on (cross-fitted heads) vs off, on integration/next
NFL (must 15 by odds, floor 0.5): cuts 0 -> 0, big seen 95.4 -> 95.8, big live 29.7 -> 31.2, crunch live 71.4 -> 71.2,
your-team big 100 -> 100, drives live 13.2 -> 14.1, **dead air 8.6 -> 8.9** (from "slow to leave" after scores watched live).
Floor 0.3 (picked on non-eval tuning nights): big live 32.0 but crunch 70.4: not shipped.
NHL (must 4, on codex/resume-redzone): entries mid-play 11 -> 5, dead air 24.3 -> 24.1, big live 41.7 -> 42.0, crunch 87.5 -> 87.2.
NBA: no gain (plays missed on entry 18 -> 19 at 8 points), so points are off; storyline only.

## Next steps
1. Fix the NFL dead-air +0.3 (rule: no dead-air rise) before turning `reasoner.enabled` on; NHL crunch -0.3 too.
2. Re-run NHL/NBA on integration/next; add a `Reasoner.view()` adapter for `redzone/contracts/reasoner.py`.
3. When PR #13's adapter lands: held-out model eval (lock_in vs hindsight, head, brain) and peak RSS with both adapters via llm_server.
