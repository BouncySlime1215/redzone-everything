# V7 Mind round: report summary (Kaggle notebooka466d7f817 v7, read by the overseer Nov 4, 3:20 PM)

Graded on dev-test nights (n=2400; the locked test set is not graded here). The full report is out/report.md in the notebook output.

| line (all leagues) | head (GBM) | teacher (32B/14B) | student (1.7B Mind) | bar |
|---|---:|---:|---:|---|
| lock_acc | 73.7 | 63.7 | **82.2** | ≥95 |
| unsafe_leave % | 10.2 | 14.7 | **7.6** | ≤0.5 |
| must-see recall | 56.8 | 34.1 | 53.7 | |
| over-cautious % | 29.2 | 55.3 | 17.8 | |
| rule conformance % | 87.8 | 81.2 | **99.7 ok** | ≥99 |
| confidence ECE | – | 0.198 | 0.131 | ≤0.10 |
| answered % | 100 | 42.7 | 76.0 | 100 |

- **Verdict:** the split gate FAILS for all three. The student is the best of the three on lock accuracy, unsafe leaves and rule conformance.
- **Red flags:**
  - **The crunch slice has 100% unsafe leaves** (n=125). The student leaves in crunch, which Nick never wants.
  - **The teacher answered 0% on CBB, NHL and MLB**: the time-split queue starved them. The student answered 56% on CBB and 0% on MLB.
  - **Train/valid/test split:** train 75.2 / 21.7 unsafe, valid 81.7 / 7.0, test 82.2 / 7.6. Train is worse than test (n=600 is small, or the train sample is skewed). Check it.
  - **No calibrated abstain cut** keeps unsafe leaves at or under 0.5%.
  - **Head AUC on NBA is .999** across train, valid and test. This is a likely leak; lane 1 is auditing it.
- **Re-score unsafe_leave under Nick's 3:15 PM rule.** Leaving mid-play at LOW leverage is now allowed, so many of today's "unsafe" labels may be fine. High-leverage leaves (crunch, down the stretch) stay at a hard 0. Report both.
- **Highlight jump:** `jump_when_it_fits` puts 99.8% of reels in dead time, misses 0 big plays and recalls 100% of jumps. It looks gate-ready for a promotion check.
- **Watchdog student:** 63.3% accuracy vs 21.6% base. FAIL. Its weak spots are prompt, paused, frozen and upstream.
