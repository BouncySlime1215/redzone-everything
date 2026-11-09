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
| 4 | 10-minute window, NFL | P1 pace bar | NFL Sunday | 10 min | 0 late joins, 0 commercial s, must-show plays seen on the games shown | waiting |
| 5 | 10-minute window, NBA | basketball join rule | NBA night | 10 min | joins only after a shot or at a dead ball | waiting |
