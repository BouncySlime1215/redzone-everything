# Ranked backlogs: no thread goes idle overnight

Nick, Nov 4 3:45 AM: every lane and both planners hold a large ranked backlog. **When a lane finishes
its cycle task early, it takes the next unblocked item from its own list below** (top first), opens a
PR into integration/next with the usual rules, and notes it on its checklist. The Start Planner
re-ranks these lists at the start of each cycle; the cycle plan's task always comes first.

Same rules as always: gates on unseen (locked) nights, flags OFF until they pass, full-set grading,
every PR shows its North Star delta (dev + locked, live + candidate columns) and a RAM/CPU line.
Sources: [idea-bank.md](idea-bank.md) (#1-#20), [playbooks.md](../../playbooks.md) (entry ids),
[roadmap.md](../../roadmap.md), the sync plan.

## Overseer queue (Nick, Nov 4 3:05 PM ET): next after each lane's current item, in this order
Usual gates: graded on locked unseen nights, flags OFF until they pass, North Star delta (dev + locked,
live + candidate) and a RAM/CPU line in every PR. No signal from Nick in any of it (self-taught rule).

| # | build | lane | what "done" looks like | flag |
|---|---|---|---|---|
| a | **Night health report**, every morning | ④ | auto-written `docs/nights/YYYY-MM-DD.md` from the live-night logs: North Star lines for last night, misses (each with game, clock, reason), sync p95 per stick, press-to-TV p50/p95, faults recovered; PENDING rows when no night ran; no Nick input | `report.nightly` |
| b | **Smarter stick assignment** | ② | the hottest game (by the decider's priority plus the hidden-stick prediction) goes on the 4K Plus (.150, HDMI1); the other stick pre-positions the predicted next game; zero extra flips or tunes vs today, mid_play 0, big live / crunch live up on dev and locked | `decider.stick_assign.v2` |
| c | **Self-calibration** each night from live signals | ② sync half: YTTV seek step (#133) and per-channel delay; ③ timing half: flip time and press-to-TV | each value re-measured automatically from live telemetry (PlaybackState bursts #128, positions, flip logs), written to a dated calibration file the planner reads at the next start; outlier guard (refuse a value > 3 MAD from the last 5 nights); no Nick work | `calib.auto` |
| d | **Pre-game warm-up** | ③ | 10 min before the first tip or kick, tune both sticks and build the pause buffer, so the first switch of the night is pre-tuned; night-lab test; no tune while Nick is watching another app on the TV | `warmup.pregame` |
| e | **Buffer watchdog** | ③ | detect a lost pause buffer (stick resumed, jumped to live, or app restarted) or behind-live drift > 2 s from target, and rebuild it at the next dead ball, never mid-play; new playbook entry + fault kind `buffer_lost`; drift-recovered row in northstar | `buffer.watchdog` |

## ① Noggin training (always on)
1. Reflex Predict v1 NBA (cycle 01).
2. Clairvoyance model: Predict over the buffered-ahead feed horizon (idea #1, GAME-04).
3. Hindsight-perfect oracle: DP over all games per archived night, offline on GH Actions; the
   `oracle_gap` line (idea #2).
4. Scenario-tree solver: the cloud builds the contingency tables from the oracle (idea #19, cloud half).
5. Reflex PICK by imitation of the oracle, forward CV across nights (idea #2).
6. NHL factory windows (api-web wsc wallclock) + Timing v1 NHL + Predict NHL.
7. NFL / CFB factory (nflverse pbp with wallclock) + Timing + Predict.
8. Ensemble + abstain (idea #8, SAFE-04).
9. Live-odds signal from free sources: research, then feature (idea #6, GAME-20).
10. Simulated nights from 20 years of pbp for offline self-improvement (idea #4).
11. ~~Taste model from Nick's taps~~ dropped (self-taught rule, Nov 4 10:55 AM).
12. Mind (Kaggle, Nick launches): V6 full-set rounds, Teacher nightly review of decision records.

## ② Noggin logic / sync (always on)
1. Finished-game exit, crunch-entry rule, #76 locked regressions, sync stack (cycle 01; SAFE-06/07).
2. P0 safety playbooks: SAFE-01, SAFE-02, SAFE-03, SAFE-05 with night-lab faults (`wifi_degrade`).
3. Scenario-tree lookup on the Mac: table format, branch match, invalidation and re-plan (idea #19,
   Mac half), seeded with GAME-01, -02, -03.
4. Clairvoyance pre-position in the candidate (GAME-04), after ① item 2.
5. P1 game playbooks GAME-05..GAME-24, highest value first (crunch overlaps, NHL empty net, OT,
   2-minute warning, review/injury as breaks, blowout idle).
6. Dead air in the candidate (buffer + reels) with its ceiling computed (GAME-12, GAME-13).
7. Sync: SYNC-01..03, break anchors filter-fed after Wednesday's probe data; per-stick delay error line.
8. 5-minute MPC lookahead over the value table (idea #3), once ① item 3 exists.
9. Nightly lag warm-up (idea #14), NET-02 buffering-aware delay.

## ③ Performance + controls
1. #64, LOCK IN, GO NOW, command ack/retry, press_to_tv_p95, Samsung LAN fast path spike (cycle 01;
   BTN-01..03, TV-01).
2. BTN-04..06 (release lock, press during a flip, phone offline).
3. Pre-flip smoothness check (idea #13, SAFE-08) and pre-tune the most-likely-next game (idea #17).
4. Stick playbooks STK-01..03 named and tested; one-stick mode end to end.
5. TV-02..04 (SmartThings down → LAN → CEC; TV off; CEC input steal).
6. Batched tick over all games (idea #11) and tick p99 ≤20 ms for 8 games.
7. Mac playbooks MAC-02..05: Mind pauses when hot / on battery / near the RAM cap (idea #15); slow tick.
8. Lookup tables instead of live inference where they're as good (idea #10).
9. Attention budget: idle on blowouts (idea #12, GAME-11 CPU half).

## ④ App + integration hardening
1. eval-data-v2, B10 leaks, platform stack, test-log auto-upload incl. traces.jsonl (cycle 01).
2. Night-lab fault kinds for every P0/P2 entry that lacks one: `wifi_degrade`, `phone_offline`,
   `mac_sleep`, `feed_frozen`, `feed_disagree`, `rate_limit`, `mac_offline`, `buffering`, `tv_off`,
   `thermal`, `mem_pressure`, `slow_tick`, `wrong_tune`. Many seeds per night (B12 realistic lab).
3. Feed playbooks FEED-01..07 (source failover order, frozen ETag, disagreement, late plays, schema
   change, 429, missing game).
4. Streaming-app playbooks APP-01..06 and STK-04 (still-watching miss on seed 9).
5. MAC-01, MAC-06..08 (sleep, crash restore ≤10 s, corrupt DB/config: port #24's intent, disk full).
6. Cloud pre-game night plan pulled to the Mac (idea #9, SLATE-01) and nightly cloud update push
   (idea #16).
7. Playbook coverage report in CI: every entry in playbooks.md has a test id that runs, and a
   `playbook=<id>` reason string appears in decision records.
8. Robustness slices in northstar.py: sync ±3 s, feed 10 s stale, one stick down.

## ⑤ Features (lowest; never preempts ①-④)
Done or in review: phone v2 (#79), night replay viewer (#88), slate preview (#91), multiview stick-free
rehearsal (#94). Next, all stick-free, small PRs, flag OFF, no change to switching behavior:
1. Phone states for Nick's buttons (UI half of BTN-01..06): LOCK IN badge until released, GO NOW
   "switching… / on TV" confirmation, "not delivered, retrying" and stale-connection banners. Uses
   lane ③'s command API (id/ack/status) only; lane ③ owns the command path.
2. Decision-record export for the Teacher's nightly review: one JSONL per night (record, reason,
   playbook id, objective outcome from pbp; no 👍/👎 or other Nick taps, self-taught rule), schema documented, ready for lane ① / Kaggle. No upload code.
3. Replay viewer overlays: the hindsight-oracle schedule next to what we did (when lane ①'s oracle
   lands; until then, a stub that reads a schedule file) and the missed-big-play markers.
4. North Star trend page: render docs/northstar-history.jsonl (live vs candidate per line) on the phone
   page, read-only.
5. "I missed that" → queue a catch-up reel request (record only; reels stay OFF).

## End Planner / Integrator
1. Cycle reviews: keep / rework / drop against intent; merged scoreboard delta; `review.md`.
2. Merge order per cycle plan; conflict resolution (keep both sides of per-night dicts).
3. northstar.py after every merge train; switch to eval-data-v2 at cycle 02 start.
4. CI: the playbook coverage check and the RAM/CPU line in the PR comment.
5. RC5 gate report on the locked set when the candidate beats live on every line.
6. Keep docs/status.md and docs/integration.md current.

## Start Planner
1. Each cycle: read review.md, re-rank these lists, write `docs/cycles/NN/plan.md`.
2. Grow playbooks.md toward ~150 entries (game situations per league, provider quirks, Nick's buttons).
3. Focused research per cycle (one or two lookups per lane): free odds sources, NFL/CFB wallclock pbp,
   Samsung LAN control, NBA clip sources.
4. Keep the idea bank's slots honest: mark planned / done / dropped with reasons.
