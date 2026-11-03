# Highlight jump (jump to the catch-up reel vs stay): status

Branch claude/highlight-jump, rebased on integration/next d702565. Behind `reel.play` (off by
default: the Fire TV player isn't checked yet) and the kill switches `flags.reel` /
`flags.highlight_jump`. With reels off, every scorecard line equals the base (CI, PR #18).

## What it does
- `redzone/reel.py jump_value(StayView, ReelClips, JumpWindow, JumpPolicy) -> JumpVerdict`, pure.
  Contract form: `reel_jump_value(...) -> dict` (contracts.ReelJump.from_dict), `contract_offer()`,
  `Reeler.ready(now)` / `played(offer)` (ReelProvider).
  - Hard stays: crunch, your team late and close, must-see, key moment >= 1.5x the league's usual rate.
  - Window: the dead ball's cautious remaining length (max of today's floor estimate and the fitted
    p10 of that break kind), minus both switches and a margin. Reel cut to fit at a clip boundary,
    never under 5 clips / 30 s.
  - Value: points per second over the window (switches count 0) vs staying (tail risk x live value)
    vs the best live visit.
- `decider.py`: one `_reel_jump` for every reel path (replaces `_reel_fits`); reel comes back for
  the restart (data runs a stream delay ahead); after the reel, the rest of the break is filled by the
  usual rules or waited out at home; missed big plays the reel doesn't have are replayed first;
  a reel's plays count as caught only once they played; `reel_trace` hook.
- Backtest: `--reels` on eval.py / eval_sports.py; `redzone/lab/reelsim.py` (archive ESPN clips,
  else one per big play) and the reel lines; a finished reel left on screen counts as dead air.
- Offline fit: `python -m redzone.lab.reeljump nfl|nba|nhl` (2-fold by night).

## Measured with reels on (vs integration/next d702565)
| | NFL base -> reels | NBA | NHL |
|---|---|---|---|
| mid-play cuts | 0 -> 0 | 0 -> 0 | 0 -> 0 |
| entries mid-play | 0 -> 0 | 38 -> 35 | **5 -> 6** |
| plays missed on entry | 0 -> 0 | **18 -> 20** | 0 -> 0 |
| dead air % | **8.6 -> 8.7** | 19.4 -> 19.0 | 24.1 -> 23.9 |
| big seen % | 95.4 -> 95.3 | 92.0 -> 92.0 | 99.7 -> 99.5 |
| big live % | 29.7 -> 29.3 | 89.7 -> 89.7 | 41.5 -> 41.0 |
| crunch live % | **71.4 -> 70.0** | 96.3 -> 96.3 | 86.3 -> 86.6 |
| your team crunch | 100 -> 100 | 100 -> 100 | 100 -> 100 |
| reels / in dead time | 42 / 98.2% | 16 / 98.6% | 23 / 98.5% |
| commercial s replaced | 1438 | 1528 | 882 |
| home big plays missed on a reel | 1 | 0 | 0 |

Each league has one small regression on a rule line (bold), so reels stay off. In the NFL the reel
mostly replaces fillers that were already commercial-free (RedZone, replays), so dead air doesn't
drop; the crunch loss is one slate (CAR @ ATL) where an earlier reel changed the replay queue and
the HOU @ TEN / GB @ NYG crunch overlap went the other way.

Fitted margins: NBA 0 and NHL 0 on both folds (no unsafe jump held out); NFL folds disagree (60 / 2 s)
because ~10% of jumps outlast the break whatever the margin (the p10 tail): the early return covers
those. Config uses 8 s everywhere.

## Next
1. NBA plays missed on entry +2 and NHL entries mid-play +1: find the reel returns that land live.
2. NFL: allow the jump only where it replaces a commercial (not RedZone or a replay chain).
3. Nick's crunch overlap: watch one crunch live, catch up the other's crunch (DVR replays now, reel
   clips when posted), then tune in live at a dead ball. Needs the core's plan interface.
