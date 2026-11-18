# Cycle 23 plan (Start Planner), Thu Nov 5, ~14:00 to 15:00 UTC: Rule #1 live check closed, taps fixed for YTTV

**From:** integration/next at eea2d94 (grader v26). Landed: #302 (EXP-700 NHL autopsy, EXP-744 clock-truth grader), #303/#304
(NHL Rule #1 package, flags off), #308 (log redaction), plus the EXP-739/741/742/743 rows.

## Rule #1: Wednesday's live "17 of 30 mid-play"

| Finding | Owner / state |
|---|---|
| 15 of 17 were Nick's single taps. The code exempted kind=user from entry timing, which is a **bug** against Nick's Nov 4 rule (one tap waits for a dead ball; a second "Go now" is instant). | #310 EXP-741. Lane 3 (corrected re-run): **0 live landings in every arm once the Prime delay is synced.** YouTube TV taps with a rewind fallback pass at 0-63 s waits. Prime taps fail the 180 s wait guardrail (p50 199 s), because the feed trails the picture and Prime can't rewind. |
| 2 of 17 were `start` first picks after the RC6.1 restarts (NBA, Prime). | EXP-739 start_timed. NBA arm graded on v3 with the mid-game restart replay. |
| Live grader was soft (unsynced 40 s Prime prior, "unknown" scored as play). | #311 EXP-742: measured delay, unknown label, taps counted apart from app entries. |
| Was the NBA dev grader hiding mid-play? | **No.** EXP-744 pbp clock truth: 64 of 64 dead, disagreement 0.6% (errs safe). Frozen as grader v26. NBA "0 mid-play" now needs both graders at 0. |

**Next RC (lane 4 stages; normal deploy rules):** #310 with user_timed ON for YouTube TV taps only (rewind fallback), Prime taps
unchanged, and `user_bug_gate` = shadow (the Prime score-bug clock-stop gate, arm E) so one live night can grade it. If E fails,
the Prime tap wait trade goes to Nick.

## Sync

#306 (A5 per-league lag prior), #305 (YTTV sync tests), #309 (sync_probe stall_s fix) are open. EXP-743: a Prime delay prior from
synced anchors (measured ~16 s vs the 40 s prior). EXP-803 A1-A5 are graded on v3 (Fri Nov 6 8:30 AM ET).

## Waiting on Nick

NHL Rule #1 package card (recommended on), RC7 sync card, RC6.3, EXP-109/107g launches.
