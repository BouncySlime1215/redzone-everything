# Cycle 03 review (End Planner / Integrator), Wed Nov 4, 12:15 to 15:30 UTC

**Plan:** [plan.md](plan.md). **Merged train:** integration/next e2ae71c (#105, #107, #108, #109, then #111 and #110).
**Scored at:** e2ae71c, `scripts/northstar.py --split both --profile candidate --oracle`, eval-data-v1, default sims
(no tune jitter). Locked was read once for this review and not used to tune anything.

## Verdicts

| lane | PR | verdict | against "what we're looking for" |
|---|---|---|---|
| ② | #107 honest sync doubt | **keep (merged), flags off** | What was asked: the sims now know the true delay (`--tune-jitter`, per-tune U(0,6) s), break anchors feed the Kalman filter (`--break-anchors`), and every tick is graded. **σ coverage PASSES: the true error is within 2σ on 98.4-100% of ticks (≥95%).** But σ is ~3x too wide (2σ ≈ 18 s vs true p95 ≈ 5 s), and anchors at today's prior barely shrink it. `NoiseCal` (`delay.self_calibrate`, off) is the fix that works without ground truth: dev NFL big live 54.4 → 57.6, locked 53.3 → 60.4, 0 new mid-play. **Misses the targets** (NFL ≥60 dev, NHL ≥50) until Thursday's probe refits the anchor prior. No cap tricks. |
| ② | #110 one-sided landing lag + strict ended entries | **keep (merged), candidate only** | Rule #1 under honest jitter: NHL mid-play 1 → 0 on all 3 dev seeds, big live and dead air within ±0.2. Base defaults off; decisions.md line present. |
| ② | #105 scenario tree: skip GAME-01 | **keep (merged), tree stays off** | The fix for #103's risk: a visit never displaces a catch-up or crunch decision. Dead air +0.3 NFL has its measured reason at the top. |
| ③ | #108 pre-tuned vs cold presses | **keep (merged); misses the bar** | The honest split asked for is in the drill and in the North Star detail. Pre-tuned presses: **p50 3.02 s, p95 3.7 s** (candidate drill); cold: ~8.5-8.75 s. press_to_tv p95 stays **8.75 s FAIL**. The finding is the useful part: a pre-tuned press is bounded by the ~2 s SmartThings flip, and ≤3 s needs the TV local key path, which the night lab doesn't model yet. The mislabel fix (a stick parked on a replay is cold) is right. |
| ① | #109 Pick v1 hook, `noggin.pick` off | **keep (merged), flag off; misses the bar** | Numpy forest (99 KB, 0.5 ms/12 games), shadow mode, the switch-rate cap (at 0), all as asked. With the flag on: NBA mid-play 0, your-team crunch 31/31, dead air -1.3 dev / -0.7 locked, but big live only **ties** (77.5 / 68.8) and **locked crunch live drops 84.9 → 81.0**. % of oracle unchanged. Honest report. Next: crunch-ahead features. |
| Int | #111 self-taught rule | **keep (merged early)** | `right_game` → `pct_of_oracle`, `load_taps`/`--taps-db` and `suggest_from_feedback` removed, phone v2 returns `suggestions: []`, noggin.md §0. Checked: the merged `northstar.py` main loop still builds the candidate, oracle and drill columns. |

**Taps-as-labels check (Nick, 10:55 AM):** after #111, nothing in `redzone/noggin`, `redzone/lab` or the scripts reads
the `feedback` table or `Store.taps()` for training, labels or grading. Phone v2 still *records* votes for its own
page. The manual sync taps in `autonomy.py` set the delay (a control, not a label). **No PR this cycle reads taps as
labels.** Cycle 02's suggestion "the harness reads `Store.taps()`" is withdrawn.

## Merged scoreboard: live default vs candidate (eval-data-v1)

| line | target | dev live | dev cand | locked live | locked cand |
|---|---|---:|---:|---:|---:|
| Mid-play / 100 landings | 0 | 2.45 | **0 PASS** | 1.75 | **0 PASS** |
| Big live NFL / NBA / NHL | ≥95 | 25.2 / 67.6 / 41.4 | 53.4 / 77.5 / 44.7 | 23.3 / 62.5 / 38.9 | 50.1 / 68.8 / 49.0 |
| Crunch live NFL / NBA / NHL | ≥95 | 62.2 / 76.7 / 79.7 | 66.7 / 90.0 / 81.9 | 59.6 / 73.7 / 89.4 | 70.6 / 84.9 / **76.2 ↓** |
| Your-team big / crunch | 100 | 77.4 / 71.7 | 90.3 / 100 | 94.1 / 100 | **91.2 ↓** / 100 |
| Live or replayed ≤10 min | ≥99 | 62.3 | 83.2 | 61.3 | 85.1 |
| Dead air (ceiling NFL/NBA/NHL) | 0 | 22.5 | 19.1 (10.6/11.2/18.9) | 26.7 | 21.1 |
| **% of oracle** (mean; NFL/NBA/NHL) | ≥90 | 49.6 | **64.8** (58.6/83.3/52.4) | 43.3 | **59.1** (54.0/68.8/54.5) |
| press_to_tv p95 (sim) | ≤3 s | 8.75 FAIL | 8.75 FAIL (pre-tuned 3.02, cold 8.75) | | |

The candidate is better than or equal to live on every dev line. On locked it is better on 9 lines, equal on
your-team crunch and press_to_tv, and worse on 2. The two locked misses are the
known thin ones: NHL crunch (2 nights) and your-team big (34 plays, 1 play = 2.9 points). Big live is still the
worst line of % of oracle in every league. The default run uses no tune jitter, so #107's self-calibrated doubt
does not show here; its honest-sim numbers are in the PR.

**Harness gap:** the candidate column's press_to_tv runs the drill with the *default* profile, so it can't show
#108's candidate gain (p50 8.5 → 3.7 s). The harness should run `controls_drill.py --profile candidate` for that column.

## Suggestions for cycle 04
- **Lane ②:** possession-start crunch entry (Nick, 10:45 AM) in the candidate, graded on locked; `NoiseCal` into
  the candidate once the Thursday probe refits the anchor prior; then GAME-04 with the honest σ.
- **Lane ①:** the crunch policy by oracle imitation (NBA first) plus crunch-ahead features for Pick v2; no Nick signal.
- **Lane ③:** model the TV local key path in the night lab; the drill's candidate column in northstar.
- **Lane ④ (if reopened):** v3 locked switch Fri Nov 6; #88/#91 rebase; #20/#23/#24 still need Nick to close.
