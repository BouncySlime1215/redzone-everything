# Cycle 10 plan (Start Planner), Thu Nov 5, ~01:05 to 02:05 UTC (60-min rhythm)

**From:**
- integration/next db739fb (#183, #200, #202 and the grader freeze v2 are in);
- the [compressed 4-day plan](../../plans/2026-11-05-compressed-4day.md);
- Nick's 8:05 PM sweep and 8:10 PM "go wide".

**Queue:** [docs/experiments.md](../../../experiments.md).
- Pre-register before running.
- Use your lane's id block.
- Before starting an id, check for a `claude/exp-<id>` branch: the overseer runs up to 20 go-wide experiments in
  parallel, and those ids are taken.

## The two blockers right now
1. **RC6.1 isn't switched yet.** The Mac tests are green. The **Mac permission guard** blocked the deploy, and it
   switches once Nick pastes lane ④'s command (a user-hands action). Until then, tonight runs RC5.1 and #173's
   gold-data capture is not on the Mac. EXP-008 and EXP-084 (sync from tonight's logs) depend on it.
2. **Merge throughput.** There are 51 open PRs. The integrator's order this hour:
   - **#145** first: review it and re-freeze the graders, or say what blocks it. This is Nick's sweep item, and
     lane ③ is waiting on it.
   - **Docs-only results** (#196, #198, #199, #201, #207): merge them as recorded fails. They're flag-off or docs.
   - Then the Phase 0 train: #206 (EXP-001 line), #186 (EXP-005), #166 (WV), #169 (NFM), #192 (V8 code), #180,
     #184 (grader, decide-step 1 s).
   - Triage the stale drafts: #26, #119, #125, #131 (already contained in #132), #139, #143, #147, #151, #153,
     #158, #159. Each one is merged, rebased or closed with a reason.

## Status
- **Bar (B):** nothing promoted since the cycle 07 review. The NHL candidate's mid-play 1-2 per seed was a
  harness artifact (#132's hidden cue without `--real-seek`). The graders are moving to `--real-seek`.
- **Phase 0 (by Fri):**
  - EXP-001 line is up (#206);
  - EXP-002 is running (NFL/NBA pass on dev; NHL misses; locked not read);
  - EXP-003 is not started;
  - EXP-004 Mac half is up (#187);
  - EXP-005 logging is up (#186);
  - EXP-006: V8 is re-running on an A100 from the public release (the T4 tab got stuck), and the watchdog
    rerun is at step 100/1436.
- **Fails recorded this cycle (honest, pre-registered):** EXP-018, 020, 026, 028 (no knee; the margin stays 10),
  029, 083 and 088.
- **New:**
  - EXP-089: unknown-landing entries rewind or wait (lane ②, rule #1, NFL live 3 → 0 bar);
  - EXP-040: the worth-it tap (#205);
  - women's basketball is off (#208, Nick 7:55 PM).

## Data ledger (cycle 10; template in docs/datasources/data-ledger.md, catalog merged in #202)
| Source | Wave | Verdict | Status | Seasons held | Rows | Values held | Nick action needed | Notes |
|---|---|---|---|---|---:|---:|---|---|
| NBA pbp (ESPN 2025 + open archive) | 1 | green | done (2025) / planned (archive) | 2025 | 625k events | 3.76 M tokens | none | from #169 |
| nflverse pbp | 1 | green | pulling (2019 done) / planned 1999+ | 2019 | 45k plays | 0.27 M tokens | none | |
| NHL api-web + shifts | 1 | green | planned | none | | | none | |
| Retrosheet | 1 | green | planned | none | | | none | notice text required |
| MoneyPuck, BDB tracking, Wikimedia pageviews, media-timeout tables | 1 | green | planned | none | | | none | per the catalog's terms |
| CollegeFootballData | 2 | yellow | blocked | none | | | **add the key as a Kaggle secret (Nick)** | |
| Baseball Savant | n/a | skipped | none | | | | none | Nick, 6:10 PM |
| Synthetic (self-play) | n/a | cache | planned (factory re-run Thu) | n/a | | | none | capped near 3× real |
| Live nights (gold) | n/a | own | **blocked: RC6.1 not deployed** | 0 nights | | | none | |

## ① Noggin training
1. **EXP-006 / 085:** V8 on the A100 rerun, plus the watchdog. Print grades.json; the overseer posts out.zip to a
   release.
2. **EXP-100, the world-model pilot** (colab/world_model_pilot.ipynb at kaggle-inputs 76f4f4e, ~30M params): it has
   been running on an A100 since ~8:05 PM. Its grades.json (throughput, `sizing_100m`) sizes Saturday's EXP-047.
   The 25M/50M size check stays Saturday's L4 row.
3. **Wave 1 ingest into the next factory pack** (#202's plan): nflverse 1999+, NHL api-web + shifts, Retrosheet.
   Fill the ledger rows. Respect each source's terms and rate limits.
4. **EXP-086/087** (Teacher 32B, V8.2) on Thursday, each pre-registered.

## ② Noggin logic / sync
1. **EXP-089** (rule #1): unknown landings rewind to a known dead ball or wait.
2. **EXP-024:** the conformal floor, with EXP-089 as its abstain.
3. **EXP-023:** the belief tracker in the decider (with ③), by Friday.

## ③ Performance + controls
1. **RC6.1:** once Nick pastes lane ④'s command, confirm the switch and that gold capture is live.
2. **EXP-084:** sync calibration from whatever logs tonight produces, even on RC5.1.
3. **#145 → merged** (integrator), then the real GO NOW p95.
4. **EXP-004:** Mac shadow (#187), ready for Sunday's NFL night.

## ④ App + integration hardening
1. **RC6.1 deploy:** the command is with Nick (Mac permission guard). Confirm the switch after he runs it.
2. **EXP-003:** the one-event-language contract with ① (due Thursday).
3. **EXP-017:** the v3 locked switch on Friday (#149). Sunday's PASS needs it.
4. **EXP-004/005 scoring half:** the morning shadow score + DR estimator.

## ⑤ Features (display only, flags off)
1. Land the stack: #170 → #177 → #178 → #185 → #190 → #205.
2. `phone.worth: true` rides the next RC, so Sunday's live nights build the worth-it baseline (EXP-040).
3. EXP-038 / EXP-039 if they're not taken by go-wide.
