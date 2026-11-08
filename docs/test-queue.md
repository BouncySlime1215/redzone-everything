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
| 4 | 10-minute window, NFL | P1 pace bar | NFL Sunday | 10 min | 0 late joins, 0 commercial s, cuts within event count | waiting |
| 5 | 10-minute window, NBA | basketball join rule | NBA night | 10 min | joins only after a shot or at a dead ball | waiting |
