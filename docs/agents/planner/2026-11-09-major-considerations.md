---
id: P-MAJOR
title: "Major considerations: what could sink RedZone for every league, every night"
date: 2026-11-09
stream: planner
author: planner 5 (major considerations)
status: draft
supersedes: []
sources: 27
verified: 18
affects: [redzone/channels.py, redzone/plan.py, redzone/discovery/providers.py, redzone/governor.py, redzone/autonomy.py, redzone/web/phone_v2.py, redzone/web/static/rz-now.js, redzone/web/static/rz-games.js, redzone/reel.py, scripts/fetch_eval_data.py, docs/data-sources.md, docs/test-queue.md]
gate: []
---

# Major considerations: what could sink RedZone for every league, every night

## Answer in 10 lines

1. **"Every league, every night" does not hold today.** Of 12,870 games in the past year, a route that has actually worked exists for 33%. Only 21% are on a fast, seekable route. Without college basketball the figure is 40%.
2. **The biggest gap is one route.** 5,031 games (39%) route to ESPN+ events, and the ESPN app has played 0 of 19 here. That covers 79% of NHL and 57% of CBB.
3. **The best fix is new and free.** ESPN Unlimited events have been inside YouTube TV since Sep 1 2026, with DVR, including out-of-market NHL. If they tune, reach goes from 33% to 72%, and NHL from 15% to 88%. None of the earlier planners found this.
4. **Nick's own teams are worse off:** 39% reachable. The Nationals are 151 of 162 with no route. Wizards are 82 of 83 on slow Prime. Capitals are 63 of 83 on the broken ESPN route.
5. **Summer is nearly empty.** June to August, 72 of 92 nights have at most one reachable game at a time, because MLB is 86% unrouted. Only 3 nights all year had no games at all.
6. **NBA is a Prime product.** 79% of games route to Prime, which can't seek and takes 100-125 s to be ready. Standard League Pass allows one stream, and the design needs two.
7. **The app has never run a full NFL Sunday.** Longest Sunday stretch is 1.5 h. On Nov 7 it saw 24 of 371 key moments, and 119 of 155 adb auto-fixes failed. About 9 regular-season Sundays are left.
8. **Rewinds only work where the stick can seek.** The 2-3 minute must-show promise fails on Prime and ESPN routes, where replays are ESPN clips 4.5-14 minutes late.
9. **Prep time and the interface are settled below:** 45 s p90 is the gate and 30 s the target. Keep Planner 2's `BrainOutput`, built by a small model call plus code. No LLM during games.
10. **Avoidable exposure:** eval data with ESPN game summaries sits in public GitHub releases, against the repo's own rule. The phone shows scores 40-66 s before the TV. Swap reached 11.0 GB with the app running.

## The analysis

### 1. Does "every league, every night" hold up? (measured here)

Method. I ran one year of ESPN's public schedule (Nov 8 2025 to Nov 7 2026, every in-season date per league, all FBS and D-I) through the repo's own `ChannelMap.where` and `parse_scoreboard`, with today's `data/channels.json`. A "night" runs 5 AM to 5 AM ET. Peak live uses Planner 1's average game lengths. Read only. Scripts (not in the repo): `/private/tmp/claude-501/-Users-nick-matta-Library-Application-Support-Claude-scratch-workspaces-9fd867a4-1593-493b-b94f-1f3aebd68493-688aed4e-2f6f-4b8a-9ed9-aa097c310a36-scratch-2026-11-08-ce8f23/9f563e62-788a-4e8b-a325-b193c8b21949/scratchpad/major/` (`fetch.py`, `cover.py`, `night.py`).

Route classes come from what has actually happened on these sticks:

| Class | Routes | Evidence here |
|---|---|---|
| Fast | YouTube TV channel code, Sunday Ticket slot | Tune p50 2.3-4.4 s (Planner 3). Can seek |
| Works, slow | Prime (including League Pass and Monumental); locals by search | Prime 25-45 s tune, no seek. Search 12-15 s by hand, 10 OCR failures on Nov 7 |
| Broken, fixable | ESPN app events; YouTube TV channels with no code (The CW, others) | ESPN 0 of 19 played |
| No route | Peacock-only, Netflix, Apple, FloSports, B1G+, MLB.TV-only and regional | `where` returns none |

**Where every game routes (12,870 games, Nov 8 2025 to Nov 7 2026):**

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

So Planner 1's whip and block modes (4+ games) are available on 213 nights today, but with fast routes on only 149. ESPN events bring fast routes to 201.

Limits of this audit:

- It assumes every "Prime" and "Sunday Ticket" game plays. Neither is proven per game. Pre-game verification has passed 0 games in 7 plan files (Planner 3).
- `where` can't see which CBS or FOX games air locally in Raleigh. Those are not on Sunday Ticket for him ([b6](#b6)), so a few games each Sunday move from "Fast" to the flaky local path.
- The live planner also uses `discovery.choose`, which adds Apple and ESPN links. The Nov 8 plan had 15 of 30 games on Prime, including 6 NFL games that `where` would not send there (Planner 3's routing bug).
- Whether the WSH Monumental feed plays in Raleigh is marked "likely" in code and is not verified here.

### 2. What Nick's subscriptions actually reach, per league

| League | Main route | What is outside it | Fix |
|---|---|---|---|
| NFL | Sunday Ticket, locals, NBC, ESPN | Thursday Night on Prime (long lead). Netflix and YouTube specials. Locally aired Sunday games are not on Sunday Ticket ([b6](#b6)) | Q1 routing fix. Add a local-game source. Known channel codes for locals |
| CFB | ESPN family, ACCN, Fox, CBS | ESPN+ only games; The CW (no code); FloSports, B1G+ | ESPN events through YouTube TV (below). Paste The CW's link |
| NBA | League Pass via Prime | Peacock exclusives. Charlotte games are blacked out in Raleigh. National games are off League Pass ([b9](#b9)) | Treat Prime as the NBA's normal case, not an edge case |
| CBB | ESPN family, CBSSN, FS1, ACCN | ESPN+ and ACCNX, Peacock, FloSports, B1G+ | ESPN events through YouTube TV. Relevance filter |
| NHL | ESPN+ out-of-market ([b26](#b26)) | Hurricanes games are in-market only, on a $99.99 add-on through Prime ([b10](#b10)). NHL Network isn't in his plan | ESPN events through YouTube TV |
| MLB | National windows only | About 9 in 10 games. MLB.TV is sold through ESPN and is not in YouTube TV ([b8](#b8), [b22](#b22)). Apple Fridays, Peacock, Netflix | Nick's money decision by March |

**The best new fact in this report.** Since Sep 1 2026, ESPN Unlimited events are inside the YouTube TV app, with no account linking, three live-guide rows, unlimited DVR and multiview. YouTube's post names out-of-market NHL and college football ([b3](#b3), [b4](#b4)). None of the first four planners used this. Planner 3 proposes fixing the ESPN app's link form. If these events have watch codes or can be scanned like Sunday Ticket, they become Fast and seekable, which the ESPN app would not be. The code already has a `youtubetv:ev:` route. ACC Network Extra is not named. Whether his plan shows these rows, and whether each event has a stable code, is unknown: one 10-minute hidden-stick look settles it.

**A concurrency risk nobody listed.** Standard League Pass allows one device at a time; Premium allows three ([b9](#b9)). Most NBA games route to League Pass, and two sticks holding two League Pass games is the design. If Nick has the standard tier, the second stream may refuse. That page describes nba.com subscriptions and doesn't say how the Prime Channels version behaves. I found no error text for this in `service.log`, but Prime player text is mostly unreadable here. Five-minute hidden-stick test. YouTube TV's base limit is reported as 3 streams (snippet-only, [b24](#b24)). Prime allows 3, at most 2 on one title ([b16](#b16)), which covers two sticks.

### 3. Catch-up has a hole on no-DVR routes

Planners 1 and 2 rely on rewinds. NHL goals with no dead-ball entry "air as rewinds or reels". Planner 2's must-show floor has a 2-3 minute deadline. That only works where the stick can seek.

| Route | Can it go back? | Earliest replay of a missed Tier 1 |
|---|---|---|
| YouTube TV, Sunday Ticket, ESPN Unlimited in YouTube TV | Yes (DVR stated for ESPN events, [b3](#b3)), seek cost 50-62 s today | 2-3 min is feasible |
| Prime, League Pass, Monumental | No live rewind (morning report Nov 3, not re-tested; test queue 3a) | Only what a paused stick already holds, otherwise ESPN clips |
| ESPN app | Unmeasured | ESPN clips |
| ESPN highlight clips (`reel.py`) | n/a | NBA 4.5 min, NHL 5.5 min, NFL 14 min after the play |

So on most NBA and NHL nights a missed Tier 1 cannot air within 2-3 minutes. **The must-show deadline has to be per route**: YouTube TV 2-3 min, clip-only routes at the measured clip lag, and the phone must not call it missed before then. Moving NHL to ESPN events in YouTube TV would close most of this for hockey.

### 4. Spoilers

The screen runs about 95-125 s behind real life (34.9 s stream lag plus B of 60-90 s). Everything faster than the screen is a spoiler source.

| Source | Lead over the TV | State today | Do |
|---|---|---|---|
| The RedZone phone app | 40-66 s (Planner 2's L), more versus real time | Now and Games tabs show feed scores with no delay. `orientation()` uses them too. `catchup_view` (spoiler-safe) is off by default | Add a "screen time" clock: phone shows scores as of the TV's position for games in the rotation |
| Planner 3's queue card | Designed to say "red-zone trip known, 40 s out" | Not built | Show "next up", never a known outcome. Setups only if they are already visible on TV |
| Other apps' push alerts, group chats, bets | 95-125 s | Nothing in repo. Nick's phone will beat the TV on every touchdown | Nick's 2-minute job: Sports Focus mode on game nights. MLB's app has a 30 s delay option ([b13](#b13)), too short for this. Apple Sports is reported about 10 s ahead of TV (snippet-only, [b27](#b27)) |
| Score bug on a live cut | Reveals earlier scores in that game | Not handled | Rule: a game's rewind airs before any live look at that game. Otherwise label it a replay |
| Broadcast tickers | Other games' scores, at that feed's delay | Can't mask over HDMI | Accept. Prefer rewinds within about 2 min so the ticker rarely wins |
| Cut pattern itself | "We cut here, so something happens" | Planners 1, 2 and 4 handle this in labels | Keep stops and failed 4th downs. Grade it: share of cuts that end in a score vs base rate |
| Reel clips | ESPN clips open on the live angle with the bug | `reel.py` trims after the bug drops | Keep |

Streams elsewhere lag less: up to 62 s at the Super Bowl per a vendor study ([b14](#b14)). We are nearly twice that, by choice.

### 5. High cut rates and the viewer

- **The published anchor is thin.** NPR's peak of about three switches a minute (Planner 1) equals 30 per 10 minutes. It is a reporter's line. Our modeled ceiling of 13-15 per 10 minutes is about half of that peak, and RedZone's average is unknown.
- **Nick has asked for more than that peak.** Nov 7: "4-5 switches every minute", which is 40-50 per 10 minutes. The audit shows 50+ isn't reachable on 2 sticks, and the plan has since moved to 13-15.
- **Evidence for faster being fine:** arousal and memory both rose with edit rate, from slow up to very fast rates, for edits inside one scene ([b15](#b15)).
- **Evidence for a limit:** fast pacing combined with arousing content lowered recognition and recall (Lang 1999, snippet-only, [b17](#b17)). Every whip-around cut is both unrelated and arousing, which is the unsafe corner. The classic paper on related vs unrelated cuts is one I could only confirm as a citation ([b18](#b18)). None of this tested sports, seven hours, or ADHD.
- **My read:** the stick ceiling will bind before attention does through this season. Don't spend engineering on cut-rate comfort yet. Do measure it: `prefs.switch_frequency` and the existing end-of-night rating, by slate size. Log a one-tap "lost" button only if he wants it, since the self-taught rule keeps it out of training.
- **Cheap ground truth.** The real RedZone channel's cut rate is measurable on one Sunday by logging its picture changes from a stick. Worth doing only on a Sunday when a stick is free. This would replace the NPR line with a number. `redzone` has no watch code in `channels.json`, so paste one if he has the channel.
- **Nothing to fix in audio yet, but nobody has measured it.** Two HDMI inputs and several apps have different loudness, and we have no host covering the cut.

### 6. Reliability during a 7-hour Sunday (measured here)

From `data/redzone.db`, `service.log`, `supervisor.jsonl`, Nov 2-8. Read only.

| Fact | Number |
|---|---|
| Full NFL Sundays ever run | **0.** Longest Sunday stretch 1.5 h (Nov 8, 9:51-11:18 AM) |
| Longest run on any night | 9.1 h (Nov 6-7), then 8.3 h and 7.3 h |
| Key moments seen, Nov 7 | **24 of 371** (night report) |
| Failed live switch rows, Nov 7 | 583 YouTube TV, 502 of them one "replay seek failed" loop |
| adb auto-fixes, Nov 2-8 | 155 attempts, **119 failed** (.150: 69 of 86, .181: 50 of 69) |
| Stale-feed interventions | 34 observed, 32 fixed, 32 rate-limited |
| Delay-doubt interventions | 68 fixes, 36 rate-limited |
| Night archive | Missed on Nov 8: the job gave up because the Mac was short on memory and on battery |

A long weeknight is not a Sunday. Sunday adds 8-14 games at once, the Sunday Ticket scan, and a sweep. The first full Sunday will find failures nobody has seen, so **the next Sunday should be a hidden-stick dress rehearsal with Nick watching normally**. There are about 9 regular-season Sundays left (Nov 15 to Jan 10, inferred from Week 1 on Sep 13, 18 weeks). Each one is a scarce test.

Visible-stick health: an earlier review flagged that only the hidden stick gets re-verified, so a "still watching" prompt or an app update on the visible stick could leave the TV stuck. `_still_on_visible` now exists in `control.py`. I did not confirm that it covers prompts and app crashes during a long hold. Add both to `fault_drill.py`.

### 7. The Mac

| Measure | Value | Source |
|---|---|---|
| Swap, app off (Planner 4) | 6.4 GB | Planner 4 |
| Swap, app off, tonight 10:47 PM | 5.8 of 7.2 GB, 37% free | `sysctl`, `memory_pressure`, measured here |
| **Swap with the app running, Nov 7** | **5.4 to 11.0 GB**, 27 governor drops to eco that night | `service.log`, Planner 4 understated it |
| On battery while the governor acted | 13 log lines across Nov 4 and Nov 7 | `service.log` |
| Battery at 10:47 PM tonight | 4%, charging | `pmset` |
| App memory on a real night | **Never logged** | No RSS or footprint row exists |

Top memory users tonight were Chrome, Claude and ChatGPT. So the swap is mostly the development workload. That makes this cheap to fix: on game nights, the Mac runs the app and little else. The harder conflict is that agents work on this same Mac around the clock.

Planner 4's 4 AM morning meeting and 5 AM report are exposed to the same gate. The Nov 8 5 AM archive was held back for about six hours and then missed. An overnight LLM needs agents to be idle too, or it won't run.

Preflight (code, 2 hours): before a live night, require AC power, swap under a set level, and list what to quit. Log footprint every 5 minutes. A 16 GB Mac is an option but not yet justified, since the workload has not been measured alone.

### 8. Platform and vendor risk

**Streaming terms.** YouTube's general terms bar access by automated means without written permission ([b1](#b1)). Paid Service Terms bar access other than by means Google authorizes and limit YouTube TV to personal residential use, and YouTube may end a subscription or act on the account for violations ([b2](#b2)). Those terms also ban any device that lets users "automatically tune away from" ads, but that sentence is about recorded programs. Our leave-at-break logic works on a live stream played behind the edge, which that sentence doesn't literally cover, and I'm not a lawyer. Disney's terms bar automated access to its products, including scraping ([b5](#b5)), and that is where the ESPN feed lives. Amazon's Prime Video terms page returned 503, so Prime's terms are unverified.

Assessment. I found no report of an account being closed for adb tuning. ADBTuner and similar tools have been public for years. But this is a Google account that may hold other things, and our volume is not human: 13-15 jumps per 10 minutes is about 550-630 flips in 7 hours, though flips are TV inputs rather than tunes. Tunes are the visible signal. Keep the app inside one household, two sticks, no redistribution. Don't add Lounge control at high rate. Check whether his YouTube TV login is his main Google account.

**Publicly posted data.** `docs/data-sources.md` rule 4 says shards go only to private Kaggle datasets. But `eval-data-v1` to `v3` are public GitHub release assets on a public repo, holding hundreds of ESPN game summaries. ESPN's data is marked personal, non-commercial use in that same file. This breaks the repo's own rule, and it is the single most avoidable legal exposure here. Also, the locked set is world-readable. Fix: move to a private Kaggle dataset or a private repo (G-08 already designs one).

**Carriage disputes.** Disney's channels left YouTube TV for about 15 days, Oct 30 to Nov 14 2025 ([b21](#b21)), in the middle of football. Fox and NBCU went to the wire in the same period (snippet-only). Almost every Fast route is one company's contract. Mitigation: nothing to build. Keep `channels.py` routing data-driven, and accept the fallback of fewer games.

**Fire OS and Vega.** His 4K Plus and 4K 2023 are on Fire OS. Vega models are the 2nd-gen HD, 4K Select and 3rd-gen 4K ([b12](#b12)), and future sticks are reported to be Vega (snippet-only, [b20](#b20)). A Feb 2024 update blocked apps on the stick from local adb, and LAN adb from a computer kept working ([b11](#b11)). In Sep 2026 a Settings update hid "install unknown apps" for a time (snippet-only, [b19](#b19)). So adb from the LAN still works, and an OTA can change that without notice. Do: log each stick's OS and app versions nightly and alert on change. Buy a spare only as Fire OS, and only if Nick wants insurance. Fire OS 4K Max stock won't last forever, and a stick dying after that would end the two-stick design.

App updates are the more likely break. YouTube TV activity names already drifted in 2026, and scrub depends on on-screen layout.

**SmartThings.** Nick dropped it Nov 8, ahead of the Dec 15 cut. So the risk has moved: `has_readback` is now false. Local key plus CEC is the only path, and `set_input` can't confirm the input from the TV. The 64-of-64 and 1.89 s flip numbers were SmartThings or CEC measurements. Re-measure flip success on the local path before trusting 2.5 s. A wrong-input flip the system can't see would be a Rule #1 event in disguise. Use the stick-side CEC state where available and the phone's "TV input is unknown" row (seen in live failures).

**Feeds.** ESPN, NBA and NHL endpoints are unofficial and unversioned. Today ESPN rejected the `dates=YYYYMMDD-YYYYMMDD` range form with HTTP 400, while single dates work, so its behaviour does change silently. Add a daily schema and freshness check per league before first game. Rate: keep to the repo's 1 request per second for bulk jobs. Note that on a 30-game night, `live_s: 10` and `hot_s: 3` multiply across games.

### 9. Nights with no good games

Only 3 nights in the year had no games at all (July, around the All-Star break). The real problem is nights with games he can't reach.

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
- **Quality isn't reach.** Reachable counts games, not good games. November CBB was mostly blowouts (Planner 1), so a 73-game night can still be dull. Planner 4's importance model is the right tool for this, later.

### 10. Cost

| Item | Price | Status |
|---|---|---|
| YouTube TV base | about $83/mo (snippet-only, [b25](#b25)) | Has it |
| Sunday Ticket | $378/season returning, $240 new ([b6](#b6)) | Has it |
| League Pass | $109.99/yr, Premium $159.99 (snippet-only, [b23](#b23)) | Has a tier, unknown which |
| ESPN Unlimited | Included in YouTube TV base ([b3](#b3)) | Likely has it |
| MLB.TV | $149.99, or $29.99/mo (2026 prices, [b22](#b22)) | Doesn't have it |
| Hurricanes in-market | $99.99/season ([b10](#b10)) | Optional; not one of his teams |
| Colab Pro+ | about $50/mo | Bought Nov 4. Planner 4 says not needed |
| Third stick, HDMI switch | $45-85 plus an unpriced switch | Planner 3: not now |
| SmartThings | about $5/mo | Dropped |
| Claude time for this whole plan | 221-274 estimated engineering hours across the five reports | Weekly cap of 88% still applies |

Cheapest real gains are free: cancel Colab (saves about $50/mo) and test ESPN events in YouTube TV.

### 11. What the first four reports missed or got wrong

| Report | Item | Correction |
|---|---|---|
| Planner 1 | "Rights and legal: nothing new" | Rights decide which games exist. See section 1 |
| Planner 1 | "No one-game night" from 20 nights | True for Oct 19-Nov 7 only. See section 9 |
| Planner 1 | No boxes on one TV | YouTube TV custom multiview shipped in April 2026 on Fire TV, mixing Sunday Ticket and any channel ([b7](#b7)). Live only, no DVR, so it can't run behind the edge. Useful as a crunch or fallback scene with no delay |
| Planner 1, 2 | Rewinds and 2-3 min must-show floor everywhere | Only on seekable routes. See section 3 |
| Planner 3 | Fix ESPN's link form | Try ESPN Unlimited inside YouTube TV first. Seekable, no new app |
| Planner 3 | Throughput modeled on YouTube TV-like games | NBA is mostly Prime. Rows at prep 30 don't describe a typical NBA night until WP13 is done |
| Planner 3 | Phone "why" line | Must not reveal known outcomes |
| Planner 3 | Lounge test "recommended yes" | Still yes, one test only. It is unofficial control of a Google account, so seek-only at low rate |
| Planner 4 | 6.4 GB swap | Measured with the app off. With it on, swap reached 11.0 GB |
| Planner 4 | 4 AM and 5 AM jobs | The same gate already caused a missed 5 AM job on Nov 8 |
| All four | Totals | Plans sum to 192-237 hours, before this report, with 9 Sundays left |

### 12. Reconciling the disagreements

**Planner 3 vs Planner 2: prep time, 30 s or 45 s?** They answer different questions. 45 s is what one NFL touchdown needs at B=75. 30 s is what four games need for throughput. **Recommendation: 45 s p90 is the release gate, 30 s p90 is the target, per app, YouTube TV first.**

- Planner 1's rates for 4 held NFL games: 4.6 Tier 1+2 per 10 minutes. The model gives 8.8 at 45 s, so 45 s covers every must-see. 30 s (12.8) is what buys Tier 3, the "hella cuts" part.
- B is a free knob. At B=90 the reach limit relaxes by 15 s at the cost of 15 s more delay, inside Nick's 60-90 band. It helps reach, not throughput.
- The engineering is the same either way (Q8, Q9), so the argument changes nothing about what to build first. It changes when we call it good.
- Prep is not one number. Basketball needs far lower for reach (Planner 3's grid: 15 s for 89/66, 45 s for 37/0). And the NBA's actual route is Prime at 100-125 s, so NBA waits on Prime or stays a held-game product.

**Planner 4 vs Planner 2: the interface.** Planner 2 wants `think(BrainInput) -> BrainOutput` with `value_now`, `scores`, `preload` and `stay_one_more` from the brain. Planner 4 says most of those are facts or formulas, and no LLM runs during games. **Recommendation: keep Planner 2's `BrainOutput` as the contract, and split how it's produced.**

- `estimate(BrainInput) -> tuple[GameValue]` is the only model call: `value_next_3m` and `p_tier1_60s`.
- `compose(BrainInput, values) -> BrainOutput` is plain code that fills `value_now`, `scores`, `preload` and `stay_one_more` using Planner 4's formulas.
- The arbiter, `valid()`, and Planner 2's adversarial tests read only `BrainOutput`. So `AdversarialBrain` still tests the shield at full strength, and a later model can replace `compose` without touching the shield.
- **No live LLM in v1**: Planner 4 wins. Live numbers are computable and the Mac can't spare the room. Planner 2's 30-60 s Mind stays as a schema field with nothing filling it.
- **Preload first**: Planner 4 wins, with one condition. It needs `valid()`, TTL and Planner 3's reach check, not `legal_options`. The commit gate stays first anyway, because it protects Rule #1 regardless of any brain.
- **Taste:** Planner 2 wins (hand-set only). Planner 1's "learns his taste" is out unless Nick reverses the self-taught rule.

## BRAIN vs CODE

| Item | Owner | Note |
|---|---|---|
| Route class, blackout rules, per-route must-show deadline | CODE | Facts about rights and apps |
| Coverage audit | CODE | Monthly script |
| What the phone may show at this moment | CODE | Screen-time clock. No model |
| Rewind before live look | CODE | A spoiler rule |
| Off-night mode | CODE | Fill ladder |
| Mac preflight, version watch, feed health | CODE | Fail loudly, before first game |
| Is tonight worth turning on | BRAIN later, CODE first | Planner 4's importance model can rank. A threshold on reachable games comes first |
| Cut-rate comfort | NICK | A setting. Nothing learned from taps |
| Which game among reachable ones | BRAIN, inside code limits | Planner 2. A brain can't rank a game the sticks can't reach |
| Subscriptions, accounts, money | NICK | Never an agent |

## Major considerations and risks

Ranked by likely damage: chance it happens times how much of the product it takes away.

| # | Risk | Likelihood | Damage | What to do |
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
| 17 | Viewer fatigue at high cut rates | Unknown | "Hella cuts" stops being fun | Measure later; `switch_frequency` setting exists |

Not verified: Prime's terms (503); the League Pass tier he holds; whether ESPN Unlimited rows exist on his plan; flip reliability on the local path; app memory on a real night; which CBS and FOX games air in Raleigh on a given week (506 Sports returned 403); the WSH Monumental feed in Raleigh; heat or stability of a stick streaming for 7 hours; DVR depth on any route.

## Decisions needed from Nick

1. **What does "every league, every night" mean?** Recommended: every game his subscriptions reach through a route that works, with a screen that says what isn't reachable. MLB stays national-only unless he buys MLB.TV before March, and not before an app that carries it has played a game here. The alternative is buying every package, which still leaves Apple, Netflix and Peacock-only games dark.
2. **Game nights: does the Mac do only RedZone?** Recommended yes: plugged in, Chrome and agents closed, from 30 minutes before first game. Otherwise the app competes with 5-11 GB of swap. The alternative is a second machine, which is money and isn't justified until we measure the app alone.
3. **Take the public ESPN data down?** Recommended yes: move eval data to a private Kaggle dataset. Costs about 4 hours and no money. The repo stays public. The alternative is making the whole repo private, which may run into free CI limits.

## Ordered steps

| # | Step | Hours (estimate) | Depends on |
|---|---|---|---|
| 1 | Hidden-stick checks: ESPN Unlimited rows and codes; League Pass on both sticks; local flip success with no SmartThings | 0.5 live | none |
| 2 | Next Sunday dress rehearsal: full window, hidden stick only, app alone on the Mac, footprint logged every 5 min | 7 elapsed, 2 to review | Nick says yes |
| 3 | `plan.py`: route class on every game, "can't reach tonight" list on the phone | 3 | Planner 3 Q1 |
| 4 | ESPN events through YouTube TV, scanned like Sunday Ticket, behind a flag | 4-6 | 1 |
| 5 | Per-route must-show deadline in the floor; rewind before any live look | 2-3 | Planner 2 step 6 |
| 6 | Phone screen-time clock; spoiler-safe by default; queue card shows no outcomes | 4-6 | Planner 3 Q12 |
| 7 | Mac preflight and footprint log | 2 | none |
| 8 | Version watch (Fire OS, app builds) and daily feed schema check | 3 | none |
| 9 | Move eval data private, update `fetch_eval_data.py` and CI | 3-4 | Decision 3 |
| 10 | Visible-stick faults added to `fault_drill.py`: prompt, app crash, update | 2 | none |
| 11 | Coverage audit into `scripts/`, run monthly | 2 | none |
| 12 | Off-night mode: below threshold, say so and offer yesterday's reel | 3 | Planner 1 step 6 |

About 29-37 hours plus elapsed Sundays, all estimates.

**Order across all five plans for the 9 Sundays left:** Planner 3 Q1-Q4 and Q8-Q10 (about 31 h); Planner 2 steps 1-2 (10-14 h); Planner 1 steps 1-2 (11-13 h); steps 1-8 here (about 21-26 h). About 72-84 hours. Everything else, including all model training, waits until those hold on a live Sunday.

## Bibliography

Opened and read unless marked snippet-only. Repo files are linked in the text and not counted.

1. <a id="b1"></a>YouTube Terms of Service, effective Dec 15 2023. https://www.youtube.com/t/terms (opened)
2. <a id="b2"></a>YouTube Paid Service Terms, including YouTube TV additional terms, effective Feb 11 2026. https://www.youtube.com/t/terms_paidservice (opened)
3. <a id="b3"></a>"ESPN Unlimited on YouTube TV", YouTube Blog, Sep 1 2026. https://blog.youtube/news-and-events/espn-unlimited-youtube-tv-live-sports/ (opened)
4. <a id="b4"></a>"YouTube TV Adds ESPN Unlimited", TV Technology, Sep 4 2026. https://www.tvtechnology.com/platform/streaming/youtube-tv-adds-espn-unlimited (opened)
5. <a id="b5"></a>Disney Terms of Use, updated May 24 2024. https://disneytermsofuse.com/english/ (opened)
6. <a id="b6"></a>"YouTube NFL Sunday Ticket 2026 details", 9to5Google, Sep 10 2026. https://9to5google.com/2026/09/10/youtube-nfl-sunday-ticket-2026-details/ (opened)
7. <a id="b7"></a>"YouTube TV custom multiview", 9to5Google, Apr 28 2026. https://9to5google.com/2026/04/28/youtube-tv-custom-multiview/ (opened; Fire TV named only in snippets of other coverage)
8. <a id="b8"></a>"MLB Media-Rights Shakeup Overview", Sports Video Group, Nov 19 2025. https://www.sportsvideo.org/2025/11/19/mlb-media-rights-shakeup-overview-espn-nbcu-and-netflix-inks-three-year-deals (opened)
9. <a id="b9"></a>NBA League Pass 2026-27 FAQ, NBA. https://www.nba.com/nba-league-pass-faqs?linkId=184823472 (opened)
10. <a id="b10"></a>Hurricanes broadcast information, NHL.com. https://www.nhl.com/hurricanes/team/broadcast-info/index (opened)
11. <a id="b11"></a>"Amazon blocks long-running Fire TV capability", AFTVnews, Feb 23 2024. https://aftvnews.com/amazon-blocks-long-running-fire-tv-capability-breaking-popular-apps-with-no-warning-and-giving-developers-the-runaround (opened, first 100,000 characters)
12. <a id="b12"></a>"These are the Fire TVs that don't support sideloading", AFTVnews, Oct 5 2026. https://www.aftvnews.com/these-are-the-fire-tvs-that-dont-support-sideloading-or-downloader-due-to-vega-os-replacing-fire-os/ (opened)
13. <a id="b13"></a>"MLB app will slow itself down", Six Colors, Mar 2024. https://sixcolors.com/post/2024/03/mlb-app-will-slow-itself-down-let-your-media-stream-catch-up/ (opened)
14. <a id="b14"></a>"Stats Perform 2026 Super Bowl latency report", Sports Video Group, Feb 11 2026. https://www.sportsvideo.org/2026/02/11/stats-perform-2026-super-bowl-latency-report/ (opened; vendor study)
15. <a id="b15"></a>Lang, Zhou, Schwartz, Bolls, Potter, "The effects of edits on arousal, attention, and memory for television messages", JOBEM 44(1), 2000. https://scholars.cityu.edu.hk/en/publications/the-effects-of-edits-on-arousal-attention-and-memory-for-televisi/ (opened, abstract page)
16. <a id="b16"></a>"How Many Streams Can I Watch With My Account?", Amazon help, via an Amazon subdomain. https://digprjsurvey.amazon.com/csad/help/node/G6WDXR9CS9A6GV9S (opened; the main help site returned 503)
17. <a id="b17"></a>Lang, Bolls, Potter, Kawahara, "The effects of production pacing and arousing content", JOBEM 43(4), 1999. https://acnpsearch.tweb-dev.unibo.it/singlejournalindex/67463345 (snippet-only)
18. <a id="b18"></a>Lang, Geiger, Strickwerda, Sumner, "The effects of related and unrelated cuts on viewers' memory for television", Communication Research 20(1), 1993. https://journals.sagepub.com/doi/10.1177/009365093020001001 (snippet-only; citation confirmed, findings not read, HTTP 403)
19. <a id="b19"></a>"Fire TV sideloading bug fix", Android Authority, Sep 2026. https://androidauthority.com/fire-tv-sideloading-bug-fix-3711263 (snippet-only; HTTP 403)
20. <a id="b20"></a>"Amazon confirms all future Fire TV Sticks will run Vega OS", AFTVnews tag page. https://www.aftvnews.com/tag/sideload/ (snippet-only)
21. <a id="b21"></a>"YouTube TV and Disney reach deal", 9to5Google, Nov 14 2025. https://9to5google.com/2025/11/14/youtube-tv-disney-reach-deal-abc-espn-channels/ (opened)
22. <a id="b22"></a>"MLB.TV on ESPN: What fans need to know", AP via WTOP, Feb 10 2026. https://wtop.com/mlb/2026/02/mlb-tv-on-espn-what-fans-need-to-know-about-the-new-deal/ (opened)
23. <a id="b23"></a>"NBA League Pass 2026-27 Plans and Prices", American TV. https://www.americantv.com/nba-league-pass-plans-prices.php (snippet-only)
24. <a id="b24"></a>"How many streams do you get with YouTube TV?", What to Watch. https://whattowatch.com/watching-guide/how-many-streams-do-you-get-youtube-tv (snippet-only)
25. <a id="b25"></a>"YouTube TV subscription cost", DealNews. https://www.dealnews.com/features/youtube-tv-subscription-cost/ (snippet-only)
26. <a id="b26"></a>"ESPN announces 100 exclusive NHL games for 2026-27", ESPN Press Room. https://espnpressroom.com/press-release/espn-announces-100-exclusive-national-hockey-league-games-for-2026-27-season/ (snippet-only)
27. <a id="b27"></a>"For Sports Fans Seeking Spoiler-Free Refuge, Phones Become Foes", Sportico via digest. https://www.pulse.bot/consumer-tech/news/for-sports-fans-seeking-spoiler-free-refuge-phones-become-foes-4f27faec-8ef2-4bdb-9242-dd89f46c6d5e (snippet-only; secondary)

## Every Nick decision, all five reports

Fifteen decisions after merging one duplicate (Planner 2's 1 and Planner 4's 1 are the same). Each default is what happens if Nick says nothing.

| # | Decision | Default | From |
|---|---|---|---|
| 1 | What "every league, every night" promises | Every game reachable on a working route; the phone lists what isn't | P5 |
| 2 | Buy MLB.TV before March? | No. MLB stays national-only until he decides | P5 |
| 3 | Game nights: Mac runs only RedZone, on AC | Yes, from 30 min before first game | P5 |
| 4 | Move public ESPN eval data private | Yes, repo stays public | P5 |
| 5 | Moment tier first, league order as the tiebreak | Yes | P1 |
| 6 | Basketball: may leave right after a made or missed shot | Yes, same as the join rule | P1 |
| 7 | All games on break, nothing left to show | Recap or highlights clip on the hidden stick | P1 |
| 8 | Brain on game nights | Small trees live, no LLM during games, big models offline only | P2, P4 |
| 9 | Self-taught rule: nothing learned from his taps | Keep. His taste enters only as settings | P2 |
| 10 | May the live show try a worse-looking option to learn | No. Replay answers it | P2 |
| 11 | Test YouTube's network remote (Lounge) on the hidden stick | Yes, one 10-minute test, seek only, low rate | P3, P5 |
| 12 | Does Pin beat a known touchdown elsewhere | No. Pin beats the brain and rotation. Only Lock beats everything | P3 |
| 13 | Third stick | Not now | P3 |
| 14 | Tier values: touchdown-class 5, setup 2, highlight 0.5 | Yes, as a start | P4 |
| 15 | Train on free Kaggle only; cancel Colab Pro+ (about $50/mo) | Yes. Nick launches the waiting Kaggle run, about 2 minutes | P4 |

Two-minute jobs that need no decision: turn on a Sports Focus mode on his phone on game nights; check whether his YouTube TV login is his main Google account; count the HDMI ports on the back of the TV; check whether his League Pass is Standard or Premium.
