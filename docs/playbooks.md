# Playbooks: "given X, do Y" across the whole platform

Nick, Nov 4 3:45 AM: every situation the app can meet is a **named, tested playbook entry**:
trigger → action → fallback, each with a test in the night lab (`redzone/lab/night`). The Start
Planner owns this list and its order; the lanes build it top-down; the End Planner checks each entry
against its intent. A playbook entry is **done** only when its night-lab test passes on seeded nights
and the PR shows its North Star line and RAM/CPU cost.

**Rules for every entry**
- Rule #1 beats every playbook: no action may cut or enter mid-play. When a playbook can't tell, it
  holds (stays on the current game) and logs why.
- L1 hard rules (your teams, crunch first, league order, never Peacock) beat everything except rule #1.
  A LOCK IN beats everything except rule #1 and safety.
- Each entry is a pure function of the live state plus a small table (no model call on the Mac
  beyond the Reflex), costs <1 ms to check, and writes a reason string (`playbook=<id>`) into the
  decision record so the End Planner and the Teacher's automated review can audit it (never Nick's taps: the Noggin is self-taught).
- Flag OFF until its test passes on seeded nights; one flag per family (`playbooks.<family>.enabled`).
- **Night lab:** extend `redzone/lab/night/faults.py` KINDS (today: stick_drop, stick_wedge,
  still_watching, feed_outage, flip_ignored, api_down, clock_skew) with one fault/scenario per entry.
  Game-situation entries are tested on replayed archive nights by injecting or selecting the situation.

**Status key:** `exists` (behavior and night-lab fault already there, entry = name + test + reason
string), `partial`, `new`. **Priority:** P0 = rule #1 / safety / Nick's buttons, P1 = big-play and
crunch value, P2 = robustness, P3 = polish.

## Format

`id | trigger | action | fallback | test (night lab) | North Star line | lane | status`

## P0: never mid-play, never stuck, buttons always work

| id | trigger | action | fallback | test | line | lane | status |
|---|---|---|---|---|---|---|---|
| SAFE-01 | feed stale > 10 s on the target game | no cut into it; hold current | if current is stale too, stay put, re-poll at 1.5 s | feed_outage on target | mid_play | ② | partial |
| SAFE-02 | sync σ on the target stick > 3 s | no cut; widen the wait to the next confirmed dead ball | hold until σ shrinks or a break anchor lands | sync ±3 s slice | mid_play | ② | new |
| SAFE-03 | Wi-Fi RSSI drop / buffering > 2 events/min on either stick | safe mode: only cut on long breaks (TV timeout, quarter end) | hold current | new `wifi_degrade` fault | mid_play, dead_air | ② | new |
| SAFE-04 | Reflex models disagree (ensemble spread > threshold) | abstain: hold | rules fallback (FLOOR_S timing) | replayed nights with noise | mid_play | ① | new |
| SAFE-05 | clock skew detected (feed vs wall > 5 s) | re-anchor the clock; no cut until re-anchored | hold | clock_skew | mid_play | ② | exists |
| SAFE-06 | finished game on screen | wait for the target's next dead ball, then enter | 180 s cap only if target is in a break | replayed nights (cycle 01 ②) | mid_play | ② | partial |
| SAFE-07 | crunch starts while target is live-play | wait for whistle unless restart countdown < flip time | stay on current crunch | replayed nights | mid_play, crunch_* | ② | partial |
| SAFE-08 | hidden stick not steady PLAYING 1 s before flip | skip the flip, retry at the next dead ball | stay on current | flip_ignored, stick_wedge | flip_playing_3s | ③ | done (tests/test_control.py; flip_playing in the night report) |
| BTN-01 | LOCK IN pressed | hold that game until released; survive restart | if the stick dies, re-tune it on the other stick | scripted press on a replayed night | press_to_tv_p95 | ③ | done (drill night: tests/test_controls_drill.py) |
| BTN-02 | GO NOW pressed | pre-tune hidden stick, flip, confirm on phone ≤3 s | if pre-tune fails, tune the visible stick and confirm | scripted press | press_to_tv_p95 | ③ | done (drill night) |
| BTN-03 | pause / resume / skip / override | id + ack + retry, idempotent | phone shows "not delivered" after 3 tries | dropped + duplicated commands | press_to_tv_p95 | ③ | done (drill night + tests/test_controls.py) |
| BTN-04 | release LOCK | return to the decider's pick at the next dead ball | stay | scripted press | mid_play | ③ | new |
| BTN-05 | press arrives during a flip | queue it, apply after the flip settles | drop only if superseded, and say so | scripted press mid-flip | press_to_tv_p95 | ③ | new |
| BTN-06 | phone loses connection | server keeps the last state; phone resyncs on reconnect | phone shows stale banner | new `phone_offline` fault | – | ③ | new |
| STK-01 | visible stick drops (adb) | reconnect; if > 10 s, flip TV to hidden stick on current game | one-stick mode (STK-05) | stick_drop host=visible | recovery | ③ | done (e2e night two_stick_recovers) |
| STK-02 | hidden stick drops | reconnect; meanwhile no pre-tune, flips become tune-in-place at dead balls | one-stick mode | stick_drop host=hidden | recovery | ③ | done (e2e night two_stick_recovers) |
| STK-03 | stick wedged (adb up, app frozen) | force-stop app, relaunch, re-tune | reboot stick via adb | stick_wedge | recovery | ③ | done (e2e night wedged_stick_reconnected) |
| STK-04 | "Still watching?" prompt | dismiss on the hidden stick at once, visible stick at the next dead ball | relaunch app | still_watching (seed 9 miss) | recovery | ④ | partial (#64) |
| STK-05 | hidden stick PAUSED off screen (HDMI-CEC pauses the stick a flip leaves; live lab Nov 4) or a stale state=3 PLAYING with active=false | media_session must read active + speed>0; verify_standby (every 15 s) drops it and re-tunes at the live edge at once (no preload gap) | at the flip: SAFE-08 skips it | stick_paused (mode stale / pause) | recovery, flip_playing_3s | ③ | done (tests/test_hot_standby.py, tests/test_night_playbook_faults.py) |
| STK-06 | pause buffer LOST (held stick resumed or jumped to live, app restart) or behind-live drift > 2 s from target (Nick, Nov 4 3:05 PM) | rebuild the hold at the next dead ball on the hidden stick (pause method, calibrated hold -1.5 s); never mid-play. An intentional hold pause is not STK-05 | run without buffer for that game (live edge, entries at confirmed dead only) | new `buffer_lost` fault | behind_live_err_p95, drift_recovered | ③ | new (overseer queue e) |
| TV-01 | SmartThings flip ignored | retry once; verify by input readback | LAN WebSocket KEY_HDMI fast path; else stay on current | flip_ignored, tv_input_ignored | switch_p95, recovery | ③ | partial |
| TV-02 | SmartThings API down | LAN WebSocket path | CEC via the stick; else one-stick mode | api_down, st_down (faults:tv) | recovery | ③ | exists (HOME fallback; tested) |
| MAC-01 | Mac about to sleep | caffeinate while games are live; persist state | on wake: restore state, re-verify sticks and TV input | new `mac_sleep` fault | recovery | ④ | partial (caffeinate while live: tests/test_mac_wake.py; wake.py recover; state not persisted) |

## P1: big plays and crunch (the scenario tree's first branches)

These are the hand-written seeds of the scenario policy (idea #19). The cloud-precomputed tree will
generalize them; until then each is a named rule, tested on replayed nights.

| id | trigger | action | fallback | test | line | lane | status |
|---|---|---|---|---|---|---|---|
| GAME-01 | A enters a TV timeout while B is in the red zone (NFL) | B now; back to A at its restart | stay on A if B's flip can't land before the snap | replayed NFL nights | big_live_nfl | ② | new |
| GAME-02 | TD/score in A, C has a power play (NHL) | C next at the first whistle | A replay reel if C's PP ends | replayed mixed nights | big_live_nhl | ② | new |
| GAME-03 | your-team crunch starts | lock in (soft lock) until it ends or a blowout | none: L1 | replayed your-team nights | your_team_crunch | ② | partial (Priority L1) |
| GAME-04 | buffer shows a big play in the next 30-60 s in game X (clairvoyance) | pre-tune X, flip at the dead ball before it airs | reel it within 10 min if missed | replayed nights with the buffer | big_live_* | ①② | new (cycle 02) |
| GAME-05 | two crunches at once, same league | higher stakes (score margin, time, playoff) live, other time-shifted on the second stick | catch-up reel after | replayed overlap nights | crunch_* | ② | built, flag `playbooks.game.crunch_overlap` off |
| GAME-06 | two crunches, different leagues | league order live, other time-shifted | catch-up | mixed nights | league_order | ② | done (league order, tested) |
| GAME-07 | NFL drive reaches the 20 | candidate for next switch (drive-in-20) | stay if current is crunch | replayed NFL | big_live_nfl | ② | exists (#62, off) |
| GAME-08 | NBA final 2:00 within 5 | crunch priority; wait for dead ball | – | replayed NBA | crunch_nba | ② | partial |
| GAME-09 | NHL empty net / 6-on-5 | top priority among non-your-team games | – | replayed NHL | big_live_nhl | ② | built, flag `playbooks.game.empty_net` off (live feed only) |
| GAME-10 | game goes to OT | crunch for its league | – | replayed | crunch_* | ② | done (OT = crunch, tested) |
| GAME-11 | blowout (win prob > 97%) | drop to idle priority; poll slower (attention budget) | resume if margin closes | replayed | crunch_*, CPU | ②③ | new |
| GAME-12 | every game in break at once | best replay reel; else stay | stay on the break with the shortest expected length | replayed | dead_air (ceiling) | ② | partial (reels off) |
| GAME-13 | big play missed live | catch-up reel in the next dead time ≤ 10 min | queue it after crunch | replayed | big_seen_10min | ② | partial (reels off) |
| GAME-14 | NFL 2-minute warning | switch out for the break, back at the restart | – | replayed NFL | dead_air | ② | partial |
| GAME-15 | NFL/CFB 4th down in opponent territory | high priority | – | replayed | big_live_nfl | ② | new |
| GAME-16 | MLB bases loaded / late close | priority within MLB | – | replayed MLB | big_live_mlb (new) | ② | new |
| GAME-17 | CFB/CBB your team (UNC) any time | L1 your-team rule | – | replayed | your_team_* | ② | partial |
| GAME-18 | NHL penalty shot / shootout | top priority | – | replayed | big_live_nhl | ② | new |
| GAME-19 | NBA end-of-quarter heave window (< 5 s) | stay if already there; don't enter | – | replayed | mid_play | ② | new |
| GAME-20 | live-odds swing > threshold (free source) | raise game priority | ignore if the source is stale | replayed with archived odds | big_live_* | ① | new (idea #6) |
| GAME-21 | review / challenge in A | treat as a break: leave for B | back at the restart | replayed | dead_air | ② | built, scenario row (flag `playbooks.game.enabled` off) |
| GAME-22 | injury stoppage | treat as a long break | – | replayed | dead_air | ② | built, scenario row (flag `playbooks.game.enabled` off) |
| GAME-23 | game delayed / weather | drop from the slate until it resumes | – | slate fixture | – | ② | new |
| GAME-24 | new game starts while another is in crunch | don't leave crunch; time-shift the new game's start | – | replayed | crunch_* | ② | partial |

## P2: robustness (feeds, network, streaming apps, Mac)

| id | trigger | action | fallback | test | line | lane | status |
|---|---|---|---|---|---|---|---|
| FEED-01 | ESPN summary 5xx | switch to the next source (NHL wsc, NBA liveData) | FastCast; else SAFE-01 | feed_5xx / api_down | recovery | ④ | done (flag sources.trust_order, off; tests/test_feed_trust_order.py) |
| FEED-02 | feed returns stale ETag for > 30 s | burst poll the challenger | SAFE-01 | new `feed_frozen` fault | mid_play | ④ | partial (#73) |
| FEED-03 | two sources disagree on clock/state | trust the faster measured source, log it | hold | new `feed_disagree` fault | mid_play | ④ | done (flag sources.trust_order, off; merger.disagreements logged) |
| FEED-04 | play arrives out of order / late | re-sort by wallclock; revise only future decisions | – | lag jitter (B12) | mid_play | ④ | new |
| FEED-05 | feed schema change (parse error) | quarantine the source, fall back | file an auto-fix issue | malformed fixture | recovery | ④ | partial (an unreadable summary is a source error, so the source loses stoppage ownership: tests/test_feeds.py; no bench yet; autofix files it) |
| FEED-06 | rate limited (429) | back off with jitter, rotate source | – | new `rate_limit` fault | recovery | ④ | new |
| FEED-07 | game missing from the scoreboard | keep last known, recheck in 30 s | drop from slate | fixture | – | ④ | new |
| NET-01 | Mac offline | keep playing the current game; no cuts | resume decisions on reconnect | new `mac_offline` fault | recovery | ④ | new |
| NET-02 | stick bitrate drop / buffering | that stick's delay estimate widens; prefer the other stick | SAFE-03 | new `buffering` fault | flip_playing_3s | ② | new |
| NET-03 | DNS failure for one source | that source down; others continue | FEED-01 | fixture | recovery | ④ | new |
| APP-01 | YTTV sign-in screen | person-only fault: notify Nick once, use the other app/channel | – | prompt_screen | recovery | ④ | partial |
| APP-02 | YTTV channel not found by search | try the next tune path (guide, deep link) | other provider | fixture | recovery | ④ | partial |
| APP-03 | Prime "are you still there" / ad break overlay | dismiss on the hidden stick | relaunch | prompt_screen | recovery | ④ | partial |
| APP-04 | app update dialog | dismiss/skip on the hidden stick | relaunch | new fault | recovery | ④ | new |
| APP-05 | wrong game tuned (OCR mismatch) | re-tune | other provider | new `wrong_tune` fault | recovery | ④ | partial (screen OCR) |
| APP-06 | blackout / not available in region | pick the next provider or drop the game | – | fixture | – | ④ | new |
| APP-07 | stream starts at live edge but buffer wanted B s behind | rewind/pause to B on the hidden stick | play at live, Timing uses live lag | buffer fixture | big_live_* | ② | partial (#61) |
| MAC-02 | Mac hot (thermal pressure) | pause the Mind; Reflex only | slow poll on blowouts | new `thermal` fault | tick p99 | ③ | new (idea #15) |
| MAC-03 | Mac on battery | pause the Mind; reduce polling | – | fixture | tick p99 | ③ | new |
| MAC-04 | RSS near the 2.5 GB AI cap | unload the Mind, shrink caches | restart the AI worker | new `mem_pressure` fault | RSS | ③ | partial (governor) |
| MAC-05 | tick loop > 200 ms | drop non-critical work this tick, log | restart the worker | new `slow_tick` fault | tick p99 | ③ | new |
| MAC-06 | app process crash | supervisor restarts, restores state ≤ 10 s | safe mode | app_crash | recovery | ④ | exists |
| MAC-07 | corrupt DB or config | start with defaults, keep TV control | – | fixture | recovery | ④ | partial (#24 closed; port) |
| MAC-08 | disk nearly full | rotate logs and recordings | stop recordings | fixture | recovery | ④ | new |
| TV-03 | TV turned off / other input by a person | stop flipping; wait for TV on our input | notify phone | tv_other_input (faults:tv) | recovery | ③ | exists (never takes the TV back; tested) |
| TV-04 | TV CEC wakes on a stick reboot and steals the input | revert input to the visible stick | – | cec_steal (faults:tv) | recovery | ③ | built, OFF (`hardening.tv`) |
| SYNC-01 | drift alarm (measured or predicted > 5 s) | re-sync on the hidden stick at a dead ball | SAFE-02 | drift fixture | delay_err_p95 | ② | partial |
| SYNC-02 | break anchor seen on screen | update the Kalman filter | – | probe replay | delay_err_p95 | ② | partial |
| SYNC-03 | stall on a stick | shift the filter, don't reset | – | stall fixture | delay_err_p95 | ② | partial |

## P3: polish and taste

| id | trigger | action | fallback | test | line | lane | status |
|---|---|---|---|---|---|---|---|
| ~~TASTE-01~~ | Nick overrides a pick | **dropped** (Nick, Nov 4 10:55 AM: no training signal from Nick) | – | – | – | – | dropped |
| ~~TASTE-02~~ | 👎 on a switch | **dropped** (Nick, Nov 4 10:55 AM: no training signal from Nick) | – | – | – | – | dropped |
| ~~TASTE-03~~ | Nick watches a game > 10 min after a GO NOW | **dropped** (Nick, Nov 4 10:55 AM: no training signal from Nick) | – | – | – | – | dropped |
| SLATE-01 | night starts | pull the cloud night plan; warm up lags | local plan | startup fixture | startup time | ④ | new (ideas #9, #14) |
| SLATE-02 | night ends (last game final) | upload logs; stop polling; let the Mac cool | – | fixture | – | ④ | partial |

## Where this goes next
- **~150 entries** are the target by the end of cycle 03; this file holds the first 79. Each cycle plan
  takes the next P0 → P1 → P2 entries per lane.
- **Scenario tree (idea #19):** GAME-* entries are the first branches. The cloud builds the full tree
  (see idea-bank.md #19); the Mac reads the branch that matches the live state.
