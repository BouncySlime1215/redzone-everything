# V8 watchdog, uncapped (EXP-085), Colab L4, Nov 4 (round 20261105-0037)

Pack: kaggle-inputs@4ccefa5. Seed 7, full preset, students mode. 1,436/1,436 steps, val_loss 0.0174, wall time 1.03 h (~5 CU). Complete.

## Watchdog, held-out wordings (n=686)

| | Base (no adapter) | Student |
|---|---|---|
| Accuracy | 22.6 | **83.1** |
| Unusable JSON | 0 | 0 |
| Gate | FAIL | FAIL |

Correct answers per category:

| Category | Base | Student |
|---|---|---|
| component | 3/42 | 36/42 |
| core_loop | 13/42 | **28/42** |
| cosmetic | 0/42 | 35/42 |
| frozen | 3/42 | 37/42 |
| mixed | 15/70 | 49/70 |
| network | 20/42 | 35/42 |
| paused | 0/42 | **27/42** |
| permission | 20/42 | 40/42 |
| person | 27/42 | 40/42 |
| prompt | 0/42 | 42/42 |
| resource | 18/42 | **29/42** |
| rule | 15/70 | 62/70 |
| stick_link | 12/42 | 35/42 |
| storage | 4/42 | 41/42 |
| upstream | 5/42 | 34/42 |

The weakest categories are paused, core_loop, resource and mixed.

## Same run's game-reasoner reference (today = rules, head = GBM; no student in this run)

| Metric | Today | Head |
|---|---|---|
| Lock accuracy (all) | 70.5 | 70.3 |
| Unsafe leaves | 8.9% (hi 1.2) | 8.9% (hi 0.2) |

Lock accuracy by league, today vs head:

| League | Today | Head |
|---|---|---|
| NFL | 59.9 | 67.2 |
| NBA | 92.8 | 91.4 |
| NHL | 54.8 | 47.8 |

Split gate: head FAIL (rule conformance 81.4 vs the 99 bar).

## Highlight jump vs stay (held-out)

`jump_when_it_fits`: 99.6% of reels land in dead time, 1 big play missed, 95.5% agreement, 100% jump recall.
