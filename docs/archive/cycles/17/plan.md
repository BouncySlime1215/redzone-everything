# Cycle 17 plan (Start Planner), Thu Nov 5, ~08:00 to 09:00 UTC: sync #1, then NBA, then NFL

**From:**
- integration/next at 3ffa167:
  - grader v17: the CBB lock exclusion, a label-leak check, and the factory re-ranked NBA first;
  - grader v18: syncgrade for EXP-800/801 (#279);
- the open sync PRs;
- the Cycle 16 plan (#274), still open.

**Order:**
1. Rule #1.
2. Sync: Nick, 2:10 AM.
3. NBA, then NFL: Nick, 2:00 AM.

NHL, MLB and CBB go ahead only through shared code or data.

## Overnight is a full work block (overseer, 3:50 AM ET: "lock in on everything, don't stop")
Every unit works its next ranked items: sync first, then NBA, then NFL.

**Running now:**

| Where | Run | Detail |
|---|---|---|
| Colab A100 | EXP-109 speedrun | `nfm_speedrun_a100` @ bd1f807, 8 arms, ~2.5 h |
| Colab A100 | Crew E commentary EXP-1402 | `teacher32b_commentary_a100`, ~3.3 h |
| Kaggle CPU | Factory v2 | |

The Kaggle TPU ladder (`redzone-nfm-tpu-ladder`) is waiting on Nick's one-time Kaggle identity check. Until then,
lane ① posts a Kaggle-GPU (T4x2 / P100) version of the ladder's 18 M and 60 M points for the overseer to launch,
along with the EXP-103 grader fix.

## Waiting on Nick
- **The RC7 sync card.** Ship the live-edge reader, offsets and force-live-edge before TNF, with logging only tonight?
  - My recommendation is yes.
  - Lane ④ is staging and won't ship until he picks.
- **RC6.3 (NBA):** the integrator's card for #258.
- **Tonight's Kaggle feed-lag probe for EXP-802.** It's cloud only, built by crew A at
  `/mnt/project-files/data-a/notebooks/rz_feed_lag_probe.ipynb`, and needs a start before tip-off.
- **Joining the BDB competitions on Kaggle** (8 of them), and the one-time Kaggle identity check for TPU v5e-8.
- **Keys:** CFBD, the Odds API, YouTube, a Twitch app token, CFL.
- **The camera and mic truth options** (EXP-804) stay on hold until he OKs them.

## Sync program status
| part | PR | state |
|---|---|---|
| A + B: per-stick live-edge meter (shadow) + re-link to live | #281: `sync.edge.enabled` (read-only shadow reader) ships **on**, so tonight is logged; the re-tune / re-link flags ship **off** | open: lane ② |
| A2: `rzseek`, reads and seeks a stick's media session over adb | #287 (tool only) | open |
| EXP-800/801 grader (syncgrade) | #279, frozen as v18 | merged |
| Prime absolute delay, as a grader proposal | #285 | open: integrator |
| Prime test grader for TNF tonight | #288 (pre-registered) | open: integrator |
| RC7 checklist adds the sync program; tests can't reach the real sticks | #282 | open: lane ④ |

## NBA / NFL gaps (unchanged since Cycle 16; the NBA set is underpowered)
- **NBA mid-commercial:** 33 of 64 entries (51.6%) over 9 dev nights.
  - EXP-735 adds the 2025-26 NBA dev nights.
  - `MIN_DECISIONS = 60` keeps a gate PENDING until it has enough decisions.
- **NFL:** all 196 of the entries that landed in a break had more than 10 s of break left. The cause is the entry
  policy, which is what EXP-205 targets.
- **Locked NBA big plays:** 16 over 4 nights.

## Next ranked work (only the changes from Cycle 16)
| unit | 1st | 2nd | 3rd |
|---|---|---|---|
| ② | Land #281 (shadow reader `sync.edge.enabled` on, re-tune flags off) and #287 (`rzseek`), ready for the afternoon window | EXP-803's belief-band entry on NBA dev, once EXP-735's nights land (it folds in EXP-205) | NFL EXP-205 |
| ③ | The afternoon real-TV press run on YTTV + Prime, daytime and with Nick's OK. Grade it with v18 syncgrade and #288 primegrade | The truth rows for EXP-800 on TNF tonight (shadow only) | — |
| ④ | #282: the RC7 checklist with the sync program, staged on Nick's card, showing the on/off split | Land #281 / #287 in the RC7 candidate: shadow reader on, re-tune flags off | EXP-017 v3 with NBA nights first |
| Int | EXP-735, the NBA 2025-26 dev nights | Freeze reviews for #284, #285 and #288 before TNF | The RC6.3 verdict |
| test writers | #277 batch 3 merged, then EXP-700 / 703 NBA autopsies | — | — |
| A | NBA wall-clock pbp and nightly liveData (#276), which feed EXP-735 | The EXP-802 lag probe, tonight on Kaggle if Nick starts it | NFL BDB archive (waits on Nick) |
| B | #280, NBA context: stakes, rest and travel, national TV, L2M | — | — |
| C | #275: NBA + NFL clip worker | — | — |
| E | #286: tokenizer v2 mirror for play tables | — | — |
| F | NFL break lengths from nflverse time_of_day, 2011+ (scout) | — | — |
| H | #273: EXP-1804 recap labels, NBA + NFL | — | — |

## Data ledger (scout cycle 16, #276)
| item | tokens |
|---|---:|
| Real landed, crew A (v1) | 190 M (nba_stats_pbp 100.3 M, statsbomb 83.5 M, statsbomb_amf 4.4 M, nba_stats_events 1.7 M) |
| Tracking | 12.3 M |
| Crew B, context-new | 0.6 M |
| **Total real, pulling or done** | **~228 M** |
| New information found | 3.46 B with the NFL BDB archive at frame-vocab size; 1.46 B without it |

## Standing rules
- No deploys and no Mac-side experiments while games are live. TNF is tonight.
- Personal-use tags on all B data.
- Usage checkpoints (ET): Fri 8 PM 58%, Sat 67%, Sun 76%, Mon 85%.
