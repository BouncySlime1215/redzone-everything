# Cycle 04 review (End Planner / Integrator), Wed Nov 4, 15:30 to 19:45 UTC

**Plan:** [plan.md](plan.md) (with addenda 1-3). **Scored at:** integration/next e819bbd, `scripts/northstar.py
--split both --profile candidate --oracle`, eval-data-v1. The gates are the new oracle-relative ones from #121. Locked
was read once for this review and not used to tune anything. From now on, cycles run on a 90-minute clock (Nick, 2:45 PM ET).

## Verdicts

| lane | PR | verdict | against "what we're looking for" |
|---|---|---|---|
| ③ | #118 live-night auto-log + STK-05 + flip prior + traces fix | **keep (merged, in RC5.1)** | As asked: the app writes `data/live_nights/<night>/` by itself, with landing offsets, sync error per stick, switch and press latency. STK-05 (paused or stale hidden stick → re-tune at once) has marked tests. The follow-up 4dfebad caught that traces.jsonl holds TraceEvents. Without it, press-to-TV would have been empty from every real night. The Live nights section is PENDING until tonight's first night. |
| ② | #117 FastCast 4 h check counts live time only | **keep (merged, in RC5.1)** | The confirmed cause is fixed, and a status trail is added for the unexplained Nov 3 case. Its lesson used `**Cause (confirmed):**`, which the parser doesn't read, so I fixed it on the train. |
| ③ | #115 SmartThings token reload, #127 per-stick tune pricing | **keep (merged)** | #115 removes a silent path to 0% flips after a probe run. #127 prices a tune by the stick that does it (4K Plus 2.33 s vs 4K 2023 3.74 s p50); the decider's switch_cost is unchanged. |
| ④ | #123 publish live nights, #126 speed asserts at background priority | **keep (merged)** | #123 lets the runner's North Star see the Mac's nights (aggregates only). #126 is the RC5 deploy fix, so integration/next equals RC5.1 (e8bbf8e, cut by lane 4). |
| Planner | #121 oracle gates, #120 addenda | **keep (merged)** | Big live ≥95% of oracle per league, landings ≥98%, % of oracle ≥95%, replay window 3 min (2 min stretch), and each row shows its gap to 100% of oracle. |
| ⑤ | #91 slate preview (includes #88 night replay) | **keep (merged), phone.v2 off** | Read-only, and every delta is 0. The phone still records votes but nothing trains on them (self-taught rule). |
| ② | #128 YTTV PlaybackState bursts (Nick merged) | keep | A free sync-anchor candidate: stick-to-stick offset within 0.1 s. |
| ② | #133 YTTV seek 15 s + wake press | **hold, lands with #132** | On its own it double-counts the wake press, which gives mid-play entries on locked (NFL 12, NHL 14). #132 contains it and brings those to 0. Neither may reach a release alone. |
| ② | #116 possession-start entry | **not landed** | It was stale for 4 h; it was updated at 19:26 and CI is running. It carries Nick's crunch decision, so it is cycle 05's first merge. |

**Self-taught check:** no PR merged this cycle reads Nick's taps, votes or overrides for training, labels or grading.

## Scoreboard: live default vs candidate, new gates (eval-data-v1)

| line | gate | dev live | dev cand | locked live | locked cand | cand gap to 100% of oracle (dev / locked) |
|---|---|---:|---:|---:|---:|---|
| Mid-play / 100 landings | 0 | 2.45 | **0 PASS** | 1.75 | **0 PASS** | |
| Landings in dead time | ≥98% | 97.5 **FAIL** (was PASS at 95) | 100 PASS | 98.3 PENDING | 100 PASS | |
| Big live NFL | ≥95% of oracle | 25.2 | 53.4 (58.6% of 91.2) | 23.3 | 50.1 (54.0% of 92.8) | 41 / 46 pts |
| Big live NBA | ≥95% of oracle | 67.6 | 77.5 (83.3% of 93.0) | 62.5 | 68.8 (68.8% of 100) | 17 / 31 pts |
| Big live NHL | ≥95% of oracle | 41.4 | 44.7 (52.4% of 85.3) | 38.9 | 49.0 (54.5% of 89.9) | 48 / 45 pts |
| Crunch live NFL / NBA / NHL, % of oracle | | | 79.0 / 96.1 / 93.2 | | 80.7 / 89.9 / 81.1 | |
| Big seen ≤3 min (≤2 min) | ≥99% | 49.7 (46.0) | 66.2 (61.9) | 47.4 (43.2) | 68.0 (63.6) | |
| Your-team big ≤3 min | 100% | 64.5 | 80.6 | 82.4 | **79.4 ↓** | |
| Dead air | 0 | 22.5 | 19.1 | 26.7 | 21.1 | live air 91-97% of oracle |
| **% of oracle** (mean) | ≥95% | 49.6 | **64.8** | 43.3 | **59.1** | 35 / 41 pts |
| press_to_tv p95 (sim) | ≤3 s | 8.75 | 8.75 | | | |

**Honest reading:**
- The sim numbers are the same as cycle 03's: % of oracle is 64.8 / 59.1. Cycle 04 landed logging, hardware fixes and gates, but none of the candidate behavior changes (#116, #129, #132 and #125 are still open).
- Under the new gates, every big-live line FAILs by 17-48 points of oracle. **Big live is still the worst line in every league.**
- The stricter 3-minute replay window shows a large gap: 66-68% against the 99% gate.
- Live dead-time landings now FAIL at 98%.
- Your-team big on locked is 1 play below live (27/34 vs 28/34).

## Promotion step (new, every review; Nick 2:50 PM ET)

`scripts/release_gate.py` on the LOCKED nights, run on the live default and on the candidate profile (the flag-off
work already merged). Full tables: [gate-locked-live.md](gate-locked-live.md), [gate-locked-candidate.md](gate-locked-candidate.md).

| | pass | fail | pending |
|---|---:|---:|---:|
| live default | 10 | 13 | 18 |
| candidate | 12 | 9 | 20 |

- **Rule #1 gates:** the candidate passes every one, and live fails three of them:
  - NFL entries above today (1 vs 0);
  - NHL entries above today (4 vs 2);
  - NBA entries at a dead ball (52.9%, 8 mid-play of 17).
- **The candidate is better on:** NFL big seen (85.2 → 94.2), NFL 10-min catch-up (55.6 → 87.5) and NBA/NFL crunch live.
- **The candidate is worse than live on 4 locked gates, all on thin data:**
  - NFL your-team big seen: 95.2 → 90.5 (one play, 20/21 → 19/21);
  - NFL your-team within 10 min: 19/21 → 18/21;
  - NHL crunch plays aired during a replay: 2 → 10;
  - NHL crunch live: 89.4 → 76.2 (2 nights).
- **Nothing reaches PASS on every gate**, live included. Under the release gate as written ("anything that misses ships OFF"), nothing can be promoted.

**Promoted this cycle: nothing.**
- The candidate is promotion-ready on rule #1.
- It is blocked by the two your-team NFL plays and the NHL crunch-in-replay regression. Lane 2 owns that one: a catch-up replay that runs over NHL crunch. The fix is to never start a replay with crunch due inside it.
- #116, #129, #131 and #132 aren't on integration/next yet, so they can't be gated. Each gets gated on locked in the review where it lands.
- Whether to promote on "rule #1 passes and no gate worse than live" instead of "every gate PASS" is Nick's call; I've asked him.

## Nick's 3:05 PM triage (parts 2-4)
- **#133:** held to land with #132 (see above). Nick pushed its decisions.md line. It never ran CI because it conflicted on decisions.md; #132 now carries integration/next.
- **Old drafts:**
  - Closed **#20, #23, #24.** Superseded: integration/next has the dark-feed check and Health with the SmartThings conditions, and the demo builds its own FeedBook. Nothing was left to salvage.
  - Closed **#16.** Salvaged as **#135** (merged). Its hard-coded holdout predated eval-data-v1 and would have tuned on 19 locked NFL nights, so it now holds out `splits.nights_for(..., "test")`. A lesson is logged.
  - Closed **#18.** It never met its bar; the branch is kept for the reel jump/stay model.
  - Closed **#13.** Salvaged by lane 1 as **#138** (merged: the Mind V7 pipeline).
  - Each closed PR has a one-line comment.
- **Still open, for Nick to close:**
  - **#112:** superseded by #111.
  - **#113:** superseded by #117.
  - Closing these was blocked for this session. Their owners confirmed both are superseded.
- **docs/status.md:** the Queue is rewritten for the open PRs in merge order plus the 5 new builds (bc70633).

## Cycle 05 (90 min, review at 21:15 UTC)
- **Merges first:**
  1. #132 (with #133);
  2. #116 (possession-start entry);
  3. #136 (lane 3 builds c/d/e: self-calibration, pre-game warm-up, buffer watchdog, all flag off);
  4. then #129, #125 and #131.
- **Lane ②:** big live per league toward 95% of oracle. This needs #116, #132 and #125 in the candidate, graded on locked.
- **Lane ①:** possession policy (#129) and the Reflex on existing shards while the Kaggle factory (cdf86ef, launched by the overseer) runs.
- **Lane ③:** real-hardware press-to-TV ≤3 s from tonight's live night.
- **Lane ④:** the night health report (build a).
