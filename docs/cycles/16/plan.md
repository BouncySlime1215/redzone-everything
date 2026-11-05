# Cycle 16 plan (Start Planner), Thu Nov 5, ~07:00 to 08:00 UTC: NBA first, then NFL

**From:**
- integration/next, at:
  - grader v16;
  - the crew landings: #248, #250, #251, #253, #255, #256, #259, #263;
  - #254, #262, #266;
  - the test writers' #249 / #268.
- The integrator's [cycles 09-15 review](../14/review.md).
- Nick, 2:00 AM ET.

## Sync is the #1 priority, above everything except Rule #1 (Nick, 2:10 AM)
The goal: join any game on Prime or YouTube TV and never be behind, every time, whatever the jumping around, TV
latency or Wi-Fi.
- **One program.** Lane ② owns it, lane ③ grades it, and lane ④ integrates it. The rest help:
  - crews A and F measure the per-league feed lag;
  - C works on "fastest feed wins".
- **Rows (pre-registered, sync section of experiments.md):**

  | Part | Row | What |
  |---|---|---|
  | A | EXP-800 | Know where each stick is: provider latency plus distance behind the live edge, from media_session every second, logged to data/sync_truth |
  | B | EXP-801 | Always join at the live edge, verified, with the hidden stick preloaded at the edge |
  | C | EXP-802 | The fastest feed wins, per league |
  | D | EXP-803 | The belief tracker switches only when its band says dead ball, then restart |
  | E | EXP-804 | Ground truth. The camera and mic options are **held for Nick's OK** |

- **Gates:**
  - per-stick delay error p95 ≤ 2 s;
  - 0 entries mid-commercial;
  - switches ≤ 3 s, including Prime.
- **First deliverable today:** the media_session live-edge reader, per-app offset logging, and "force live edge" on
  every tune, shadow-logged tonight.
  - Once Nick OKs it, it ships in the afternoon deploy window, before TNF at 8:15 PM ET.
  - Tonight it only logs. There are no Mac-side experiments while Nick watches.
- **The RC7 sync card is still Nick's call.** Lane ④ stages the work and doesn't ship it until he decides.

## Nick's 2:00 AM priority (binding for every lane, crew and planner)
"I really only care about NBA and NFL, mostly NBA because that's what's on most right now."
- **Every queue is re-ranked by NBA impact first, then NFL.**
- **NHL, MLB and CBB work continues only where it is shared code or shared data.** Shared data includes
  world-model pretraining tokens from any sport.

**The gaps to close, from the scorecard on next:**

| League | Line | Now | Note |
|---|---|---:|---|
| NBA | Entries landing mid-commercial | 21% | 33 of ~160 |
| NBA | Dead air | 19.6% | |
| NBA | Crunch live | 90.1% | |
| NBA | Big plays seen live | 78.9% | Only 71 graded plays over 9 nights. **Grow the graded set to ≥ 60 decisions per gate** |
| NFL | Big plays live | 40.3% | |
| NFL | Catch-up within 3 min | 64.9% | |
| NFL | Entries landed in a break | 196 of 196 | The never-join-at-a-break rule fails hardest here |
| NFL | Crunch live | 66.9% | |

**Power check (test writers, EXP-726, NBA dev, 9 nights, real lag).** No NBA rate line can PASS or FAIL on its CI
with 9 nights.

| NBA line | Now [CI] | Nights needed for ±5 pt |
|---|---|---:|
| Big plays seen live | 78.9% [66, 100] | ~100 |
| Crunch live | 90.1% [78, 100] | ~44 |
| Mid-commercial | **33 of 64 entries = 51.6%** (the 21% used the wrong denominator) | ~23 |
| Dead air | — | ~16 |

- **EXP-735** adds the 2025-26 NBA season (NBA cdn, green) as dev nights before the locked start, until every NBA gate
  has at least 60 decisions.
- **EXP-732** checks whether NFL's 196/196 "in a break" counts the normal gap between plays as a break.

## RC7 (integrator's read: FAIL / PENDING / PENDING)
| Requirement | Owner | Status |
|---|---|---|
| 1. Switches ≤ 3 s | ③ | FAIL now. The real-TV press run, including Prime, is Thursday afternoon with Nick's OK |
| 2. Sync p95 ≤ 2 s | ② | PENDING. It needs RC7's telemetry. Nick's card is open: my recommendation is to grade it on TNF night |
| 3. Never join at a break | ② | PENDING. NBA 21%, NFL 196/196. This is the top behavior gap in both priority leagues |

## Next ranked work (NBA → NFL)
| unit | 1st | 2nd | 3rd |
|---|---|---|---|
| ② | **The sync program (EXP-800..803), first deliverable today** (the live-edge reader, per-app offsets, force live edge, shadow-logged tonight). Then **EXP-205, NBA first:** never land mid-commercial, using crew F's NBA TV-timeout tables (two mandatory timeouts per quarter, under 6:59 and under 2:59) | EXP-205 on NFL (196/196 in a break), then NFL catch-up within 3 min | NBA dead air and crunch live: the EXP-209 arms (react / hybrid / chase) on NBA nights first |
| ③ | **Grade the sync program:** EXP-800's truth rows (media_session), and the real-TV press run this afternoon, YTTV and Prime (requirement 1), daytime only | The EXP-084 delay fit on NBA and NFL streams, handed to ② | EXP-301 grader on NBA nights |
| ① body | Teacher EXP-086 and EXP-103: grades.json and CU spent, with NBA and NFL heads reported first | Saturday's B-sized world model: NBA and NFL held-out losses as the headline numbers | An NBA possession-start / break head from SportVU + nba_stats events |
| ④ | The RC7 cut, staged on Nick's sync answer | EXP-017 v3 locked, **with NBA nights first** so the NBA gates reach ≥ 60 decisions | NFL locked nights next |
| ⑤ | EXP-040 is done: `phone.worth` is on by default, checked end to end, and its decisions line has landed. The worth line reads PENDING until Sunday | EXP-043 follow-ups with NBA players first, once lane ① lands its weight | — |
| Int | **Grow the NBA graded set to ≥ 60 decisions per gate** (F1/F3 nightmare and edge nights from NBA history first) | F4 two-harness grading as a required column, NBA first (the RC6.2 NBA buffer lesson) | #258 RC6.3 (NBA) for Nick; review #206 against the freeze; re-run #186 and #187 CI |
| A | NBA: NBA.com liveData with wall clocks 2019-26 (26 M, measured break lengths) and a nightly liveData collector (it grows the NBA graded set), hoopR NBA 2002-26, SportVU, nba_stats events; then NFL: nflverse 1999+, StatsBomb AMF (NFL tracking) | EuroLeague and ACB as shared basketball pretraining | Other sports continue as shared pretraining tokens only |
| B | NBA context first: injuries, refs (NBA L2M, green), rest and back-to-backs, standings; then NFL | Hourly pageviews for NBA and NFL teams | Other leagues only as shared code |
| C | NBA video events and clip tags first, then NFL | YouTube for official NBA and NFL channels (waits on Nick's key) | — |
| D | NBA and NFL lines and moves first: Polymarket (yellow-B) plus the SBRO and nflverse line history (#271) | — | Kalshi is **red** (Developer Agreement §4.1.1 limits the API to members; §3.1 bans storing data). Off unless Nick gets written research permission |
| E | Teacher commentary on NBA plays first, then NFL | Self-play on NBA nights (within the 3x cap) | — |
| F | **Done on #250 (207dc41) and with ②:** the NBA mandatory timeouts (165 s local / 195 s national, long q50 local 187, national ~217) and the NFL after-score, after-change, two-minute-warning and TV-timeout lengths per network. NFL pod counts aren't public, so the measured lengths stand in. F's probe: mid-commercial entries come from entry policy, so EXP-205 is the lever | Network-specific rules for NBA and NFL | Other leagues later |
| G | NBA and NFL attention curves (Twitch viewer curves wait on Nick's token) | — | — |
| H | EXP-1800 on NBA and NFL recaps only | — | — |
| Scout | NBA and NFL sources first (StatsBomb AMF is ready for A) | Shared basketball / football pretraining | Liiga / NCAA probes, low priority |
| Test writers | EXP-721 (cross-source game identity) and EXP-722 (Holm correction) first, as they suggested | Then EXP-700 / 703 / 704 on NBA, then NFL | A row: NBA graded-set growth to ≥ 60 decisions per gate |

## Data ledger (scout cycle 15)
| item | tokens |
|---|---:|
| Real landed (crew A's pbp ledger) | **97.5 M** (StatsBomb 83.5 M from 3,961 games; SportVU 12.3 M; nba_stats events 1.7 M) |
| New information found | ~1.33 B est. (estimates replaced by real counts; HockeyTech red) |
| Ready next | StatsBomb AMF (NFL tracking, ~3 M); ACB Spain (probe, 10 M) |
| Derived state | 5.7 B, counted separately |

Red: Sports-Reference, stats.ncaa.org and HockeyTech. Their terms bar bots.

## Standing rules
- No deploys and no Mac-side experiments while games are live.
- Tag all B data personal-use.
- Usage checkpoints (ET), with crews C, D, F, G and H pausing first:

  | When | Usage at or below |
  |---|---|
  | Fri 8 PM | 58% |
  | Sat 8 PM | 67% |
  | Sun 8 PM | 76% |
  | Mon 8 PM | 85% |
