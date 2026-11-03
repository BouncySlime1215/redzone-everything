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

**The NFL dead air, found and fixed.** Lock-in points pushed single NFL broadcasts above RedZone
(fixed score 45, no commercials). The TV left RedZone for a game just before its break. On the
MIN @ SEA slate that alone was +370 s of commercials (BUF @ CLE +100 s). Now RedZone gets the same
lock-in points as its best NFL game (`reasoner.redzone_share`, 1.0), since it whips to that play anyway.

NFL, 15 pts by odds, floor 0.5 (eval slates; tuning slates in brackets):
| redzone_share | dead air | big seen | big live | crunch live | your team | drives live |
|---|---|---|---|---|---|---|
| off | 8.6 [10.8] | 95.4 [95.0] | 29.7 [34.4] | 71.4 [75.3] | 100 | 13.2 [13.2] |
| 0 (before the fix) | 8.9 | 95.8 | 31.2 | 71.2 | 100 | 14.1 |
| 0.5 | 8.9 [10.7] | 95.7 [94.7] | 30.5 [34.4] | 71.0 [71.7] | 100 | 13.6 [14.3] |
| 0.75 | 8.7 [9.9] | 95.8 [94.4] | 29.8 [33.1] | 71.6 [71.7] | 100 | 13.2 [13.9] |
| 1.0 | 8.5 [10.6] | 95.7 [95.3] | 28.8 [34.1] | 71.6 [74.7] | 100 | 12.7 [13.5] |

So on NFL, the only way the head gets more big plays live is by leaving RedZone, and that costs
commercials. No setting wins on every line, so **NFL lock-in points are off (storyline only)**.

NHL (integration/next):
| must_points | mid-play entries | missed on entry | dead air | big live | crunch live | your team |
|---|---|---|---|---|---|---|
| off | 11 | 1 | 24.3 | 42.0 | 87.5 | 100 |
| 2 | 8 | 2 | 24.4 | 42.2 | 87.8 | 100 |
| **4 (default when on)** | **5** | 1 | 24.4 | 42.0 | 87.3 | 100 |
| 8 by odds | 7 | 3 | 24.3 | 42.0 | 88.2 | 100 |

NBA: no gain (plays missed on entry 18 -> 19 at 8 points), so points are off and it shows the storyline only.

With `reasoner.enabled: false` (the default) every scorecard equals integration/next exactly.

## Next steps
1. Decide on NHL points (halves mid-play entries; dead air +0.1, crunch -0.2).
2. When PR #13's adapter lands: held-out model eval and peak RSS with both adapters via llm_server.
3. Move the scorer hook behind `contracts.Reasoner.view()` once the decision core reads it.
