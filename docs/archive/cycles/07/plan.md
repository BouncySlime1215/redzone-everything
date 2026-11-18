# Cycle 07 plan (Start Planner), Wed Nov 4, ~22:35 to 23:35 UTC (60-min rhythm)

**From:** what landed since cycle 06. There's no cycle 06 review yet (due 22:25), so this is built from
integration/next 8cb0f6e and the open PRs. Rules as always:
- bar (B) on locked, flags off until they pass;
- no signal from Nick;
- read `docs/citymap.md` first and tag every new module;
- every PR shows its North Star delta plus a RAM/CPU line.

**Tonight is a live night.** NBA/NHL tip around 7 PM ET (00:00 UTC). Everything that turns tonight into data
or keeps it safe comes first.

## Status
- **City map / one brain:** merged #165 + #168 (one-brain steps 1-4: one crunch table, lab predicates out of
  the live path, one tree walker and loader) and #175 (`contract_violations` reads the kernel).
- **Lane ③ merged:** #167 (STK-06 unheld standby), which turns the red e2e test green, and #164.
- **Lane ⑤ merged:** #161 (the WV surface), plus #163.
- **Bar (B) promotion:** blocked on evidence, not behavior.
  - Locked has NHL 2 / NBA 4 nights, so crunch reads PENDING for live and candidate. For NHL, the
    candidate's 76.2 has a CI of 58.1-98.1, and live's 89.4 is inside it.
  - On dev the candidate leads NHL crunch, 82.8 vs 79.7.
  - The real gap is the 4 your-team catch-ups (lane ②).
  - More locked nights arrive with v3 on Fri (#149; lane ④ to report the count).
- **HEAT 2.0 (#166, flag off):** WV rank agreement with the oracle on locked:
  - NBA 74.2 vs 65.5 (clock-only), NFL 58.6 vs 42.4;
  - calibration error 0.057 / 0.056;
  - crunch exit accuracy on locked NBA 71.4 / NFL 92.1 (today's rule: 14-18).
- **NFM (#169):** tokenizer v1, token shards, the data ledger and docs/data-sources.md.
- **Crunch vs crunch:** #171 (STK-07, hold the left game at the dead ball we left, off) and #174 (the crunch
  block holds until an OT winner airs; NHL crunch live +2.5 locked) are in review. The TREE-6 Mac lookup has
  not started.
- **Merge backlog is the bottleneck:** 30 open PRs. **#133 → #132 (the measured YTTV seek, rule #1) are still
  unmerged.**

## Data ledger (from #169; real data so far)
| league / season | games | events | tokens | crunch-event share | OT / comeback / blowout games |
|---|---:|---:|---:|---:|---|
| NBA 2025 | 1,324 | 625,000 | 3.76 M | 3.6% | 66 / 367 / 276 |
| NFL 2019 | 267 | 45,209 | 0.27 M | PENDING | PENDING |
| NHL, CFB, CBB, MLB | PENDING | | | | |
| synthetic (self-play) | PENDING | | | | |
| live nights (gold) | first one tonight | | | | |

- The estimated real corpus across every planned season is **~0.7-0.9 B tokens**.
- **CFBD is waiting on Nick's key** (a Kaggle secret). **MLB Statcast is waiting on Nick's OK**, because its
  terms are unclear.
- The crunch-event share of 3.6% is low, so stratified sampling has to lift it.

## Integrator (first, because the backlog is the bottleneck)
1. **#133 → #132 before tonight's games**, taking #132's `_rewind` (one wake press). Then #160, #154, #174 and #171.
2. Then #166 (HEAT 2.0, off), #169 (NFM shards), #173 (provenance), #172 (stakes).
3. The cycle 06 review, with the CI on every thin locked line and the v3 night count.

## ① Noggin training
1. **NFM pretraining run #1:** a 50-100M model on the NBA + NFL shards (sized to the tokens per the cycle 06
   compute note). The kernel goes to the overseer for Kaggle. Gates: next-event loss and calibration on
   locked; the WV head fine-tuned from it vs #166's trees (rank agreement on locked).
2. **Fill the ledger:** NHL api-web + shifts, NBA back seasons, NFL nflverse, CBB ESPN. Stratify sampling to
   lift crunch, OT and comeback shares. Report every row.
3. **Importance head** (#172 wiring is up): the model behind `Scored.importance` / `stakes_line`, with
   `importance_rank_agreement` per slate.
4. **TREE-6 cloud half:** scenario tables by self-play vs the oracle on crunch-overlap nights, using #152.

## ② Noggin logic / sync
1. **Your-team catch-ups** (the behavioral promotion blocker): a your-team catch-up leaves at the first dead
   window without opening dead windows for crunch replays. Bar: 21/21 on dev seeds, crunch-in-replay not
   worse.
2. **TREE-6 Mac half:** decider lookup with WV (#166) over jump / stay / interleave / catch-up-on-return,
   reusing #171's hold. Marked test: Bills / Bears.
3. **WV into the decider** behind `scoring.watch_value`, with parity-tested migration of the old scorers.
4. NHL whistle recognition, which also fixes the leave gate reading "live" for 6+ minutes.

## ③ Performance + controls
1. **Tonight's live night:** confirm the auto-log, provenance traces (#173) and the night report (#137)
   capture everything, so the night becomes gold data. Watch for STK-05/06/07 events.
2. **#171 STK-07** on the rig before games start (hold the left game, resume on the exact dead ball).
3. **#145 GO NOW** segment split, with a real p95 row.
4. One-source timing: `shadow.flip_s` from `tv_flip_prior_s` + settle (re-apply 3379642).

## ④ App + integration hardening
1. **The onebrain migration's next steps** after #165/#168: burn down `contract_violations` per owning lane.
2. **Night report (#137)** live for tonight, so tomorrow morning's `docs/nights/` exists.
3. **The v3 locked switch on Fri** (#149). Report how many NBA/NHL locked nights it adds.

## ⑤ Features
1. **#170:** the provenance trace viewer plus the held / coming-back strip, wired to #173 and #171 once they
   merge.
2. **Data ledger + NFM status page** from the factory manifest (#169's format).
3. Show the stakes line from `Scored.stakes_line` (#172) once its model lands.
