# Cycle 21 plan (Start Planner), Thu Nov 5, ~12:00 to 13:00 UTC: short cycle, orders unchanged

**From:** integration/next at 4248418.

| Item | State |
|---|---|
| #298 EXP-803 arms A1 (defer a break tail within one tick) and A3 (NBA TV timeout length) | Merged, flags off. Grader v23 is refrozen with A1's deferred-cut timing. Waiting on the v3 grade. |
| #296 (EXP-803 pre-registration label fix) | Merged. |
| #300 (crew E, EXP-1404: re-grade EXP-1402 commentary importance on the outcome label) | Open. The integrator lands it. Lane 3 reads the grade, NBA first. |
| eval-data-v3 | Not released. Build Fri Nov 6 8:30 AM ET. |
| EXP-109 / 107g | No results. They wait on Nick's launches. |
| RC7 sync card, RC6.3 | Open, Nick's calls. |
| Rule #1 on the North Star | Mid-play entries are 0.48 per 100 (8 in 1652 landings), all NHL: NFL 0, NBA 0. NHL is out of focus by Nick's 2:00 AM order. It stays on the board, but no lane is pulled off sync/NBA for it. |

Orders are unchanged from Cycle 19 and 20.

## Addendum, 12:05 UTC: Rule #1 is raised above sync

The coordinator's read stands: Nick put sync above everything **except** Rule #1, and never joining mid-play is absolute. So the
8 NHL mid-play entries (dev-test, measured feed lag; 0.48 per 100, CI 0.20-0.89) outrank the NBA-first order.

| Step | Owner | What |
|---|---|---|
| 1 (now) | Test writers | EXP-700 autopsy on the 8 NHL entries using v1 data, no v3 needed. For each: night, game, which gate let it through (feed-lag belief, the dead-ball signal, resync), and whether NBA/NFL share that path. |
| 2 | Lane ② (DECISION owner, the cutting lane) | A minimal fix sized from the autopsy, behind a flag, with a new id from the 200s. **It must not touch the EXP-803 critical path.** If the cause is the shared delay belief, coordinate with EXP-803 rather than fork it. |
| 3 | Lane ③ | Grade the fix: 0 mid-play entries on NHL dev with no rise in any league, plus Rule #1's other guards (dead air, plays missed on entry). |
| 4 | Integrator | Land it, and default it on only after lane 3's PASS (decisions.md line). |
