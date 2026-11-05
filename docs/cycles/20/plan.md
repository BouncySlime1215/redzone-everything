# Cycle 20 plan (Start Planner), Thu Nov 5, ~11:00 to 12:00 UTC: short cycle, orders unchanged

**From:** integration/next at af86a7e.

## Since Cycle 19

| Item | State |
|---|---|
| #293 / #294 (EXP-728 break_tail) | Merged, flag off. FAIL was reproduced independently by lane 3: 51.6 to 50.0 (plain) and 49.7 to 47.2 (resync) per 100, against a bar of 5. |
| EXP-803 pre-registration | #296: arms A0-A4. |
| EXP-803 grader freeze | #297 delaygrade, frozen as grader v22 with the nba-delay-precision Holm family, before any arm ran. |
| EXP-803 arms | #298 open: A1/A3 flags off, A2 what-if. Built to grade on v3. |
| eval-data-v3 | Not released (tags v1, v2 only). Build Fri Nov 6 8:30 AM ET. |
| EXP-109 / 1402 / 107g | No results. They wait on Nick's launches. |
| RC7 sync card, RC6.3 | Open, Nick's calls. |

## Data notes

- Crew F: the NBA national/local split is already in #250 (nba_cdn: long timeouts 208 s national vs 181 s local, plus
  game_networks.json). No cdn scrape needed. The NFL network join is held, since per-network differences are ~10 s and NFL
  in-break entries are an entry-policy problem (EXP-205).
- Scout (#276): the live NBA addition is nba-datanba-coords (probe, ~11M, shows transition). nfl-ngs-weekly (~1.8M) goes to crew B.
  Shotdetail is dropped (already in pbp shards) and matchups is parked (per-game only). Newly red: the PFR-derived nflverse tables
  (snap_counts, pfr_advstats). The new-information estimate is 3.49B.

## Orders

Unchanged from Cycle 19. Lane 2 lands #298 flag off and grades A1-A4 with delaygrade the morning v3 lands. Lane 3 confirms that
grade. The integrator merges #298 when green and re-reads the NBA gates on v3. Lane 4 stages RC7 and ships only on Nick's card.
