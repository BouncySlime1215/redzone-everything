# Roadmap

Ranked by `python scripts/northstar.py` from [roadmap.json](roadmap.json) against [northstar.md](northstar.md) (dev-test nights) on `3704a23` at 2026-11-05 23:58 UTC. Edit roadmap.json, not this file.

**Score** = (gap to target × expected impact) / effort, gap and impact in units of the line's scale (100 points for a rate, 5 per 100 landings for mid-play, 3 s for switch time), ×2 on rule #1 (mid-play); the gap is measured from the conservative end of the CI, so a PENDING line still has one. Expected deltas are estimates until the PR measures them. **Order:** the director's priority (`priority` in roadmap.json, set each morning) first, then everything else by score.

| rank | prio | id | work | moves | gap now | expected Δ | effort (days) | owner | status | score |
|---:|---:|---|---|---|---:|---:|---:|---|---|---:|
| 1 |  | NFM | NOGGIN FOUNDATION MODEL, the Noggin's main track (Nick 4:45 PM Nov 4). Event-token sequences per game plus the cross-game slate from the factory (~1B tokens, real + self-play); self-supervised pretraining (next event, its time, next stoppage); fine-tuned heads for Watch Value, crunch enter/exit, break start/length, big-play-next, game importance; hand features become optional inputs. ~100-300M params on Kaggle T4s within 30 GPU-h/week; distilled to the Reflex (<5 ms), plus a 3B-class Mind/MLX tier on the Mac (<=2 GB). Gates: locked nights, oracle rank agreement, calibration. Design: docs/noggin-foundation-model.md. | % of hindsight-oracle value achieved (mean of NFL, NBA, NHL) | 36.6 | 20 | 10 | lane 1 (Noggin training); Kaggle via the overseer | design doc this cycle (06); factory emits event-token shards next round | 7.3 |
| 2 | 1 | P1 | Time-shift buffer LIVE: hold each stream B s behind live (YTTV/Prime rewind/pause on the hidden stick before the flip, verified by the per-stick delay estimate + Prime clock OCR); B=90 NBA/NHL, 60-75 NFL behind timing.behind_live_s, OFF by default, ready for Wed test. Sim at B=90: NBA seen 67.8->87.4, NFL seen 77.9->97.1, NHL entries mid-play 12->0 | Big plays live or replayed within 3 min | 39.88 | 12 | 1.5 | TIMING | merged OFF (#61); live test Wed | 31.9 |
| 3 | 2 | P2 | A3 restart countdown for NBA (KM -> boosted hazard -> conformal) on top of #56's NHL floor: NBA entries mid-play 9 -> 0 (with buffer), crunch live 91 -> >=95% | Mid-play entries (per 100 landings) | 0.0 | 1.7 | 1.5 | TIMING | queued | 0.0 |
| 4 | 3 | P3a | Merge #58 NHL api-web wsc play-by-play (16.7 s p50 vs ESPN 43 s) once green | Big plays seen live, NHL | 64.61 | 5 | 0.25 | TIMING | review | 129.2 |
| 5 | 3 | TREE-6 | Multi-game conflicts by look-ahead (Nick 3:15 PM rule 6): plan the next 5-10 min across all games with the scenario tree (idea #19) over predicted stoppages, crunch and leverage, and pick flips that keep the best expected value; replaces greedy conflict resolution. Cloud builds the tables from the oracle, the Mac looks up branches. | % of hindsight-oracle value achieved (mean of NFL, NBA, NHL) | 36.6 | 5 | 3 | lane 2 (Mac lookup) + lane 1 (cloud tables) | queued (cycle 06+) | 6.1 |
| 6 | 3 | P3b | FastCast never logged a row on the Mac: find out why and fix (A2), per-source lag logged | Mid-play entries (per 100 landings) | 0.0 | 0.5 | 0.5 | TIMING | queued | 0.0 |
| 7 | 3 | P3c | NBA cdn.nba.com liveData timeActual as a faster dead-ball source | Mid-play entries (per 100 landings) | 0.0 | 0.5 | 0.75 | TIMING | queued | 0.0 |
| 8 | 4 | P4a | NFL big plays LIVE: drive-in-the-20 priority using the buffer's lead (49% with the buffer in sim) | Big plays seen live, NFL | 61.02 | 20 | 1.5 | DECISION | merged OFF (#62) | 81.4 |
| 9 | 4 | P4b | Catch-up reels T1-T5, the RedZone way (flag OFF until its gate passes; after A3 #56) | Big plays live or replayed within 3 min | 39.88 | 6 | 2 | INTEGRATION (reel/jump #18) | merged OFF (replay.redzone_way) | 12.0 |
| 10 | 6 | P6 | Harness: scripts/northstar.py + nightly CI + this roadmap (then: your-team big plays in every scorecard, dead-air ceiling, league-order grade on mixed nights) | Big plays live or replayed within 3 min | 39.88 | 0 | 0.5 | HARNESS | review | 0.0 |
| 11 |  | C14 | Next big play: what it is plus ETA quantiles | Big plays seen live, NFL | 61.02 | 5 | 2 | MOMENT/BREAK | queued | 15.3 |
| 12 |  | C15 | Stakes layer: standings, clinch, rivalry, bet-decisive-next from ESPN probabilities; NFL drive tempo + NHL PP clock features | Big plays seen live, NFL | 61.02 | 3 | 2 | MOMENT/BREAK | queued | 9.2 |
| 13 |  | A8 | Hidden-stick prediction (next target in 180 s), so every switch is a pre-tuned flip | Big plays seen live, NBA | 28.33 | 3 | 1 | TIMING | queued | 8.5 |
| 14 |  | C16 | Reel ranker: pbp value + Nick's taps; NHL/MLB clip sources | Big plays live or replayed within 3 min | 39.88 | 1 | 1.5 | MOMENT/BREAK | queued | 2.7 |
| 15 |  | NOGGIN-PRI | Noggin Priority module (docs/noggin.md § Priority): L1 Nick's hard rules from config only (your teams 100%, crunch first, NFL>CFB>NBA>CBB>NHL>MLB, never Peacock), L2 phone-page preferences, L3 mind read (bounded), L4 reflex live value; deterministic, reason string per switch. Feeds the league-order and right-game North Star lines | League order respected (no lower-league non-crunch over a higher-league crunch) | 100.0 | 0 | 2 | NOGGIN | queued | 0.0 |
| 16 |  | ERR-57 | C17 watchdog 5 -> 12 guarded actions, all 13 kinds (after #57); first: the e2e seed-9 'Still watching?' prompt left up | Faults recovered without Nick (e2e night) | 0.0 | 5 | 1.5 | ERRORS | working (C17 next; #57 merged) | 0.0 |
| 17 |  | INT-RC5 | RC5 gate report on the LOCKED set for integration/next (Nick cuts releases) | – (process) | – | – | 0.5 | INTEGRATION | queued | 0.0 |
| 18 |  | A4 | Rule features: national vs local break lengths, full TV-timeout rules per league, NHL inferred TV timeouts | Mid-play entries (per 100 landings) | 0.0 | 0.4 | 1 | TIMING | queued | 0.0 |
| 19 |  | A5 | Per-(app, channel, tune) stream-delay Kalman filter fed by Prime clock OCR (the buffer's verifier) | Landings in dead time | 0.0 | 1 | 1.5 | TIMING (with SYNC) | queued | 0.0 |
| 20 |  | A6 | Commit a cut only at P_safe >= 99.5% over the ~3.5 s flip + settle window (SmartThings 1.98 s trip cost) | Mid-play entries (per 100 landings) | 0.0 | 0.5 | 1 | TIMING | queued | 0.0 |
| 21 |  | B10 | Leaks left after #51: the 2 locked-set games in the e2e night (NHL 401892626, NBA 401909906) and parse_summary leaders | – (process) | – | – | 0.5 | INTEGRATION | queued | 0.0 |
| 22 |  | B12 | Realistic night lab: lag jitter, late plays, drops, resyncs fitted from real traces; many nights x seeds (more recovery evidence) | Faults recovered without Nick (e2e night) | 0.0 | 0 | 2 | INTEGRATION | queued | 0.0 |
| 23 |  | EVAL2 | eval-data-v2: more nights (NBA/NHL locked sets have 4 nights; PASS needs >= 8) and fresh post-pin nights | Big plays seen live, NBA | 28.33 | 0 | 1 | INTEGRATION | queued | 0.0 |
| 24 |  | C13 | Calibrated safe-to-cut/enter now with a runtime abstain (unsure = unsafe, rules fallback) | Mid-play entries (per 100 landings) | 0.0 | 0.5 | 2 | MOMENT/BREAK | queued | 0.0 |
| 25 |  | C18 | Bugs: breaks.blend 50/50 calibration, two JSON parsers, switch-cost constants disagreeing (Nick's own session; don't touch) | – (process) | – | – | 1 | Nick (separate session) | working | 0.0 |
| 26 |  | B11 | Reasoner gate split: rule conformance, calibration, ON/OFF non-inferiority, abstain | – (process) | – | – | 1 | LOCAL AI | queued | 0.0 |
| 27 |  | D19-22 | Kaggle: student recipe (D19 ready), teacher throughput, KD, historical label factory. The LLM writes words only, so no North Star line moves directly | – (process) | – | – | 3 | LOCAL AI | working | 0.0 |
| 28 |  | E23-25 | Decision records + phone thumbs, shadow mode + SPRT rollback, BRAIN auto-fix on dev-test only | – (process) | – | – | 3 | DECISION (E25 PLATFORM) | queued | 0.0 |
| – | 5 | P5 | Triage the 10 failing RC 2 gate rows: fix, or accept and flag off | – (process) | – | – | 0.5 | INTEGRATION | done | 0.0 |
| – | 7 | P7 | docs/live-test-wed.md: Nick's Wed evening test script (SmartThings flips on real games, buffer on, record real landing offsets), <= 30 min of his time. Gives the first real switch samples | Switch time p95 (SmartThings input change) | 0.0 | 0.8 | 0.25 | TIMING | done | 0.0 |
| – |  | A1 | Honest baseline with the measured per-league feed lag | Mid-play entries (per 100 landings) | 0.0 | 0 | 0.5 | TIMING | done | 0.0 |
| – |  | A7 | Scorecard lines: landing/leave-offset histograms, % landings in dead time | Landings in dead time | 0.0 | 0 | 0.5 | TIMING | done | 0.0 |
| – |  | B9 | Night-level cluster bootstrap; PASS needs >= 8 nights, >= 60 decisions, lower bound at the bar | – (process) | – | – | 0.5 | INTEGRATION | done | 0.0 |

## Planning loop

1. **Nightly** (`.github/workflows/northstar.yml`, ~10:00 UTC): runs `scripts/northstar.py --night-seeds 8` on `integration/next`, commits docs/northstar.md, docs/northstar-history.jsonl and this file back to `integration/next`, and uploads them as an artifact.
2. **Every morning** the director reads [northstar.md](northstar.md) and this page, re-sets `priority` in [roadmap.json](roadmap.json) (the score is the mechanical suggestion; the director's order wins), updates statuses, and hands each thread the day's top items.
3. **Every PR** says which North Star line it moves and its expected delta (the PR template has the section), and the CI comment shows the measured delta on dev-test nights (PR vs base, same data). The locked set never appears in a PR delta.

The planner's own table (docs/plans/2026-11-04-0115-planner.md) used the full `all` split; northstar.md uses dev (`fit`) and locked (`test`) separately, so the numbers differ by slice, not by code.
