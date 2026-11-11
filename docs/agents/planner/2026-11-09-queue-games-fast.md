---
id: P-QUEUE
title: "Queue games fast: getting any game onto the hidden stick and ready to cut"
date: 2026-11-09
stream: planner
author: planner 3 (queue games fast)
status: draft
supersedes: []
sources: 18
verified: 10
affects: [redzone/plan.py, redzone/standby.py, redzone/engine.py, redzone/control.py, redzone/channels.py, redzone/discovery/providers.py, redzone/tuners/tuners.py, redzone/tuners/yttv_seek.py, redzone/autonomy.py, redzone/web/server.py, redzone/web/phone_v2.py, scripts/buffered_jump_sim.py, scripts/fault_drill.py]
gate: []
---

# Queue games fast: getting any game onto the hidden stick and ready to cut

## Answer in 10 lines

1. **"Ready" is not "tuned".** A game is ready when it plays on the hidden stick 60-90 s behind live with a known position. Today that takes 57-77 s on YouTube TV and about 100-125 s on Prime. The tune is the small part on YouTube TV.
2. **The YouTube TV buffer is the biggest fixable cost.** Scrub is 50-62 s: a fixed 22 s banner wait, then about 4 screen reads at 7-8 s each. The tune is 3-4 s. Target 30 s total first, 20 s stretch.
3. **Prime is a different class.** Its buffer is a pause, so B seconds behind costs B seconds. Tune is 25 or 45 s. Prime enters the queue early or not at all, until a test shows Prime can seek.
4. **Targets come from throughput, not only reach.** Planner 2's 45 s makes one NFL TD reachable at B=75. A 4-game block needs 30 s or less: the stick model gives 8.8 jumps per 10 min at 45 s, 12.8 at 30 s, 14.2 at 20 s.
5. **Two sticks means one game physically ready.** The queue is 1 parked game, 1 ping-pong partner (the stick we left keeps its buffer) and up to 5 resolved games behind them.
6. **Tonight's list is less ready than it looks.** On Nov 8 at noon, 6 of 11 afternoon NFL games (Washington's included) pointed at a Prime feed flagged "may be blacked out" and were marked ready. Four more had no channel. Pre-game checks have never verified a game in 7 plan files.
7. **Who decides is a fixed ladder, in code:** Nick's tap, the committed next switch, known moments the stick can still reach, slow-app warnings, then the brain's ranking, then `standby.Planner`.
8. **A failed tune advances the queue.** Typed failure, fixed budget per app, next route, then next game. Nothing waits 75 s or more again.
9. **ESPN has never worked here (0 of 19).** Other apps are live-edge only. Fix ESPN's link form behind a flag, one test.
10. **All of this is software, about 54-62 hours, and none of it needs the TV to build.** A third stick is worth about 30 s of prep, but the TV may have only 2 HDMI ports. Not now.

## The analysis

### 1. Where each second goes today

Measured here unless marked. Sources: `switches` table Nov 5-8 (spans), `data/switch_times.json`, `buffer.jsonl`, Nov 4 live lab (`docs/livelab-2026-11-04.md`, untracked in the main checkout), P1-BOTTLENECKS, P1-ROOTCAUSE.

| Stage | YouTube TV, channel code | YouTube TV, local by search | Prime | ESPN app |
|---|---|---|---|---|
| Know game -> app + ref | Instant if code known | Instant, but no code | GTI from Apple feed, 5 min cache | Watch id from ESPN Graph |
| Tune to playing | p50 2.3-4.4, p90/p95 3.1-12.2 | 12-15 by hand; 10 OCR failures Nov 7, 24-31 s each, two at 160-200 s | 25 or 45 (bimodal), lab p50 27.1, slate p50 43.1, p90 49.4 | **0 of 19 ever played** ("couldn't open the link") |
| Get behind live | Scrub 50-62 s when ok; 10 of 17 real attempts failed | Same scrub, not separately measured | Pause-hold in 25 s chunks: B seconds by design; 4 of 10 failed, all on HDMI2; 6 of 6 on HDMI1 after the chunk fix | None |
| Park at the line-up | Pause, about 3 s settle | Same | Same | None |
| Flip | 2.5 p50, 3.2 p90 (lab 1.9) | Same | Same | Same |
| Picture after flip, preloaded | 2.9 p50, 8.2 p90 | Same | 11.6 p50, 34.8 p90 (lab 2.2, unexplained gap) | n/a |
| **Total to buffered and on screen** | **57-77 s** | about 65-80 s (estimate) | **about 100-125 s** at B=75 (estimate from parts) | no path |

Scrub split, adding up to the measured 50-62 s (split estimated from config and code; `res.steps` was not logged until now):

| Part | Seconds | Source |
|---|---|---|
| Wait for the LIVE/title banner to clear | 22 | `timing.behind_live_s.scrub_after_tune_s` |
| Screen reads: focus, after LEFTs, after commit (3-4) | 24-32 | "each label read adds ~8 s" in `_scrub_into`; 6.5 s PNG encode in `adb.py` |
| Overlay waits and key presses | 4-8 | `overlay_wait_s` 1.3 per step |

Warm vs cold, new cut of the `switches` spans (tune seconds, by what that stick last played; n is small):

| App | Same app last | Other app last | First in 30 min |
|---|---|---|---|
| YouTube TV | p50 3.2, p90 5.4 (n=9) | p50 4.2, p90 8.3 (n=3) | p50 4.4, p90 12.2 (n=13) |
| Prime | p50 36.6, p90 63.9 (n=6) | p50 25.7, p90 43.8 (n=6) | p50 30.7, p90 49.4 (n=6) |

Reading:

- A warm YouTube TV saves about 1 s at the median and about 7 s at p90. ADBTuner's author logged the same shape: 3.42 s app running, 11.29 s with the display off, and it leaves apps running on purpose ([b1](#b1)).
- A warm Prime does not help and may hurt. Hypothesis, unmeasured: `PrimeTuner._watch_for` calls `_on_detail_page`, which runs OCR on a player screen, inside its poll loop. `_confirm` also has up to 3 rounds of `screen_text` with OCR fallback. The 25 s vs 45 s modes look like one extra round. The new `prime_tune` step rows (P1-ROOTCAUSE-07) have 0 rows so far, so the first Prime test settles this.

Conflicts in the repo, now resolved or flagged:

- **HDMI-CEC comment is stale.** `control.py` around the flip says the stick we left pauses YouTube TV. The Nov 4 lab turned CEC off on both sticks: 83 of 83 samples stayed playing across 6 flips. That is the basis for free ping-pong. Live check L11 confirms it still holds.
- **MEDIA_REWIND.** The Nov 4 lab says 6 presses moved 81.6 s. `control.py` says it never seeks, proven Nov 6 with 275 failures. I treat the later result as right. Do not rebuild on rewind keys.
- **Pause on YouTube TV.** The Nov 4 lab held 60 and 90 s for 40-60 min with no drift, and the Nov 6 header says "pause does NOT hold". Unresolved. Live check L3 settles it, because a working pause would be a no-OCR fallback.

### 2. Targets per app

Reach ceiling: finer grid of Planner 2's script, dev nights only, `prep` includes tune + buffer + flip (script: `/private/tmp/claude-501/-Users-nick-matta-Library-Application-Support-Claude-scratch-workspaces-9fd867a4-1593-493b-b94f-1f3aebd68493-688aed4e-2f6f-4b8a-9ed9-aa097c310a36-scratch-2026-11-08-ce8f23/9f563e62-788a-4e8b-a325-b193c8b21949/scratchpad/queue/reach_grid.py`).

| % of events reachable, feed lag p50 / p90 | Prep 5 | 15 | 20 | 30 | 45 | 60 |
|---|---|---|---|---|---|---|
| NFL TD, B=60 | 100/100 | 100/100 | 100/100 | 100/100 | 100/0 | 0/0 |
| NFL TD, B=75 | 100/100 | 100/100 | 100/100 | 100/100 | 100/100 | 100/0 |
| NFL TD, B=90 | 100/100 | 100/100 | 100/100 | 100/100 | 100/100 | 100/100 |
| NBA clutch make, shot or dead ball, B=75 | 91/79 | 89/66 | 84/45 | 78/14 | 37/0 | 1/0 |
| NHL goal from faceoff, B=75 | 51/35 | 43/22 | 39/16 | 32/4 | 10/0 | 0/0 |

Throughput: `scripts/buffered_jump_sim.py --maxrate`, NFL slates of 7+ games, **12 dev slates only, 2,302 min** (locked and fresh nights removed by linking dev files into a separate archive). 0 mid-play joins in every row. Mid-play leaves 0-4 per row across all 2,302 minutes, same position-error tail P1-MAXRATE reports.

| Sticks | Games held | Prep s | Seek s | Fail | Jumps per 10 min |
|---|---|---|---|---|---|
| 2 | 4 | 60 | 40 | 74% (today) | 1.8 |
| 2 | 4 | 60 | 15 | 25% | 6.3 |
| 2 | 4 | 45 | 15 | 25% | 7.3 |
| 2 | 4 | 45 | 8 | 10% | 8.8 |
| 2 | 4 | **30** | 8 | 10% | **12.8** |
| 2 | 4 | **20** | 8 | 10% | **14.2** |
| 2 | 4 | 15 | 8 | 10% | 14.5 |
| 2 | 4 | 110 (Prime-like) | 8 | 10% | 3.6 |
| 3 | 4 | 45 | 8 | 10% | 14.6 |
| 3 | 4 | 20 | 8 | 10% | 17.1 |
| 3 | 4 | 110 (Prime-like) | 8 | 10% | 6.1 |
| 4 | 4 | 20 | 8 | 10% | 17.7 |

These are model rows: use them to compare setups, not as predictions. Prime is modeled only as a longer prep.

Targets (p90 unless noted):

| App or path | Today | Target v1 | Target v2 | What gets there | Queue class |
|---|---|---|---|---|---|
| YouTube TV code (includes Sunday Ticket slots) | 57-77 | **30** | 20 | Band reads, fewer reads, shorter banner wait, or Lounge seek | Fast |
| YouTube TV local by search | 65-80 | Same as code, once a code is learned | Same | Learn codes through the existing read-only Lounge path | Fast after codes, else Long lead |
| Prime NFL, NBA, NHL | 100-125 | **Tune 25**, hold B: about 90-105 | 30 if Prime can seek | Remove OCR from confirm; test a playback link; test seek | **Long lead** |
| ESPN app | none | Tune 20, live edge only | Unknown | Link form fix | Live edge only |
| Other apps (`tuners/providers.py`) | Not measured here | Tune 30, live edge only | Unknown | Measure first | Live edge only |
| Flip | 2.5 | 2.5 | 2.5 | Hold | All |

**Long lead** means the queue may start it only with at least `prep_s` of expected lead. Warning covers 31% of NFL TDs at 120 s or more (46% at 60 s). So Prime NFL games hold in the pair as a block or air as rewinds. They are not chased.

### 3. The readiness ladder

Each game in the queue has one level. It replaces the current ready or not-ready flag on the phone and in the decider's options.

| Level | Meaning | Who sets it | Flippable? |
|---|---|---|---|
| R0 Known | On the slate, no route | `plan.build_plan` | No |
| R1 Resolved | App + ref + confidence, not walled or flagged | `plan.py`, `channels.where`, `discovery.choose` | No |
| R2 Verified | Played on a stick tonight, or a same-app check passed | `Tonight._check` | No |
| R3 Tuned | Playing at the live edge on the hidden stick | `control._preload` | Only by the strict live-edge gate |
| R4 Buffered | Playing B seconds behind, position read | `_scrub_into`, `_hold` | Yes |
| R5 Parked | Paused at the line-up | `snap_park` | Yes |

`prep_s(entry, stick)` is the time from the current level to R4, from per-stick `LatencyBook` medians plus the time the hidden stick's current job has left. Planner 2's `Option.needs_prep_s` is exactly this number.

### 4. The nightly list

`plan.py` already builds a per-game plan every 60 s and saves it. Extend it. Do not add a second file.

| When | What | Today | Change |
|---|---|---|---|
| 4 AM | App check per stick | `Autonomy._selftest` | Keep; feeds R2 for "app works" |
| T-24 h to first start | Build routes, every 60 s | Exists | Add level, `prep_s`, route confidence, alternates |
| T-75 min per NFL window | Sunday Ticket slot scan | `_st_scan`, 4 channels per hold, 14 of 16 codes known | Finish all batches before T-20; log how early a channel names its game (L9) |
| T-30 to T-20 before the first window | Sweep every distinct target on the hidden stick, your teams first | `Tonight` tries from T-20, but stops on "paused" or "games are on" | Sweep earlier. Later windows use idle gaps (below) |
| Later windows, games already live | Verify in gaps | Refused always, so later windows expire | Allow when the queue has no R4 need within `prep_s` + 60 s |
| T-10 | First game to R4 on the hidden stick | `warmup.pregame` is off | Turn on after the first night's test |

Evidence the sweep is needed: across 7 plan files, 0 games were ever verified (15 "not checked", 22 pending, 8 n/a). Notes read "paused" or "games are on".

**Routing bug to fix first.** Nov 8 plan, saved 12:10 PM. LAR@WSH (his team), DAL@IND, NYG@PHI, GB@NE, LV@SF and ARI@SEA are `Prime Video, may be blacked out`, status ready, with Sunday Ticket unmatched as fallback. NYJ@KC, DET@MIA, CLE@NO and HOU@LAC have no route. The `st_slots` count in `channels.json` is 0. The scan may have filled this in closer to kickoff, and the plan file stops at 12:10, so I cannot say what happened at 1 PM. But "ready" should never be true for a flagged route, and a Sunday-afternoon NFL game should wait for its slot, not take a Prime guess.

Plan file mix on the same day: 30 games, 15 Prime, 12 YouTube TV, 3 ESPN. So Prime is not an edge case.

### 5. Warm apps and what evicts them

- **Keep apps alive.** Never `force-stop`, never HOME on a hidden stick mid-queue (already the rule).
- **Affinity.** A buffered YouTube TV stick should not open Prime unless the queue's top entry is Prime. Amazon says memory kills are the most common unexpected closure on entry-level devices and publishes no thresholds (R-FIRETV finding 5). A second heavy app may evict the first. There is no eviction measurement here yet.
- **Log it.** Record a warm flag with each tune: app in `dumpsys activity` before the link. R-FIRETV recommended this; it is not built.
- **Channel change vs fresh launch.** With YouTube TV warm, the deep link is the channel change: p50 3.2 s. The `-f 0x14000000` resend runs only on "brought to the front". A same-channel link does not reset a seek (`behind_on`), so it goes through another channel. That costs one more tune, about 4 s. Accept it.
- **No channel-up path exists** on these apps that I could find. Deep links stay the only channel change.

### 6. Deep links per app

| App | Today | Prior art | Plan |
|---|---|---|---|
| YouTube TV | `https://tv.youtube.com/watch/<code>`, package-only | Same form; codes change "sometimes" ([b3](#b3)) | Keep. Weekly re-verify exists |
| YouTube TV locals | Search only; `cbs`/`fox` codes are Sunday Ticket mirrors off Sundays | No published codes found | Use `lounge.py` (read-only) after a search tune to learn the code. Test whether the code holds across days |
| Sunday Ticket | Slot codes by scan | None | Keep |
| Prime | `app.primevideo.com/detail?gti=`, then 2 presses | Community configs use a fixed 8 s delay and 2-3 CENTER presses; one warns blind presses can subscribe you ([b2](#b2)). No playback link documented. Amazon tells catalog apps that a deep link should start playback ([b4](#b4)), which proves nothing about Prime's own app | Test one candidate first: Apple's punchout from `eventlinks.py`, `aiv://aiv/detail?gti=<content>&broadcast=<GTI>`. Keep `is_purchase_screen`. Never press blind |
| ESPN | `espn.com/watch/player/_/id/<id>` fails 19 of 19 | `sportscenter://x-callback-url/showWatchStream?playID=<id>` reported working on ESPN+ and some lanes ([b2](#b2), R-FIRETV finding 6) | Add behind a flag with `com.espn.gtv/...StartupActivity` |
| Lounge (YouTube TV) | Read-only now by design | pyytlounge documents `seek_to(time)` and `play_video(id)`; I found no test against YouTube TV on Fire TV ([b5](#b5)) | See decision 1 |

Amazon has blocked on-device apps from deep linking or from local adb. We connect from the Mac, so reported blocks don't apply as far as I can tell (R-FIRETV finding 4, [b16](#b16)). If adb from the LAN ever closes, this whole design stops.

### 7. The queue and who decides it

**Shape.** Two sticks can hold exactly two games ready: the screen's partner on the hidden stick, and the screen itself.

```
ON SCREEN    A   playing behind live
HIDDEN       B   R4/R5, the next cut
PARTNER      A   when we flip to B, the left stick keeps A buffered: flip back costs 2.5 s
UP NEXT      C, D, E   R1 or R2, no stick. Ordered, with prep_s and a reason
```

So 2 games ping-pong with no prep. A third game costs one prep on the just-left stick. While that runs, the screen must hold at least `prep_s`. That is why prep sets the rate on 3-4 game blocks.

**Authority, top wins (code):**

| # | Source | Rule | Exists as |
|---|---|---|---|
| 1 | Nick's pin or tap | Beats everything below except decision 2 | `engine._peek`, `preload(user=True)` |
| 2 | Committed next switch (`Decision.want`) | Already decided | `standby.NEXT` |
| 3 | Known Tier 1 or 2 inside L, and reachable | Facts inside the look-ahead | New, Planner 2 section 3 |
| 4 | Warning for long-lead games | Red-zone trip, crunch soon, power play, when `prep_s` fits | Part of `standby.MOMENT` |
| 5 | Brain `preload` order | When nothing above applies, Planner 2's P/V row | New, `BrainOutput.preload` |
| 6 | `standby.Planner` | Fallback when the brain is off, late or invalid | Exists |

**Eviction rule (pure function):**

```python
def replace(hidden, want, now):
    if want.owner in (USER, NEXT):                        return True
    if not reach(want, now):                              return False  # it becomes a rewind
    sunk = prep_s_spent(hidden)                           # a ready R4 cost 30-110 s
    return value(want) - value(hidden) > margin + k * sunk

def reach(e, now):
    return e.join_in_s + e.lookahead_s >= e.prep_s + busy_s(now) + 2.0
```

`_scrub_protected` and `rotate_due` keep working inside this; they stop being separate paths once the flag is on. The queue never flips. Every cut still goes through Planner 2's `commit_gate`.

**Disagreements with Planner 2** (boundary otherwise respected: the queue is code and the brain only ranks):

| Planner 2 said | I recommend | Why |
|---|---|---|
| Under about 45 s makes NFL reachable | True for one TD at B=75 and p90 lag. **Use 30 s as the target** | At B=60 the ceiling is 30 s. For 4 games the model gives 8.8 at 45 s vs 12.8 at 30 s |
| A known Tier 1 or 2 inside L always beats the brain's preload | Yes, **if reachable** | Evicting a ready R4 for a moment the stick can't reach loses both |
| Reach table assumes a free stick | Add `busy_s` | The hidden stick is often mid-prep. Its 42% ready rate says so |

### 8. Scrub and hold in the queue

- Buffer right after the tune, while the game is still R3. Keep the live-edge fallback (R3 stays flippable only through the strict gate).
- On the YouTube TV scrub, the banner wait is the first thing to attack. Test whether BACK or a re-open clears it at 3, 6, 10, 15 s (L2).
- Use `screencap_band` (raw pixels, no PNG encode) for the overlay. The scorebug path already has it. Read times with it on the overlay are not measured here. 2 s is my estimate.
- Reads kept: one focus check before LEFTs, one label read after commit. Those two carry Rule #1 (`focus guard`, fail closed). The middle read is what can go, since the label read after commit proves the landing.
- Estimated result: tune 5 + banner 6-22 + keys and 2 reads 9 + flip 2.5 = **23-39 s**. The top of that range misses 30 s, which is why L2 matters.
- The relink retry is wired (P1-ROOTCAUSE-05). The knob-moved-0 cause is still unknown; it needs the new `steps` rows.
- Prime stays on pause chunks. Test whether it can seek at all (already in test queue row 3a).
- Same-game seek matters at least as much as prep for rate (P1-MAXRATE). This report covers only moving between games. Same-game seek belongs to the sync and seek work.

### 9. When a tune fails

Budgets are per attempt, then the queue moves.

| Failure | Detected by | Do | Budget |
|---|---|---|---|
| Stick hung | `STICK_HUNG`, breaker | Don't blame the route; next stick job when closed | Fail fast |
| Link refused | `_cant_open` | Next route now; mark route bad tonight | Instant |
| Walled, purchase or sign-in | `classify_screen`, `is_purchase_screen` | Stop. Never press. Route off for the night; phone line | Instant |
| Blackout | Screen text | Route off for the night; next route | One read |
| Video never started | No playback in budget | One fresh retry on YouTube TV only, then next route | YouTube TV 12 s, search 30, Prime 55, ESPN 20 |
| Scrub failed | `scrub_failed` reason | Stay R3 at live edge, retry after a banner miss in 30 s, otherwise next | 45 s |
| Hold failed | `_hold` end state | Re-tune once at live edge, stay R3 | One tune |
| No route left | Queue | Skip the game; next entry; phone line | Instant |

Budgets are set near today's p90 times with little margin, so they get re-checked after L1. Today's cost: Prime timed out at 75, 150 and 194 s (13 rows), and DEV-17's 28 s screen read after a failure already got a 4 s cap (`DIAGNOSE_TIMEOUT_S`). `fallback_ladder.py` is the natural home; it is flag-off today.

Queue failures are logged with a typed reason, and `PRELOAD_BACKOFF_S` (120 s) stays only for "video never started".

### 10. The phone

New card above Tonight's plan. Top 5 rows, then "+N more".

```
NOW       LAR @ WSH      YouTube TV      on screen
NEXT      DAL @ IND      Sunday Ticket   READY  72 s behind
          why: red-zone trip known, 40 s out
UP NEXT   NYG @ PHI      Sunday Ticket   ready in 28 s    [Pin] [Drop]
          KC @ BUF       Prime           long lead: 95 s  [Pin] [Drop]
          Blocked: GB @ NE  no route     [Fix]
```

- **Pin** moves a game to authority 1. **Drop** removes it for the night. **Go now** already exists (the phone's tap). **Fix** opens the existing override (`ChannelMap.set_override`).
- API: `GET /api/queue`, `POST /api/queue/pin|drop|move|route`. State lives in `userstate.py` and survives a restart.
- Every row shows its level and its reason, so a bad queue is visible as a bug. No brain text is needed.
- Nothing in the card moves the visible stick except Go now, and that goes through today's tap path.

### 11. Prior art

| Who | What | Take |
|---|---|---|
| ADBTuner | Deep link, keep apps running, per-channel alternates. 2-3 s best, 10+ cold ([b1](#b1)) | Same core as ours. Our YouTube TV tune already matches |
| FruitDeepLinks | Sports aggregator, ESPN Graph + Apple, fixed delays and presses ([b2](#b2)) | Our event link feed matches. The ESPN form is the useful piece |
| pyytlounge | Network remote with seek and load ([b5](#b5)) | Could replace scrub; untested on our stack |
| IPTV fast channel change | Spare tuner predicts the next channel ([b17](#b17), snippet); hot-channel preload ([b18](#b18), snippet) | This is exactly the hidden stick. Prediction quality sets its value, as with `preloaded` here: 7 of 15 |

I found no public project that buffers a hidden streaming stick behind live and cuts between games.

### 12. Software vs hardware

| Item | Type | Value | Cost |
|---|---|---|---|
| Everything in section "Ordered steps" | Software | 1.8 to about 12.8-14.2 jumps per 10 min in the model | Hours only |
| Third stick | **Hardware, Nick's money** | Model: 14.6 at prep 45, the same as 2 sticks at prep 15 | About $45-85 for a Fire OS 4K Max ([b10](#b10)), must not be Vega ([b9](#b9)) |
| HDMI switch | **Hardware, Nick's money** | Needed if the TV has only 2 ports: most listings say 2, one says 3 ([b13](#b13), [b14](#b14)). A switch needs its own control path and adds a flip delay | Not priced. Not researched |
| More YouTube TV streams | Subscription | Base limit 3 ([b8](#b8), 2021). A hidden stick counts as a stream. 3 sticks uses all 3, leaving none for a phone or another TV. Sunday Ticket at home was uncapped in 2023 ([b7](#b7)); current terms unverified | Not priced |
| Ethernet for sticks | Hardware, Nick's money | Fewer hangs, unproven (R-FIRETV) | Adapter |

Recommendation: no purchase until L1-L3 are in. The queue work removes most of what a third stick would give.

## BRAIN vs CODE

| Decision | Class | Who | Note |
|---|---|---|---|
| Game -> app + ref, route order | C | `channels`, `discovery` | Never model output |
| Is a route verified, walled, blacked out | C | Outcomes and screen rules | Purchase guard stays |
| Nightly list and sweep timing | C | `plan.py` | |
| Readiness level and `prep_s` | C | Measured medians | Filled into `Option.needs_prep_s` |
| Reach check | C | Facts inside L | |
| Queue order, known moment inside L | C | Authority 3 | Planner 2 |
| Queue order, long-lead warning | C | Authority 4 | Rule table |
| Queue order, nothing known | P/V | `BrainOutput.preload`, code picks | `standby.Planner` as fallback |
| Evict the hidden stick | C | `replace()` | Brain value may feed `value()` only |
| Tune, buffer, park, retry | C | Control and tuners | |
| Failure handling | C | Typed table | Mind may explain, never act |
| Pin, drop, go | Nick | Phone | |
| Which of 4 games to hold this block | P/V | Planner 2 | Queue only reads it |
| Why text on the phone | C first, B later | Reason strings | Words only |

## Major considerations and risks

| # | Risk | Effect | What to do |
|---|---|---|---|
| 1 | Band read of the overlay fails, or the banner can't shorten | YouTube TV stays at 38 s or more | L2 and L3 first. Keep current path as fallback |
| 2 | Fewer reads raises wrong-focus seeks | A seek on the wrong screen (#356). Only touches the hidden stick, but a bad position can break Rule #1 | Keep the focus check and post-commit label. Unknown label fails closed |
| 3 | Prime's real split is unknown | The Prime target may be wrong | L1 before WP9 changes behaviour |
| 4 | Link forms and codes drift | Sudden failures | Flags, weekly re-verify, alternates |
| 5 | Blind press on Prime subscribes the account | Real money | `is_purchase_screen` stays on every press path |
| 6 | Sweep runs while Nick watches | Hidden only, but it uses a stream and could hit a stream limit | Hidden stick only, stops on any switch |
| 7 | Eviction margin wrong | Churn or a stale standby | Log evictions; replay against `ShadowStick` |
| 8 | Unmodeled mix | Sim treats all games alike | WP13 models app mix |
| 9 | SmartThings paywall on Dec 15, 2026 | Flips may stop; for the risks planner | The queue assumes a 2.5 s flip |
| 10 | Vega replacement stick | Uncontrollable (R-FIRETV) | Buy Fire OS only |
| 11 | Mac memory pressure slows OCR and adb | Scrub and confirm times grow; P1-ROOTCAUSE-08 and -09 | Fewer OCR calls here helps. Memory is its own item |
| 12 | Lounge is unofficial | May break or breach terms | Optional path, flag off |

Not verified: all targets; read time of `screencap_band` on the overlay; Prime stage split; eviction behaviour; any Lounge control on Fire TV; HDMI port count; current YouTube TV and Prime stream limits (Amazon's help page returned 503, Google's page did not state a limit). Throughput rows are model output. Measured samples are small: one slate for jumps, n=3-13 per warm/cold cell.

**For the mind planner.** Own `value()` in `replace()` and `BrainOutput.preload`. Grade them only after WP3 lands, per Planner 2's warning. `prep_s` is an input the mind can read, never set.

**For the risks planner.** Take items 2, 5, 9 and 11, and the sweep's interaction with "no disruptive tests while Nick watches".

## Decisions needed from Nick

1. **May we test YouTube's network remote (Lounge) on the hidden stick to seek and change channels?** Recommended yes, one 10-minute test. It could cut about 40 s of scrub to a few seconds. Today's code is read-only on purpose. The alternative is staying on screen-read scrubbing, which should reach about 23-39 s.
2. **Does your Pin beat a known touchdown elsewhere?** Recommended: a Pin beats the brain and rotation, but not a known Tier 1. Only Lock beats everything. The alternative is that Pin always wins, which can miss a TD while you waited on another game.
3. **Third stick?** Recommended not now. Look at the back of the TV sometime and count HDMI ports. Costs about $45-85 per Fire OS stick, maybe an HDMI switch, and it would use all 3 YouTube TV streams.

## Ordered steps

Work packages Q1-Q14 need no TV (fake sticks, recorded frames, replay). Each ships off behind `queue.*` flags, with the CLAUDE.md before/after table.

| # | Package | Files | Test without TV | Hours (estimate) |
|---|---|---|---|---|
| Q1 | Routing fix: flagged route is never ready; NFL Sunday waits for its slot | `plan.py`, `channels.py`, `discovery/providers.py` | Nov 8 plan as a sanitized fixture: 0 flagged-ready | 3 |
| Q2 | Stage timing: warm flag, per-stick medians, scrub split, budget misses | `control.py`, `yttv_seek.py` | Fake stick, assert rows | 3 |
| Q3 | Readiness levels and `prep_s`, pure | new `redzone/gamequeue.py` | Table tests | 3 |
| Q4 | Queue policy: authority ladder, `reach`, `replace` behind `queue.enabled`; engine calls it from `_preload_standby` | `gamequeue.py`, `engine.py`, `standby.py` | Replay with `ShadowStick`; flag off identical | 7-8 |
| Q5 | `needs_prep_s` and brain `preload` glue | `gamequeue.py`, `contracts/brain.py` | Null brain gives the baseline | 2, after Planner 2 step 3 |
| Q6 | Sweep: pre-window and idle-gap verify | `plan.py` | Fake clock and control | 3-4 |
| Q7 | Sunday Ticket scan finishes before T-20, resumable | `autonomy.py` | Fake channels | 2 |
| Q8 | Scrub reads: band capture, drop middle read, banner wait as a parameter | `yttv_seek.py`, `yttv_label.py`, `control.py` | Frames in `data/lab/navigation_frames`, fake stick | 4-5 |
| Q9 | Prime confirm: no OCR once loading or in poll loops, one guarded read per press | `tuners.py` | Fake stick; purchase guard test | 4 |
| Q10 | Typed failures, budgets, route failover, queue advance | `control.py`, `gamequeue.py`, `fallback_ladder.py` | Fault table as parametrized test | 4-5 |
| Q11 | ESPN link form behind a flag | `tuners.py` | Command string test | 2 |
| Q12 | Phone card and API | `web/server.py`, `web/phone_v2.py`, `userstate.py` | API tests; restart keeps pins | 6-7 |
| Q13 | Sim per-app prep and slate app mix, dev nights only | `scripts/buffered_jump_sim.py` | Existing `test_maxrate_sim.py` | 3-4 |
| Q14 | Fault drill: tune, scrub and route failures | `scripts/fault_drill.py` | The drill | 2 |
| | Regression pass, PR tables, decisions lines | | | 6-8 |

About 54-62 hours. Build order: Q1 and Q2 first (cheap, true regardless), then Q3, Q4, Q10, Q8, Q9, Q6, Q7, Q12, Q11, Q5, Q13, Q14.

Live measurements, added to [test-queue.md](../../test-queue.md). Hidden stick only, no disruptive test while Nick watches.

| # | Measure | Why | Time | Bar |
|---|---|---|---|---|
| L1 | Prime `prime_tune` steps, 5 tunes per stick, warm and cold | Finds the 25 vs 45 split | 10 min | Each step timed |
| L2 | Banner clear: BACK or re-open at 3, 6, 10, 15 s | The 22 s | 5 min | Earliest safe time |
| L3 | Band capture time on the overlay; YouTube TV pause holds for 30, 60, 90 s | Read cost; resolves the pause conflict | 10 min | Read under 2.5 s |
| L4 | Scrub end to end after Q8: p50, p90, failure with reason, by HDMI | Target | 15 min | 30 s or less, under 10% fail |
| L5 | Prime link candidates, including Apple's `aiv://` form | Removes page presses | 10 min | Plays with 0 presses, or ruled out |
| L6 | Prime seek in steps (test queue 3a) | Could end long lead | 5 min | Yes or no |
| L7 | ESPN forms per network | 0 of 19 today | 10 min | Plays on at least ESPN+ |
| L8 | Warm and evict: YouTube TV -> Prime -> YouTube TV with warm flag, memory read | Affinity rule | 10 min | Evict rate per stick |
| L9 | Prime page before the event; Sunday Ticket naming lead time | Sweep timing | On a game night | Minutes of lead |
| L10 | TV input list read (no flip) | HDMI count | 1 min | Count |
| L11 | Left stick keeps its offset after a flip, 2 min | Ping-pong basis | 5 min | Drift under 1 s |
| L12 | Lounge: DIAL read-only first; `seek_to` and `play_video` only after decision 1 | Could replace scrub | 10 min | Works or ruled out |

After L1-L4 re-run the grid with measured numbers: `--maxrate --mr-games 4 --mr-prep X --mr-seek Y --mr-fail F`, on a dev-only archive.

Scripts for this report: `/private/tmp/claude-501/-Users-nick-matta-Library-Application-Support-Claude-scratch-workspaces-9fd867a4-1593-493b-b94f-1f3aebd68493-688aed4e-2f6f-4b8a-9ed9-aa097c310a36-scratch-2026-11-08-ce8f23/9f563e62-788a-4e8b-a325-b193c8b21949/scratchpad/queue/` (`stages.py`, `reach_grid.py`, `grid.py`, `dev.py`).

## Bibliography

Opened and read unless marked snippet-only. Repo documents are linked in the text and not counted.

1. <a id="b1"></a>ADBTuner thread, Channels community, 2023. https://community.getchannels.com/t/adbtuner-a-channel-tuning-application-for-networked-google-tv-android-tv-devices/36822 (opened; anecdotal)
2. <a id="b2"></a>FruitDeepLinks thread p10, Channels community, Dec 26-27 2025. https://community.getchannels.com/t/fruitdeeplinks-universal-sports-aggregator-for-channels-dvr/45197?page=10 (opened; anecdotal)
3. <a id="b3"></a>"Stream Link - Amazon Prime", Channels community, Jul 2021. https://community.getchannels.com/t/stream-link-amazon-prime/27974/34 (opened; old, shows only a product-page link)
4. <a id="b4"></a>"Verify deep links from the catalog", Amazon, updated Sep 30 2026. https://developer.amazon.com/docs/catalog/verify-deep-links-from-the-catalog.html (opened)
5. <a id="b5"></a>pyytlounge documentation. https://pyytlounge.readthedocs.io/en/stable/pyytlounge.html (opened; does not mention YouTube TV on Fire TV)
6. <a id="b6"></a>"Watch YouTube TV on supported devices", Google. https://support.google.com/youtubetv/answer/7129767?hl=en (opened; Fire TV models and multiview; no stream limit stated)
7. <a id="b7"></a>"YouTube TV Will Have Unlimited Simultaneous Streams for NFL Sunday Ticket", Droid Life, May 26 2023. https://www.droid-life.com/2023/05/26/youtube-tv-will-have-unlimited-simultaneous-streams-for-nfl-sunday-ticket/ (opened; old)
8. <a id="b8"></a>"How many devices can you watch with YouTube TV", TV Guide, Oct 13 2021. https://www.tvguide.com/news/how-many-devices-can-you-watch-with-youtube-tv (opened; old)
9. <a id="b9"></a>"These are the Fire TVs that don't support sideloading", AFTVnews, Oct 5 2026. https://www.aftvnews.com/these-are-the-fire-tvs-that-dont-support-sideloading-or-downloader-due-to-vega-os-replacing-fire-os/ (opened)
10. <a id="b10"></a>Fire TV Stick 4K Max deal, Android Headlines, Oct 6 2026. https://www.androidheadlines.com/2026/10/amazons-fire-tv-stick-4k-max-is-40-off-and-its-the-one-to-buy-right-now.html (opened; $44.99 sale, $85 regular)
11. "YouTube TV simultaneous streams", A Good Movie to Watch; 4K Plus said to lift the at-home cap. https://agoodmovietowatch.com/how-to/youtube-tv-simultaneous-streams/ (snippet-only)
12. "How many devices can I use to stream Prime Video", Astro help; 3 total, 2 same title. https://www.astro.com.my/support/question/how-many-devices-can-i-use-to-stream-prime-video-at-the-same-time (snippet-only; Amazon's own help page returned 503)
13. <a id="b13"></a>UN55NU6900BXZA specifications, tvsfaq; 2 HDMI per search summary. https://tvsfaq.com/en/specifications/samsung-un55nu6900bxza (snippet-only)
14. <a id="b14"></a>UN55NU6900 specifications, takespec; 3 HDMI per search summary. https://takespec.com/tv/samsung-un55nu6900-un55nu6900fxza (snippet-only)
15. Prime multiview with League Pass, PPC Land. https://ppc.land/prime-video-launches-interactive-betting-and-ai-features-for-nba-coverage/ (snippet-only)
16. <a id="b16"></a>"Amazon Fire TV Blocking Deep Links from Third Party Apps", The Streamable. https://thestreamable.com/cl/news/amazon-fire-tv-blocking-deep-links-from-third-party-apps-making-content-discovery-more-difficult (snippet-only)
17. <a id="b17"></a>"Methods and systems for rapid channel change within a digital system", US application 20040181813. https://patents.justia.com/patent/20040181813 (snippet-only)
18. <a id="b18"></a>"Fast IPTV channel switching using hot-view and personalized channel preload", EURASIP JWCN, 2012. https://jwcn-eurasipjournals.springeropen.com/articles/10.1186/1687-1499-2012-354 (snippet-only)
