"""One-off analysis (major/fill.py) behind docs/agents/planner/2026-11-09-major-considerations.md; not part of the app.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import sys
p=sys.argv[1]; s=open(p).read()
ANS='''1. **"Every league, every night" does not hold today.** Of 12,870 games in the past year, a route that has actually worked exists for 33%. Only 21% are on a fast, seekable route. Without college basketball the figure is 40%.
2. **The biggest gap is one route.** 5,031 games (39%) route to ESPN+ events, and the ESPN app has played 0 of 19 here. That covers 79% of NHL and 57% of CBB.
3. **The best fix is new and free.** ESPN Unlimited events have been inside YouTube TV since Sep 1 2026, with DVR, including out-of-market NHL. If they tune, reach goes from 33% to 72%, and NHL from 15% to 88%. None of the earlier planners found this.
4. **Nick's own teams are worse off:** 39% reachable. The Nationals are 151 of 162 with no route. Wizards are 82 of 83 on slow Prime. Capitals are 63 of 83 on the broken ESPN route.
5. **Summer is nearly empty.** June to August, 72 of 92 nights have at most one reachable game at a time, because MLB is 86% unrouted. Only 3 nights all year had no games at all.
6. **NBA is a Prime product.** 79% of games route to Prime, which can't seek and takes 100-125 s to be ready. Standard League Pass allows one stream, and the design needs two.
7. **The app has never run a full NFL Sunday.** Longest Sunday stretch is 1.5 h. On Nov 7 it saw 24 of 371 key moments, and 119 of 155 adb auto-fixes failed. About 9 regular-season Sundays are left.
8. **Rewinds only work where the stick can seek.** The 2-3 minute must-show promise fails on Prime and ESPN routes, where replays are ESPN clips 4.5-14 minutes late.
9. **Prep time and the interface are settled below:** 45 s p90 is the gate and 30 s the target. Keep Planner 2's `BrainOutput`, built by a small model call plus code. No LLM during games.
10. **Avoidable exposure:** eval data with ESPN game summaries sits in public GitHub releases, against the repo's own rule. The phone shows scores 40-66 s before the TV. Swap reached 11.0 GB with the app running.'''
COV='''**Where every game routes (12,870 games, Nov 8 2025 to Nov 7 2026):**

| League | Games | YouTube TV code | Sunday Ticket | Local by search | Prime | ESPN event | No code | Peacock or other app | No route |
|---|---|---|---|---|---|---|---|---|---|
| NFL | 272 | 10% | 68% | 15% | 6% | 0% | 0% | 1% | 0% |
| CFB (FBS) | 981 | 52% | 0% | 15% | 0% | 25% | 4% | 1% | 2% |
| NBA | 1,333 | 11% | 0% | 10% | 79% | 0% | 0% | 0% | 0% |
| CBB (all D-I) | 6,375 | 24% | 0% | 2% | 0% | 57% | 0% | 5% | 11% |
| NHL | 1,440 | 13% | 0% | 2% | 0% | 79% | 7% | 0% | 0% |
| MLB | 2,469 | 5% | 0% | 4% | 0% | 0% | 1% | 3% | 86% |

**Share of games reachable**, removing 157 certain local blackouts (80 Hurricanes on ESPN+, 77 Hornets on League Pass):

| League | Fast and seekable | Reachable today | If ESPN events tune in YouTube TV | Plus MLB.TV |
|---|---|---|---|---|
| NFL | 78% | 99% | 99% | 99% |
| CFB | 52% | 68% | 93% | 93% |
| NBA | 11% | 94% | 94% | 94% |
| CBB | 24% | 26% | 83% | 83% |
| NHL | 13% | 15% | 88% | 88% |
| MLB | 5% | 9% | 9% | 95% |
| **All** | **21%** | **33%** | **72%** | **88%** |
| Without CBB | 18% | 40% | 60% | 93% |
| NFL, CFB, NBA, NHL | 26% | 60% | 92% | 92% |
| **WSH and UNC only (389 games)** | **16%** | **39%** | **54%** | **93%** |

The CBB denominator is every D-I game, most of which Nick would never choose, so the row without it is the fairer headline. The "+ESPN events" column is a ceiling until the test in section 2 passes. ACC Network Extra, which carries many college games, isn't confirmed in the YouTube TV rows. The MLB.TV column also needs an app that works: MLB.TV isn't in YouTube TV, so it would use the ESPN app (0 of 19) or the MLB app (no tuner test here).

**His teams, past year:**

| Team | Games | Routes |
|---|---|---|
| Commanders | 15 | 8 Sunday Ticket, 3 channel code, 3 local by search, 1 Netflix |
| UNC football | 13 | 9 code, 2 no code, 1 ESPN event, 1 local |
| UNC basketball | 33 | 28 code, 3 local, 2 no code |
| Wizards | 83 | 82 Prime (Monumental or League Pass), 1 code |
| Capitals | 83 | 63 ESPN event, 9 code, 8 no code (NHL Network), 3 local |
| Nationals | 162 | 151 no route, 6 code, 5 Peacock |

**How many games can be live at once, per night (365 nights):**

| Peak games live at once | 0 | 1 | 2-3 | 4-7 | 8+ |
|---|---|---|---|---|---|
| Reachable today | 26 | 63 | 63 | 72 | 141 |
| Fast routes only | 58 | 78 | 80 | 79 | 70 |
| Reachable with ESPN events | 26 | 63 | 52 | 37 | 187 |
| Fast with ESPN events | 57 | 67 | 40 | 37 | 164 |
| Reachable with ESPN events and MLB.TV | 3 | 3 | 6 | 49 | 304 |

So Planner 1's whip and block modes (4+ games) are available on 213 nights today, but with fast routes on only 149. ESPN events bring fast routes to 201.'''
NIGHTS='''Only 3 nights in the year had no games at all (July, around the All-Star break). The real problem is nights with games he can't reach.

| Month | Games per night (median) | Peak reachable (median) | Nights with at most 1 reachable | With ESPN events | With ESPN and MLB.TV |
|---|---|---|---|---|---|
| Nov 2025 (from the 8th) | 73 | 12 | 0 of 23 | 0 | 0 |
| Dec | 51 | 10 | 1 of 31 | 1 | 1 |
| Jan 2026 | 55 | 12 | 0 of 31 | 0 | 0 |
| Feb | 42.5 | 11 | 0 of 28 | 0 | 0 |
| Mar | 31 | 9 | 0 of 31 | 0 | 0 |
| Apr | 23 | 5 | 1 of 30 | 1 | 0 |
| May | 16 | 3 | 4 of 31 | 4 | 0 |
| Jun | 15 | 1 | **20 of 30** | 20 | 0 |
| Jul | 15 | 1 | **27 of 31** | 27 | 4 |
| Aug | 15 | 1 | **25 of 31** | 25 | 0 |
| Sep | 15.5 | 3 | 11 of 30 | 11 | 1 |
| Oct | 17 | 6 | 0 of 31 | 0 | 0 |

Jun 27 to Aug 26: 781 games, 670 with no route. This corrects Planner 1's "no one-game night", which sampled Oct 19-Nov 7 only, when four leagues overlap.

What to do:

- **Say so.** Below a threshold of reachable games (start: fewer than 2 at once for most of the night), the phone says "quiet night" and offers the one good game, or yesterday's reel. Don't run the two-stick machinery for nothing.
- **Summer is Nick's money decision.** MLB.TV is the only thing that changes it. It doesn't need deciding before March, and buying it is pointless until an app that carries it plays reliably.
- **Quality isn't reach.** Reachable counts games, not good games. November CBB was mostly blowouts (Planner 1), so a 73-game night can still be dull. Planner 4's importance model is the right tool for this, later.'''
RISKS='''| # | Risk | Likelihood | Damage | What to do |
|---|---|---|---|---|
| 1 | ESPN-event route doesn't work: 39% of all games, NHL at 15% reachable | Certain today | Removes hockey and most college | Step 1 test, then step 4 |
| 2 | First full Sunday fails in new ways (0 run so far; 24 of 371 moments on Nov 7) | High | The flagship product | Dress rehearsal Nov 15, hidden stick only |
| 3 | NBA is Prime: slow, no seek, League Pass may allow one stream | Certain (route); unknown (stream limit) | NBA stays a held-game show | Test both sticks on League Pass. Plan NBA as hold-and-reel. Run Prime seek test |
| 4 | Plan is too large for the time left: 221-274 h, 9 Sundays | High | Nothing finishes before the playoffs | Do only the 72-84 h list in Ordered steps |
| 5 | His teams hit the worst routes: Capitals, Wizards, Nationals | Certain | Hurts the top priority | Steps 1, 3 and 4. Route confidence shown per game |
| 6 | Summer has almost nothing reachable | Certain, June to August | Product idle 3 months | Quiet-night mode; MLB.TV decision |
| 7 | Mac pressure (11.0 GB swap, on battery during games) | High while agents share it | Slow OCR, late flips, sleep risk | Decision 2; preflight; footprint log |
| 8 | Input flip has no readback since SmartThings was dropped | Medium | A wrong-input flip can't be detected | Re-measure on local path. Use CEC state. Treat unknown input as no-cut |
| 9 | Rewinds promised where sticks can't seek | Certain on Prime and ESPN routes | Broken must-show promise | Per-route deadline, step 5 |
| 10 | Spoilers: phone, alerts, score bug before a rewind | High | Ruins the delay advantage | Step 6; Focus mode; rewind-first rule |
| 11 | App or OS update breaks scrub or adb overnight | Medium, ongoing | A dead night, no warning | Version watch; keep flags and alternates |
| 12 | Carriage dispute removes a network, as in 2025 (15 days) | Low-medium per year | Weeks without ESPN or similar | Accept; routing stays data-driven |
| 13 | Stick dies and only Vega can replace it | Low now, rising | Ends the two-stick design | Fire OS only. Spare is Nick's choice |
| 14 | Account action for automation | Low | Could touch his Google account | Personal use, no high-rate Lounge, check which account |
| 15 | Public ESPN data in releases | Low chance of complaint, trivial fix | Takedown; CI breaks | Decision 3, step 9 |
| 16 | Unofficial feeds change or throttle | Medium | Stale data, no cuts | Daily schema check; `trust_order` fallbacks |
| 17 | Viewer fatigue at high cut rates | Unknown | "Hella cuts" stops being fun | Measure later; `switch_frequency` setting exists |'''
for k,v in [('@@ANSWER',ANS),('@@COVERAGE',COV),('@@NIGHTS',NIGHTS),('@@RISKS',RISKS)]:
    assert k in s; s=s.replace(k,v)
R=[("Fourteen decisions after removing one duplicate (Planner 2's 1 and Planner 4's 1 are the same). Each default is what happens if Nick says nothing.","Fifteen decisions after merging one duplicate (Planner 2's 1 and Planner 4's 1 are the same). Each default is what happens if Nick says nothing."),
("\nItem 2 is split out of item 1 because it costs money, so the count of separate choices is 15 rows and 14 topics.\n",""),
("MLB stays national-only unless he buys MLB.TV before March.","MLB stays national-only unless he buys MLB.TV before March, and not before an app that carries it has played a game here."),
("| NHL | ESPN+ out-of-market |","| NHL | ESPN+ out-of-market ([b26](#b26)) |"),
("MLB's app has a 30 s delay option ([b13](#b13)), too short for this","MLB's app has a 30 s delay option ([b13](#b13)), too short for this. Apple Sports is reported about 10 s ahead of TV (snippet-only, [b27](#b27))"),
("Vega models are the 2nd-gen HD, 4K Select and 3rd-gen 4K ([b12](#b12)).","Vega models are the 2nd-gen HD, 4K Select and 3rd-gen 4K ([b12](#b12)), and future sticks are reported to be Vega (snippet-only, [b20](#b20))."),
]
for a,b in R:
    assert a in s,a[:30]; s=s.replace(a,b)
assert '@@' not in s
open(p,'w').write(s)
