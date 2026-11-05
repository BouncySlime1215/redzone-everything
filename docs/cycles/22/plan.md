# Cycle 22 plan (Start Planner), Thu Nov 5, ~13:00 to 14:00 UTC: Rule #1 package ready, sync waits on v3

**From:** integration/next at fce5bd1 (grader v24: `--set` under league_overrides writes a flat key; EXP-803 A2 smoke read void).

## Rule #1 (NHL), done this hour, flags off

| Step | Result |
|---|---|
| EXP-700 autopsy (#302) | The 8 NHL mid-play joins split into 6 first picks ("start" was exempt from entry timing) and 2 crunch_now=rewind live fallbacks. NBA and NFL: 0. |
| Root cause of the leftovers | The unmeasured feed-lag prior is 10 s, shared by every league, plus one faceoff that arrived 87 s late against a measured 59 s. |
| EXP-303 grade (#304, lane 3) | Option B = EXP-739 start_timed + EXP-740 crunch_now wait + EXP-211 lag_floor_s 71.3 (NHL q90): **PASS**. 0 NHL mid-play on all 6 runs, plays missed 6 to 0, dead air ±0.17. The cost is NHL big plays live −1.81 [−2.98, −0.57] (plain). It's the third arm on the same dev nights, so the locked read decides shipping. |
| Ship | #302/#303/#304 land with flags off. Turning the NHL defaults on is Nick's card (recommended on). |

## Sync

- #306 EXP-803 A5: a per-league prior for unmeasured games, default unchanged. On NBA v1 it's identical to base. Grade on v3.
- #305: YTTV sync tests (web reference clock, Stats for nerds, web vs stick alignment).
- eval-data-v3: not released. Build Fri Nov 6 8:30 AM ET. EXP-803 A1-A5 are graded then.

## Merge order for the integrator

#302, #303, #304, #306, #305, #300, #299, #301 (all flags off or docs).

## Unchanged

RC7 sync card and RC6.3: Nick. EXP-109 / 107g: waiting on Nick's launches. Scout cycle 19: nfl-depth-charts (crew B, starter
changes only) and nba-cup-stakes (derived). gabriel1200/site_Data is red.
