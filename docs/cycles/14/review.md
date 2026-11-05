# Cycles 09-15 review (integrator), Wed Nov 4 9:30 PM to Thu Nov 5 1:30 AM ET

Covers cycles 09 through 15. The RC fast track ran inside them, so they're reviewed together. Scored on eval-data-v1,
pinned. Locked was read once each for RC6.2's NBA/NFL sets and for #257's NBA option, and never used to tune.

## Shipped

- **RC6.2 (1bf4150), deployed 10:55 PM ET with the TV off.** Contents:
  - #232 benched-feed health fix;
  - #227 league-scoped NBA/NFL set, with NHL at {};
  - #231 EXP-204 late-and-close;
  - #229 Prime hand-back.

  Two full suites passed on separate machines. On dev, mid-play entries went from 2.34 to 0.48 per 100, and nothing
  regressed.
- **Open with Nick:** the NBA card for RC6.3. The recommended option is the set plus #257's stuck-hold leave gate
  (#258).
  - On locked, it equals RC6.2, apart from +0.7 dead air and 3-6 allowed low-leverage leaves.
  - On dev under --resync, your-team crunch is 31/31, against 17/31 for the set alone.

## Graders frozen (v10 to v16)

| v | what | why |
|---|---|---|
| 10 | `mid_break`, `delay_err_p95` (per stick, data/sync_truth), Prime sub-gate on `press_to_tv_p95` | Nick's RC7 scope (9:45 PM) |
| 11 | `entries_mid_play_forced` (double Go now on its own line); college PLAY_LEN/RESUME_LEAD | Nick 10:55 PM; lane 1 |
| 12-13 | F8 `license_check.py` + docs/datasources/terms.json | Nick's data-B call (11:20 PM) |
| 14 | `time_to_see_<lg>`, `catchup_<lg>` ratchet (docs/catchup_ratchet.json, 180 s everywhere) | Nick 12:28 AM |
| 15 | EXP-301 delay-and-react grader (lane 3, #266) | EXP-209 |
| 16 | red sources always fail; landing grader (EXP-705) + family Holm (docs/families.json) from the test writers | scouts #263, #249/#268 |

## RC7 gates today (candidate, locked)

- **Never join at a break:** FAIL. 35% of entries land mid-commercial. EXP-205 ships OFF, and it can't pass until
  sync doubt is about ±2.5 s.
- **Sync p95 ≤ 2 s:** PENDING. There are no truth rows yet. #235's producer is waiting on the Mac's gold capture.
- **Switches ≤ 3 s, including Prime:** PENDING until the real-TV press run, including Prime, which happens Thursday
  afternoon with Nick's OK.

## Lessons

- **Grade every league set on both harnesses.** RC6.2's NBA set passed the honest harness and lost your-team crunch
  under --resync. The cause was the time-shift buffer key (lessons.md, Test Factory F4).
- **Don't put a decider import in a grader.** #257 first imported the decider's close-and-late table into
  lab/leverage.py. The grader keeps its own copy now, and a test asserts the two are equal.
- **A clean environment catches missing deps.** pyarrow was missing, which kept #255 and #259 out of one train. CI now
  installs requirements-ci.txt.

## For cycle 16

1. **F4:** two-harness grading becomes a required column for every league set (Thursday morning).
2. **F1/F2:** the first 10 golden nightmare nights from dev, including WSH @ CHI Oct 24 and #257's floor cases, with
   the ratchet test.
3. **Lane 2:** EXP-208 (buffer passes --resync), EXP-209 grids, sync truth rows from the gold capture.
4. **Lane 3:** the real-TV press run, including Prime, Thursday afternoon before games.
5. **Open PRs that need work:** #206 (failing on the freeze; grader review), #187 and #186 (cancelled CI).
