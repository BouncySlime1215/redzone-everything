---
id: P-PRODUCT
title: "The product: our own RedZone for every league, every night"
date: 2026-11-09
stream: planner
author: planner 1 (product per league)
status: draft
supersedes: []
sources: 20
verified: 17
affects: [redzone/default_config.yaml, redzone/crunch.py, redzone/lab/leverage.py, redzone/scoring/engine.py, redzone/decider/cuts.py, redzone/decider/buffered.py, redzone/reel.py]
gate: []
---

# The product: our own RedZone for every league, every night

## Answer in 10 lines

1. **The show is one ranked queue of moments, not six sports.** Every league feeds the same three tiers: Tier 1 must show, Tier 2 setup, Tier 3 highlight. Per-league detectors fill the tiers; one show engine airs them.
2. **RedZone's method is simple to copy:** cut in at a scoring threat, show every touchdown, leave any game that goes to break, narrow to close games late, replay what it missed seconds later, never show nothing. The sources below agree on these points.
3. **Our edge is that we run 60-90 s behind and the play-by-play already knows the outcome.** RedZone guesses; we choose. RedZone's own touchdown replays land about 16 s to 60 s late, so a delayed show is already how it works.
4. **The rate is set by events and game count, not by a target number.** NFL must-see (Tier 1+2) per 10 minutes: 1.1 with one game live, 3.2 with 2-3, 6.4 with 4-7, 9.7 with 8+ (208 dev games).
5. **Four games at a time is the right near-term shape for football.** 4 NFL games produce about 13.8 Tier 1+2+3 moments per 10 minutes, matching the modeled 13-15 jump stick ceiling. Every major play of the held games fits.
6. **Basketball and hockey cannot copy that.** Hanson says so directly. NBA must-see is crunch-heavy; NHL goals have no warning. From the last faceoff to a goal is a median 52 s and only 71% fit inside 90 s, so about 3 in 10 goals can only be replays.
7. **"Hella cuts" means every scene pays off:** a scene ends the moment its play ends, no cut without something to show, no dead air, and cuts come in bursts when events cluster. Football targets 10-14 cuts per 10 minutes on a full slate; basketball 4-8.
8. **Crunch time lowers the cut rate on one game and raises it across two.** Two NFL games are in crunch together 25% of crunch time, so alternate them at dead balls. It is less common elsewhere (3-15%).
9. **"Nothing is happening" is rare on full nights and common on small ones.** All games are on break 0-2% of the time with 8+ live, but 35-54% with one game live. The show needs a fixed fill ladder, ending in a decision only Nick can make (decision 3).
10. **Brain vs code:** the must-see list, join rules and fill ladder are code. Which moment next, how long to hold, and what is worth a catch-up clip are the brain. See the table.

## The analysis

### 1. How RedZone actually works (from producers and hosts)

| Practice | What the source says | Source |
|---|---|---|
| Trigger | Cuts to any game reaching the 20. Strong drives and important games can get focus outside it. | [DirecTV](#b8), [Wikipedia](#b7) |
| Every touchdown | "We show every touchdown from every game" (Hanson). Kaplan: even at 49-14. 64 TDs in one broadcast. | [NPR](#b5), [Grantland](#b1) |
| Not all live | Producers try for live, but some TDs air after a 30-60 s shift. A rolled-back replay airs about 16 s after the play. | [Wikipedia](#b7), [NPR](#b5) |
| Replays | Turnovers, deep passes, long runs, return TDs. Outcome-changing long field goals. Missed moments replayed moments later with a line of context. | [Wikipedia](#b7), [Gear Patrol](#b2) |
| Break handling | Featured game goes to break: switch to the next most interesting game. | [Wikipedia](#b7) |
| The 40 seconds | Hanson: after a play there are about 40 s before the next snap, enough for one or two plays from elsewhere. He says this cannot be done in basketball or hockey. | [FOS](#b6) |
| Late game | Q4 focus narrows to one-possession games (the "Witching Hour"). OT gets priority but never at the cost of missing touchdowns elsewhere. | [Wikipedia](#b7), [Grantland](#b1) |
| Halftime | Moves to games still in Q2. Second-half kickoffs get priority. | [Wikipedia](#b7) |
| All on break | Back to studio: commentary, replays, stats. One observer saw a pre-planned playoff-picture graphic when all three games broke at once. | [Wikipedia](#b7), [Lyons](#b9) |
| Multi-box | 2, 3, 4, 8 or 10 boxes when several games are hot together. | [LAist](#b4), [Wikipedia](#b7) |
| Pace | Feeds change "up to three times a minute" at peak. One host covered five scenes in about 90 s. | [NPR](#b5), [SI](#b3) |
| Preparation | Morning meeting per game: storylines, players to watch. A "1" sticky note marks one-possession games. | [Grantland](#b1), [NPR](#b5) |
| Finish | A touchdown montage ends the day, about 10 minutes. | [NPR](#b5), [Grantland](#b1) |
| Who decides | Lead producer runs the show, a host pushes ideas, 7-15 others spot and verify. Host and producer often disagree. "Sometimes you just have to guess." | [NPR](#b5), [Grantland](#b1), [Gear Patrol](#b2) |

Other leagues' whip-arounds tell us what changes:

- **NBA CrunchTime** is built on crunch time when the result is in doubt, uses highlight packages when a live look isn't possible, and turns to standout stat lines and betting angles when nothing is close ([Uproxx](#b10)). A basketball whip-around cannot show every score, so it picks the biggest plays ([Awful Announcing](#b11)).
- **Goal Rush** (soccer) ends up as a main game plus reactive cut-aways, because goals can't be predicted. A reviewer called it more of an in-game highlights show ([Engadget](#b15)). That is the honest model for hockey.
- **Frozen Frenzy** staggers starts by at least 15 minutes to keep action spread out ([SVG](#b13)). We can't change start times, but it confirms that clustered starts are the enemy.
- **Goal Line and Fast Break** apply the same recipe to college ([Awful Announcing](#b17), [Awful Announcing](#b12)). 15+ Saturday games at once is normal.
- I found no written cut rulebook from any producer. Priority rules above are reconstructed from interviews and descriptions.

**Correction for the repo:** [redzone-numbers](../../research/2026-11-08/redzone-numbers.md) and viewer-experience both say no switch rate is published. NPR's 2014 piece gives a peak of three a minute (30 per 10 minutes). It is a reporter's description, not a measurement.

### 2. Three things we do differently

1. **We know the future.** The feed tells us which red-zone trip scores before it airs. Rule: use that to pick, but keep stops and failed 4th downs as Tier 1 or 2, or the show becomes a spoiler machine where every cut scores.
2. **No boxes.** One picture on one TV (Samsung Multi View is likely unsupported on this set per samsung-tv-switching). Two hot games alternate at dead balls instead.
3. **Personal.** His teams and bets come first, and we have no host. A cut has to explain itself by what's on screen.

### 3. Measured here: must-see rates per league

Source: `data/archive`, ESPN play-by-play wall clocks, read only. **Locked and fresh nights are excluded** (NFL before 20251209, NBA and NHL before 20261029, per `release_gate.py` HELDOUT) because these numbers set thresholds. CFB, CBB and MLB have no locked set yet, so their nights were all read and their future locked block must use later nights.

Sample warnings: CFB is one Saturday (17 games). CBB is 5 November nights (37 games), mostly early-season blowouts: only 14% reached crunch. MLB is 54 games, mostly playoffs, plus one 15-game final Sunday. Treat those three as rough.

Tier definitions used:

| League | Tier 1 must show | Tier 2 setup | Tier 3 highlight |
|---|---|---|---|
| NFL, CFB | Touchdown, turnover | Red-zone trip (first snap inside the 20), field goal, 4th-down try | 10+ yard gain, 3rd-and-5+ conversion, 5+ point win-prob swing |
| NBA, CBB | Made shot in the last 5:00 (CBB 2nd half) or OT within 5 | 8-0 run, 2nd-half lead change, 10+ point win-prob swing | Dunk, made 3, clutch turnover, 5-9 point win-prob swing |
| NHL | Goal, penalty shot | Power play start, final 2:00 within 1, OT start | Shot on goal, fight |
| MLB | Home run, run-scoring plate appearance | Bases loaded, or runner on 2nd/3rd in 7th+ within 3, 5+ point win-prob swing | Other at-bat with runner in scoring position |

Basketball win-prob swings of 5+ points fired about 1.8 times per game per 10 minutes, too noisy for Tier 2 (matches leverage-excitement's warning that win prob is noisy).

**Per live game, per 10 wall-clock minutes:**

| League | Games / nights | Avg length | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|---|---|
| NFL | 208 / 44 | 3.08 h | 0.39 | 0.77 | 2.29 |
| CFB | 17 / 2 | 3.45 h | 0.44 | 0.68 | 1.86 |
| NBA | 68 / 9 | 2.43 h | 0.17 | 0.78 | 4.30 |
| CBB | 37 / 5 | 2.10 h | 0.03 | 0.34 | 2.19 |
| NHL | 66 / 10 | 2.56 h | 0.42 | 0.44 | 3.27 |
| MLB | 54 / 24 | 2.93 h | 0.35 | 0.56 | 0.54 |

**By games live, per 10 minutes.** Each cell: Tier 1+2 mean, fewest cuts to see them all (p50/p90), and share of windows with zero Tier 1+2.

| League | 1 live | 2-3 live | 4-7 live | 8+ live |
|---|---|---|---|---|
| NFL | 1.1, 0/0, 33% | 3.2, 1/3, 4% | 6.4, 4/8, 1% | 9.7, 7/12, 0% |
| CFB | 1.2, 0/0, 34% | 2.3, 1/2, 0% (n=4) | 5.4, 3/6, 2% | no data |
| NBA | 0.9, 0/0, 56% | 2.5, 0/2, 29% | 5.1, 1/5, 7% | 7.3, 5/8, 0% |
| CBB | 0.2, 0/0, 79% | 0.6, 0/0, 58% | 1.7, 0/1, 24% | 9.5, 6/9, 0% (n=4) |
| NHL | 0.9, 0/0, 43% | 2.1, 0/2, 16% | 4.2, 2/6, 9% | 7.8, 6/11, 5% |
| MLB | 0.9, 0/0, 52% | 1.7, 0/2, 18% | 10, 9/9, 0% (n=2) | 13.3, 12/14, 0% (one night) |

These match the earlier NFL finding (median 5 cuts per 10 minutes on 4+ games) but split it by slate size, which is what changes the show.

**Collisions:** share of Tier 1 events with another must-see in a different game within 45 s: NFL 42%, CFB 43%, NHL 36%, NBA 30%, CBB 25% (12 events), MLB 25%. Collisions are the normal case, which is why a replay path is required, not optional.

**What 4 held games produce per 10 minutes** (4 x the per-game rate; near-term block of 2-4 games for a few minutes):

| League | Tier 1+2 | Tier 1+2+3 | Stick model ceiling (P1-MAXRATE) | Result |
|---|---|---|---|---|
| NFL | 4.6 | 13.8 | 13-15 | Every major play of the 4 held games fits |
| CFB | 4.5 | 12 | not modeled (NFL rules) | Should match NFL |
| NBA | 3.8 | 21 | 2-4 (3.5-4.3 with 0.5 s stick error) | Tier 1+2 only; Tier 3 goes to catch-up reels |
| NHL | 3.4 | 17 (mostly shots) | not modeled | Tier 1+2 only |
| MLB | 3.6 | 5.8 | not modeled | Tier 1+2 fits |

**One-TV check (estimate, ignores stick tuning):** events aired first-come, clip plus 2.5 s flip, with 30 s of slack. Clips assumed: NFL 27 s (P1-MAXRATE), NBA 35 s, NHL 60 s, MLB 30 s.

| League | Tier 1 aired | Tier 1+2 aired | Tier 1+2 with 5 min slack |
|---|---|---|---|
| NFL | 99.4% | 94.0% | 100% |
| CFB | 98.6% | 95.7% | 100% |
| MLB | 97.8% | 92.4% | 100% |
| NBA | 92.6% | 81.1% | 97.8% |
| NHL | 90.8% | 82.6% | 98.7% |

Screen time is not the limit. Getting a stick parked at the right moment is.

**When a game gives you a join point:**

| League | Measure | Result |
|---|---|---|
| NFL / CFB | Between plays (record gap median) | 43 s / 41 s, consistent with Hanson's 40 s |
| NBA | Previous shot or rebound to a clutch make | median 20 s; 72% within 30 s, 87% within 60 s |
| NBA | Previous dead ball to a clutch make | median 74 s, p90 212 s; only 60% within 90 s |
| NHL | Faceoff to goal | median 52 s, p90 159 s; 54% within 60 s, 71% within 90 s |
| NHL | Faceoffs per game per 10 min | 3.8 |
| MLB | Plate appearance length | median 73 s (p25 46, p90 144) |

The NBA line is the reason for decision 2.

**Crunch** (NFL/CFB last 5:00 of Q4 or OT within 8; NBA last 5:00 or OT within 5; CBB last 4:00 or OT within 6; NHL last 5:00 of P3 within 1, or OT; MLB 8th+ within 2, or extras):

| League | Games reaching crunch | Wall minutes (p50/p90) | Crunch time with 2+ games in crunch |
|---|---|---|---|
| NFL | 57% | 19 / 35 | 25% |
| NBA | 50% | 11 / 23 | 8% |
| NHL | 55% | 11 / 19 | 13% |
| CFB | 41% | 23 / 31 | 5% (thin) |
| CBB | 14% | 17 / 18 | 15% (thin) |
| MLB | 69% | 28 / 46 | 3% |

**All games on break at once** (gap of 90+ s between plays is a stand-in for break; it also catches reviews and long timeouts):

| League | 1 live | 2-3 | 4-7 | 8+ |
|---|---|---|---|---|
| NFL | 41% | 10% | 2% | 1% |
| CFB | 54% | 6% | 6% | no data |
| NBA | 44% | 11% | 1% | 0% |
| NHL | 44% | 15% | 11% | 1% |
| MLB | 35% | 10% | 0% | 0% |
| CBB | 38% | 8% | 0% | 0% |

**Real nights, Oct 19 to Nov 7 2026** (schedule only; CFB and CBB undercount since the archive holds only games pulled). 20 of 20 nights had games, 19 of 20 spanned at least two leagues, total games ranged 5 to 36, and peak games live ranged 5 to 27. **This sample had no one-game night**, so single-game mode is mostly the tail of a night (inference from this 20-night sample; other parts of the year will differ).

### 4. The show engine

Six scene types, the same in every league:

| Scene | What it is | Length |
|---|---|---|
| Live hold | Stay on a game and let plays run | one play to several minutes |
| Whip | One-play look-in, then away | one play |
| Look-in | Stay on a scoring threat until resolved | until the setup ends |
| Rewind | Just-happened play from the buffer | one play |
| Reel | 5+ clips back to back, 30-60 s (Nick's catch-up) | 30-60 s |
| Fill | Something that isn't a live game when nothing is available | as needed |

**Priority ladder (top wins, league order breaks ties at each step):**

1. His team's Tier 1 or crunch (existing `decider.crunch.mine_first`).
2. Overtime and crunch, but never at the cost of a Tier 1 elsewhere (the RedZone rule). A Tier 1 in another game becomes a rewind.
3. Tier 1 in progress or about to happen.
4. Tier 1 that just happened and wasn't seen: rewind.
5. Tier 2 setup.
6. Tier 3, only when the next scene has room.
7. Best game by suspense (brain).

**A good cut (every league):** land on the dead ball just before action, stay for at least one complete play, leave the moment the play ends, and don't return to the game inside 60 s without a new Tier 1 or 2 (no ping-pong).

**How "hella cuts" feels:**

- Scenes end when the play ends, so no huddle, no walk-back, no commercial.
- A cut needs a payoff. No looking in just to check.
- Cuts cluster: bursts when events pile up (NFL p90 is 12 per 10 minutes on a full slate), then a held scene.
- Targets are outputs. The bar is must-see seen, with the band as a guide.

| League | 1 live | 2-3 | 4-7 | 8+ | Scene length |
|---|---|---|---|---|---|
| NFL, CFB | stay | 2-4 | 6-10 | 10-14 | 20-35 s |
| NBA, CBB | stay | 2-4 | 4-6 | 5-8 | 30-60 s |
| NHL | stay | 1-3 | 3-6 | 6-10 | 40-90 s |
| MLB | stay | 1-3 | 4-8 | 8-14 | 25-70 s |

These bands are proposals built from the p50-p90 cuts needed plus Tier 3 fill. Today's sticks measure 1.7-1.9 jumps per 10 minutes in the model, so they need the seek and prep fixes first.

### 5. Per-league spec

**NFL.** Tier 1 live or rewound within the window. Land after the whistle, ideally after the replay, seconds before the snap (Nick's bar). Stay for one play per whip; a red-zone trip stays until it ends. Two-minute drill and OT hold, unless a Tier 1 elsewhere. Crunch in two games: alternate at dead balls. Halftime: games still in Q2, then second-half kickoffs first (RedZone's rule). Close-and-late games get priority over blowouts, but every TD still airs.

**CFB.** NFL rules. Differences: more games in worse TV spots, so filter a 15+ game Saturday to the about 8 that matter (UNC first, ranked teams, close games). OT possessions start in scoring range, so every OT possession is Tier 1. Tie with NFL only when both are in crunch (Nick). 7.0 TDs per game measured vs 5.2 NFL, and 4-7 game windows have about 5.4 Tier 1+2 per 10 minutes.

**NBA.** Crunch is the show (only 0.17 Tier 1 per game per 10 minutes). Early game: hold 1-2 games, cut at timeouts and free throws, use reels for dunks and threes. Land at a dead ball or the walk after a make or miss (Nick). Free throws give a natural leave. Late: crunch games held; a Tier 1 elsewhere becomes a rewind. Runs (8-0) are the Tier 2 that lifts a middling game. Buzzer shots at end of quarter are worth adding to Tier 2 (not measured).

**NCAA basketball.** NBA rules, plus media timeouts at fixed spots (306 official TV timeouts across 37 games, about 8 per game) that act as planned cut points. November is mostly blowouts, so Tier 1 is near zero; apply a relevance filter (UNC, ranked, close) and rely on the next league. March is a different show (16 games at once, Fast Break exists for it): re-measure then, since the rate here will be far off.

**NHL.** Nick's list: power plays, goals, overtimes. Land at a faceoff. A power play start is the red-zone trip: go there at the faceoff. Goals: 71% are within 90 s of a faceoff, so the live path can reach them; the other 29% have no dead-ball entry and air as rewinds or reels. 5-on-3 and the last 2:00 within one get the hold. OT and shootouts: each attempt is its own dead-ball moment, hold through. Reuse `nhl_goal_imminent` in `scoring/engine.py`.

**MLB.** Land between pitches, preferably before a plate appearance with runners on. Bases loaded or a late runner in scoring position is the red-zone trip. No-hitter bid from the 7th is Tier 1 (not in my counts). Off from November to March. Lowest league priority, so most of its value is on nights with little else on. Big Inning (snippet-only) uses live look-ins and up to four boxes.

### 6. Slates and modes

| Situation | Mode | Behaviour |
|---|---|---|
| 0 games | Off night | Reel of yesterday's best, or off |
| 1 game live | Single | Stay on it; the hidden stick builds the reel for breaks |
| 1 top-league game plus lower-league games (MNF, TNF, late NFL) | Anchor | NFL holds the TV; during its breaks, run other games' Tier 1 and 2 |
| 2-4 live | Whip | All in rotation |
| 5-16 live | Block | Hold 2-4 for a few minutes, then rotate; the rest become rewinds and reels |
| 16+ live | Filtered block | Relevance filter down to about 8 first |

**Overlapping leagues:** Tier first, then league order. An NFL Tier 2 outranks an NBA Tier 2, but an NHL OT goal outranks an NFL first-quarter field goal attempt (decision 1). Example from the archive: Sunday Nov 1 had 12 NFL, 4 NBA and 5 NHL games.

**When nothing is happening (fill ladder, top wins):**

1. Any live game not on break: go to the best one.
2. Unseen Tier 1 or 2 from anywhere: reel.
3. Tier 3 highlights from live games.
4. Last game's recap or the night's scores.
5. Off nights and all-on-break with nothing left: decision 3.

End every night with a **touchdown-style montage of the day** (RedZone's closer, cheap once reels exist).

## BRAIN vs CODE

| Decision | Owner | Why |
|---|---|---|
| Must-see list per league (Tier 1, 2, 3 definitions) | **CODE** | One auditable list in config; the brain's labels and tests read it |
| Event detectors from feeds | **CODE** | Deterministic parsing of play-by-play |
| Rule #1 join and leave gates | **CODE** | Hard rule; brain can only be stricter |
| Scene length caps, anti-ping-pong, 0 commercial seconds | **CODE** | Limits, not taste |
| Slate mode selection | **CODE** | A table from games live and league mix |
| Queue games fast / stick pre-positioning | **CODE** | Mechanics (other planner) |
| Priority ladder steps 1-6 | **CODE** at first, brain may reorder inside a step | Auditable and explainable |
| Which of several moments next | **BRAIN** | Suspense, story, Nick's taste |
| How long to hold a game | **BRAIN** (inside code caps) | Reading the game |
| Which Tier 3 is worth airing | **BRAIN** | Quality judgment |
| Reel content and order | **BRAIN** (inside the 30-60 s format) | Story |
| Relevance filter weights for big slates | **BRAIN**, code default | Learns his taste |
| Storylines (milestones, playoff stakes, bets) | **BRAIN** | RedZone's morning meeting, automated |
| Fill ladder | **CODE** | Must never hang on a model |

Rule of thumb: if breaking it would be visible as a bug, it's code. If it would be visible as a boring show, it's the brain.

## Major considerations and risks

- **Sample size:** CFB, CBB and MLB numbers are thin (see section 3). Re-measure before setting their gates.
- **Locked data:** this report excluded locked nights, but [redzone-numbers](../../research/2026-11-08/redzone-numbers.md) used all 19 Sundays, 10 of which are in the locked window. The P1 pace bar in PLAN section 3 cites that note, so the process planner should check it.
- **Spoilers from the oracle:** picking only successful plays would flatten suspense. Keep stops and fails in the must-see list.
- **Comprehension cost:** no study covers this speed (viewer-experience). "Hella cuts" may reach a limit. Test on Nick, on real nights.
- **Grader gaps:** college leagues read the pro rows in `lab/leverage.py` and the crunch table, so they have no college-specific numbers, and system-map lists college and MLB as not covered by several entry rules. Tier 1 seen per league needs its own gate.
- **Crossing into other planners:** NBA clips are long because the model must run to the next dead ball; leaving after a shot changes that sharply (timing planner, decision 2). Queue speed and stick prep own the ceiling (queue planner). Single-game mode may want more than 90 s behind to skip whole breaks (timing planner).
- **Commercial proxy:** the 90 s gap measure is not a true break flag; no feed has one (live-data-feeds).
- **Rights and legal:** personal use only, as in the repo's existing limits. Nothing new here.

## Decisions needed from Nick

1. **Moment tier first, league order as the tiebreak?** Recommended. The alternative is strict league order, which would keep the TV on NFL through dull stretches while an NHL overtime goal goes unseen.
2. **Basketball leave rule: may we leave right after a made or missed shot, same as the join rule?** Recommended. Your bar allows this for joins. The stick model holds NBA at 2-4 jumps per 10 minutes partly because each clip must run to the next dead ball, so this is the biggest NBA pace lever.
3. **All games on break and nothing left to show: what's on screen?** Recommended: a recap or highlights clip on the hidden stick. Other options are a commercial on screen (breaks your 0-second bar) or a blank score card.

## Ordered steps

| # | Step | Hours (estimate) |
|---|---|---|
| 1 | One must-see list in config: Tier 1/2/3 per league, as in section 3 | 3 |
| 2 | Event detectors for each league from live feeds, with tests on dev nights | 8-10 |
| 3 | Move the measuring scripts into `scripts/` and run them weekly; re-measure CBB in January and March, CFB after more Saturdays | 2 |
| 4 | Slate mode selector and relevance filter default | 4 |
| 5 | Scene types and limits in the decider (scene length, anti-ping-pong, payoff check), flags off | 6-10 |
| 6 | Fill ladder and end-of-night montage on top of `reel.py` | 4-6 |
| 7 | Per-league product scorecard: Tier 1 seen, cuts per 10 minutes vs band, dead seconds | 6 |
| 8 | Hand the tier list to the brain as its label source; the brain then replaces the ladder's steps 3-7 inside code limits | later, brain phase |

About 33-41 hours total before the brain phase, all estimates. Near-term priority per Nick stays queue speed; steps 1 and 2 are what the queue reads, so they go first.

Measuring scripts (not in the repo yet): `/private/tmp/claude-501/-Users-nick-matta-Library-Application-Support-Claude-scratch-workspaces-9fd867a4-1593-493b-b94f-1f3aebd68493-688aed4e-2f6f-4b8a-9ed9-aa097c310a36-scratch-2026-11-08-ce8f23/9f563e62-788a-4e8b-a325-b193c8b21949/scratchpad/product/` (`rates.py`, `r2.py`, `r3.py`, `nights.py`).

## Bibliography

Opened and read unless marked snippet-only.

1. <a id="b1"></a>"A Trip Inside the RedZone", Grantland, Nov 2012. https://grantland.com/features/watching-day-nfl-games-redzone-studio/ (opened)
2. <a id="b2"></a>"Behind the Scenes at DIRECTV RedZone", Gear Patrol. https://www.gearpatrol.com/outdoors/a153601/redzone-football-distilled/ (opened)
3. <a id="b3"></a>"A (Heavily Caffeinated) NFL Sunday in the Red Zone", Sports Illustrated, Nov 30 2016. https://www.si.com/nfl/2016/11/30/nfl-directv-red-zone-channel-andrew-siciliano-embed (opened)
4. <a id="b4"></a>"NFL RedZone: Why watch one game when you can watch eight?", KPCC/LAist, 2013. https://laist.com/news/kpcc-archive/nfl-redzone-why-watch-one-game-when-you-can-watch (opened)
5. <a id="b5"></a>"7 Chaotic Hours Behind The Scenes At NFL RedZone", NPR, Dec 14 2014, via WFSU mirror. https://news.wfsu.org/2014-12-15/7-chaotic-hours-behind-the-scenes-at-nfl-redzone (opened; npr.org timed out)
6. <a id="b6"></a>Scott Hanson Q&A, Front Office Sports. https://frontofficesports.com/scott-hanson-qa-nfl-network-nfl-redzone (opened)
7. <a id="b7"></a>"NFL RedZone", Wikipedia. https://en.wikipedia.org/wiki/NFL_RedZone (opened; first 100,000 of 136,000 characters)
8. <a id="b8"></a>"NFL RedZone from NFL Network", DirecTV Insider. https://www.directv.com/insider/nfl-redzone/ (opened)
9. <a id="b9"></a>Terry Lyons, "Sunday with the NFL Red Zone", Substack, Nov 30 2020 (viewer diary). https://terrylyons.substack.com/p/sunday-with-the-nfl-red-zone (opened)
10. <a id="b10"></a>Jared Greenberg interview on NBA CrunchTime, Uproxx. https://uproxx.com/dimemag/nba-crunchtime-jared-greenberg-interview-app (opened)
11. <a id="b11"></a>NBA CrunchTime whip-around, Awful Announcing. https://awfulannouncing.com/nba/nba-crunchtime-whiparound-nfl-red-zone-style-nba-app.html (opened)
12. <a id="b12"></a>March Madness Live Fast Break, Awful Announcing. https://awfulannouncing.com/ncaa/march-madness-live-fast-break-whiparound.html (opened)
13. <a id="b13"></a>Frozen Frenzy fourth season, Sports Video Group, Oct 13 2026. https://www.sportsvideo.org/2026/10/13/all-32-teams-one-night-espns-nhl-frozen-frenzy-returns-for-fourth-season/ (opened)
14. <a id="b14"></a>SVG Rewind: Frozen Frenzy, Sports Video Group, Nov 4 2024. https://www.sportsvideo.org/2024/11/04/svg-rewind-espns-nhl-frozen-frenzy-delivers-a-whip-around-show-at-a-breakneck-speed/ (opened; no cut rules on the page)
15. <a id="b15"></a>"NBC Sports Goal Rush", Engadget, Aug 28 2016. https://www.engadget.com/2016-08-28-nbc-sports-goal-rush-premier-league-soccer.html (opened)
16. <a id="b16"></a>ESPN NBA whip-around rights, Sports Media Watch. https://www.sportsmediawatch.com/?p=1332576 (opened)
17. <a id="b17"></a>"ESPN Goal Line brings Red Zone Channel fandom to college fans", Awful Announcing, 2010. https://awfulannouncing.com/2010-articles/espn-goal-line-brings-red-zone-channel-fandom-to-college-fans.html (opened)
18. "What Is MLB Big Inning?", TV Answer Man, May 5 2025. https://tvanswerman.com/2025/05/05/what-is-mlb-big-inning-when-can-you-watch-it/ (snippet-only; redirected)
19. "Big Inning", MLB Network. https://www.mlb.com/network/shows/big-inning (snippet-only; HTTP 406)
20. ESPN Goal Line description, Comcast Washington. https://washington.comcast.com/?p=2087 (snippet-only)
