# TV test queue (coordinator)

What needs testing on the real TV, in order. When Nick is away, the Mac is closed, or Nick says no tests for a
window, nothing here runs; the queue waits. Each test: what, why, how long, pass bar, result link.

Rules: no disruptive test while Nick watches; hidden stick first; 1-minute checks before 10-minute windows;
results go to the locked results store (location set by the wave-1 "locked results" research).

| # | Test | Why | Needs | Time | Pass bar | Status |
|---|---|---|---|---|---|---|
| 1 | App starts on the merged build, both sticks connect, `/api/machines` shows sane states | merged code is new | app on, no game needed | 5 min | every machine in a declared state, no illegal moves | waiting: Nick's "turn it back on" |
| 2 | One buffered jump on YouTube TV, HDMI1 -> HDMI2 and back | S-1 snap-park fix + merge | 1 live game pair on YTTV | 1 min | jump completes, lands on a dead ball | waiting |
| 3 | Same with Prime on the hidden stick | Prime delay sync | 1 Prime + 1 YTTV game | 1 min | jump completes, Prime sigma < 4 s | waiting |
| 3a | Stick speed numbers, per app and HDMI (P1-MAXRATE): hidden seek latency by distance (<= 8 s), seek failure rate (< 10%), parked position error (<= 1.5 s sd), game-change prep (15 s), flip and PLAY-to-picture (2.5 s), 30 s pause holds, can Prime seek in steps | sets what pace is reachable | any game on the hidden stick | 10 min | numbers recorded in the results ledger | waiting |
| 3b | YouTube TV label anchor at the live edge: read the label after a RIGHT probe, then after a LEFT probe, on the hidden stick (WP-03 review) | `LabelProbe.overlay_reader` adds one 15 s step to every anchor, but commit ff562066 says neither probe moves the knob at the live edge, so behind-live may read 15 s high when `label_reader` is on | 1 live YTTV game, hidden stick | 3 min | the two labels agree with the code's offset, or the anchor offset gets fixed with a test | waiting |
| 3c | Live brain size on the real Mac: load a 1 billion, then a 2 billion parameter model (4-bit) while the app runs a live night on the hidden stick; log peak memory, swap, and seconds per brain pass | Nick wants the live brain at 1-2 billion parameters; the only figures so far are community benchmarks | Trained model, 1 live night | 30 min | total brain memory <= 2 GB, swap does not grow, no tune or flip gets slower, or the size steps down | waiting: model |
| 3d | How far back each app can really seek on the hidden stick (YouTube TV, Prime), by game | Must-show catch-up is worth 71.5% of must-show plays at 95 s behind and 88.3% at 600 s (NFL sim) | 1 live game per app | 10 min | deepest reliable seek recorded per app | waiting |
| 4 | 10-minute window, NFL | P1 pace bar | NFL Sunday | 10 min | 0 late joins, 0 commercial s, must-show plays seen on the games shown | waiting |
| 5 | 10-minute window, NBA | basketball join rule | NBA night | 10 min | joins only after a shot or at a dead ball | waiting |
| 3e | **Can a catch-up clip load on time?** On the hidden stick, time from "go" to a parked picture 3 s before a chosen play, 10 tries each: (a) same game, seek back 30 / 60 / 95 / 150 / 180 s; (b) a different game on the same app (re-tune, then seek); (c) a different app. Then flip, play 12 s, flip back | The catch-up route and short clips (ZERO-LOSS-4) assume 15 s to tune and seek and 3.2 s to flip. If a re-tune takes 60 s, a clip can air only for a game already on the hidden stick | 2 live games on YouTube TV, 1 on Prime | 25 min | p90 load time recorded per case; the sim's 15 s is kept, or replaced with the measured number | waiting |
| 3f | ESPN events through YouTube TV: can an ESPN-only game (NHL, CBB) be opened from YouTube TV's ESPN tile, and does it seek? | 39.1% of games are blocked only by this unproven route; NHL would go from 15% to about 88% reachable | 1 ESPN-only live game | 10 min | opens, plays, seeks back 30 s; or logged as closed | waiting |
| 6 | Short-clip chain: 3 missed plays in a row on the hidden stick, each lead-in + play + 1 s, flipped to at dead balls on screen | Nick: "thats the whole point" (Nov 9). Needs 3e's numbers first | 2 live games, `arbiter.catchup_route` on for the test only | 10 min | every clip shows its whole play, none airs 180 s or more late, 0 flips back after the live game restarted | waiting: 3e |
| 7 | `legal.leave_low_leverage` on for one league: watch for ping-pong (leave, come straight back) | The sim took NFL cuts per hour from 5.7 to 13.9 | 3+ live games | 15 min | no A-B-A inside 30 s; 0 high-leverage leaves | waiting |
| 8 | NBA with the flags on: the 1 simulated mid-play entry | Rule #1. One entry in the sim with all flags on (heuristic stand-in brain) | NBA night | 15 min | 0 mid-play joins seen; if one shows, the flags stay off for NBA | waiting |

## Run order for the first TV session (about 140 minutes, hidden stick first)

Nothing here starts until Nick says the TV is free. Stop at the first failed pass bar and fix before going on.

1. Start-up and states: test 1 (5 min).
2. One jump each way on YouTube TV, then Prime: tests 2 and 3 (2 min).
3. Stick speed numbers: 3a (10 min), then label anchor 3b (3 min).
4. How far back each app seeks: 3d (10 min).
5. **Clip load time: 3e (25 min). This answers "can you load on time".**
6. ESPN through YouTube TV: 3f (10 min).
7. Short-clip chain: 6 (10 min), only if 3e passed.
8. Low-leverage leaves: 7 (15 min). NBA flags: 8 (15 min, NBA night).
9. Ten-minute windows: 4 (NFL Sunday) and 5 (NBA night).
10. Live brain size: 3c (30 min), once a trained model file is on the Mac.
