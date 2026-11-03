# Decision core: findings and next steps (2026-11-03)

## Where NFL dead air comes from (integration/next d702565, 16 slates, 14,890 s of dead air)
By stoppage on screen: team/official timeouts ~6,300 s, after a score (TD/FG -> kickoff) ~4,970 s,
two-minute warning ~1,460 s, end of period / injury the rest.
By the decision in force at the time:
- ~3,000 s: the gate says "delay not calibrated, official breaks only". The simulator calibrates each
  channel once at kickoff and lets sigma drift 3 -> 9 s; live, Kalman sync (PR #8) keeps it near its 4 s floor.
- ~2,400 s: "want RedZone / a play is on screen": the 2-sigma + 3 s safety margin after the last play.
- ~2,000 s: nothing else on (no other live game, no RedZone) and no replay fits the cautious break budget.
- ~1,800 s: landing back home / at crunch before the ad ends (crunch returns come 2 sigma + cost early).
- Grader detail: a break counts as dead until the WALLCLOCK of the next play, which is that play's end,
  so the first snap after every break (~7-10 s) is billed as commercial.

## Same decider with the delay held at the sync floor (sigma 4 s, no drift)
big live 29.7 -> 54.6, big seen 95.4 -> 98.3, crunch 71.4 -> 72.7, your team 100/100, 0 cuts,
0 mid-play entries, 0 missed on entry; dead air 8.6 -> 9.7 (more visits, more live).

## Knob probes (not shipped; defaults unchanged)
| knobs | dead air | big seen | big live | crunch |
|---|---|---|---|---|
| defaults | 8.6 | 95.4 | 29.7 | 71.4 |
| core.dead_air_penalty=40 | 8.0 | 94.6 | 29.7 | 70.0 |
| core.break_value_frac=0 | 8.0 | 94.8 | 29.7 | 70.0 |
| sync-floor sim + dead_air_penalty=40 | 9.3 | 98.5 | 56.3 | 72.3 |

## Next
1. Commercials -> 0: crunch returns by DVR rewind instead of arriving early; fill "nothing else on" with
   replays/reel; consume reasoner must_see / p_big_soon in rate() with break awareness (PR #17).
2. Crunch overlaps: core.league_priority (NFL > CFB > NBA > NHL > MLB), live -> catch-up reel -> rejoin plan.
3. NBA: 38 mid-play entries / 18 missed on entry.
4. Hard rules into guards; auto-tune SPACE on core.* knobs.
