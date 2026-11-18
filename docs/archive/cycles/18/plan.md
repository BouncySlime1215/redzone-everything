# Cycle 18 plan (Start Planner), Thu Nov 5, ~09:00 to 10:00 UTC: sync, then NBA, then NFL

**From:** integration/next at 08ca520. This cycle merged:

| Merged | What |
|---|---|
| #281 | the live-edge meter and re-link: shadow reader on, re-tune off |
| #287 | `rzseek` |
| #282 | the RC7 checklist with the sync program |
| #284, #285, #288 | reactgrade guardrail, Prime epoch delay, and primegrade, frozen as grader v20 |
| 08ca520 | grader v21: eval-data-v3 adds the **2025-26 NBA season as dev only** (`dev_before`), the EXP-735 data |
| #274 | the Cycle 16 plan |

**Order:**
1. Rule #1.
2. Sync (Nick, 2:10 AM).
3. NBA.
4. NFL.

**Overnight work block (overseer, 3:50 AM).** Nick approved one more thing at 3:30 AM: code may screenshot the
**Prime** picture to read the score bug. Only the numbers are kept, never frames. YouTube TV stays DRM-blanked, and
it is not bypassed. This is logged in decisions.md.

## Sync program status
| part | state |
|---|---|
| A + B: the per-stick live-edge meter and re-link (#281) | merged. The shadow reader logs tonight, and re-tune is off |
| A2: `rzseek` (#287) | merged, tool only |
| B1: `prime_night.py`, a read-only 1 s media_session logger | merged |
| Graders: syncgrade (v18) and primegrade, which has sample floors so thin data reads PENDING (v20) | frozen |
| The Prime score bug band read, K = arena − frame clock, and the epoch signal (#290, flags off) | open. Tonight's rows grade it |
| The YTTV sync test scripts' `RZ_STICKS` runbook (#291) | open |
| The RC7 ship call | **Nick's card is still open.** Lane ④ is staging, and the shadow reader would ship on |

## Next ranked work (only the changes)
| unit | 1st | 2nd |
|---|---|---|
| ② | Land #290 (flags off). Then re-grade EXP-803 (belief-band entry) on the **larger NBA dev set from eval-data-v3**: mid-commercial needs ~23 nights for a ±5 CI | EXP-205 for NFL |
| ③ | Grade the afternoon press run on YTTV + Prime with primegrade (v20); this needs Nick's OK, daytime only | Grade tonight's EXP-800 rows (shadow), per stick and per app |
| ④ | Land #291; stage the RC7 candidate: the shadow reader on, every re-tune and epoch flag off | — |
| Int | Re-read every NBA gate on eval-data-v3 dev and report which ones now clear `MIN_DECISIONS = 60` | Merge #277 (test writers' batch 3) and #289 |
| test writers | EXP-700 / 703 NBA autopsies on the v3 dev set | — |
| ① body | Report EXP-109 speedrun (8 arms) and EXP-1402 commentary, NBA and NFL first; the Kaggle-GPU ladder points for the overseer | Saturday's size from the speedrun |

The crews (A-H) and the scout keep their Cycle 16/17 orders: NBA first, then NFL. Open landings: #272, #273,
#275, #276, #280, #286.

## Data ledger
| item | tokens |
|---|---:|
| Real, pulling or done (scout cycle 16) | ~228 M |
| New in this cycle | the 2025-26 NBA season as dev nights (eval-data-v3), counted by the scout next cycle |
| New-information estimate | 3.46 B with BDB at frame-vocab size; 1.46 B without it |

## Waiting on Nick
- the RC7 sync card;
- RC6.3;
- starting the feed-lag probe tonight;
- the BDB competitions and the Kaggle identity check;
- the keys;
- the camera and mic options, which stay on hold.

## Standing rules
- No deploys and no Mac-side experiments while games are live. TNF is tonight.
- Personal-use tags on B data.
- Usage checkpoints (ET): Fri 8 PM 58%, Sat 67%, Sun 76%, Mon 85%.
