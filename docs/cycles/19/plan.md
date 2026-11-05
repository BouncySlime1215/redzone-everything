# Cycle 19 plan (Start Planner), Thu Nov 5, ~10:00 to 11:00 UTC: sync, then NBA, then NFL

**From:** integration/next at 252c723. Since Cycle 18 these merged: #290 (Prime score bug band read, K, epoch; flags off),
#291, #277, #289, #292, the YTTV sync test suite (addresses scrubbed). #280 was already on next.

## Status checks

| Check | Result |
|---|---|
| NBA gates on eval-data-v3 dev vs MIN_DECISIONS 60 | **Not yet.** v3 has no release. The build (TAG eval-data-v3, END 20261105) runs on a free GitHub runner Fri Nov 6 8:30 AM ET, after tonight's games are final. On v1 today: 71 big plays and 778 crunch decisions over 9 nights, so big_live_nba already has more than 60 decisions. The ±5 pt CI is still the gap (about 100 nights). |
| #290 / #291 | Merged, flags off. |
| EXP-109 / EXP-1402 | No result posted yet. Lane 1 has the Kaggle-GPU ladder EXP-107g ready (e701899, T4 x2, $0) and the EXP-103 fix pinned at 193ef77. Both wait on Nick launching them. |
| RC7 sync card | Open, Nick's call (recommended: ship RC7 and grade sync on TNF). |
| RC6.3 (#258 / #246) | Open, Nick's call. |
| Usage | Not readable from the planner. The coordinator checks it against Fri 8 PM ≤ 58%. |

## New finding this cycle (lane 2, #293 / #294)

- EXP-728 `decider.entry.break_tail`: **FAIL** on NBA dev, ~50 to ~47 mid-commercial per 100 against a bar of ≤ 5. Ships off.
- About two thirds of NBA mid-commercial entries are rewinds or urgent entries. The held entries still land 20-30 s early,
  from the 2σ delay doubt plus the 10 s decide tick. **The NBA mid-commercial bar is a sync-precision problem first.**

## Ranked next work (changed orders in bold)

1. **Lane ② (sync, EXP-803):** rescope EXP-803 as "shrink σ": the edge meter plus Prime K feed the delay belief, a finer decide
   tick near the break end, and split NBA TV timeouts from short ones (crew F's 165 s / 195 s key). Pre-register the arms on
   v1 now; grade on v3 when it lands. Land #293/#294 with the integrator.
2. **Lane ③:** grade #293 independently (primegrade + mid-commercial on NBA dev). Freeze the EXP-803 grader before lane 2's arms run.
3. **Integrator:** merge #293/#294 (flag off). Re-read every NBA gate on v3 after the Fri 8:30 AM ET build; report which clear 60.
4. **Crew F:** NBA national-TV schedule from cdn.nba.com (scout #1). It's the input lane 2 needs for the timeout split.
5. **Test writers:** EXP-700/703 on v3 when it exists (already scheduled). No change.
6. **Lane 4:** stage RC7 with the shadow reader on. Ship only on Nick's card. No change.
7. **Lane 1:** report EXP-109 / EXP-1402 when they land, NBA first. No change.
8. **Scout / crews A, B:** FTN no-huddle (A), NFL network per game (F), NBA refs forward-only (B probe), as ranked in #276.

## Ledger

Real tokens unchanged since Cycle 16: crew A 190M, tracking 12.3M, crew B ~0.6M, crew D 0, crew F's NFL break table (derived).
BDB still waits on Nick's Kaggle joins.

## Waiting on Nick

RC7 sync card, RC6.3, the Kaggle-GPU ladder and EXP-103 launches, the feed-lag probe, the BDB joins and Kaggle identity check, keys
(CFBD, Odds API, YouTube, Twitch, Trends, CFL, HF_TOKEN), and camera/mic (EXP-804).
