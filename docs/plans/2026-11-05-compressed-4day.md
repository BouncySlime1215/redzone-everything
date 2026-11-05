# Compressed plan: master plan v2, Phases 0-2 by Sun Nov 8

**Source:** Nick, 7:30 PM ET Nov 4. Colab Pro+ is bought (~$50/mo, ~500 compute units (CU), A100/L4, background
runs). "GO HARD, get this done within the next few days." This plan compresses
[master plan v2](https://github.com/BouncySlime1215/redzone-everything/blob/codex/resume-redzone/docs/plans/2026-11-04-noggin-master-plan-v2.md)
Phases 0-2 into ~4 days.

**Owners:**
- The Start Planner writes this plan.
- Lane ① owns the CU estimates and corrects them in its PRs.
- The overseer launches every notebook in Nick's browser.

**Binding:**
- [grading.md](../grading.md) holds: fast, not sloppy.
  - Every run is pre-registered in [experiments.md](../experiments.md) (success bar + nights needed) before it starts.
  - The integrator freezes the graders.
  - Locked is read once per release.
- The playbook's line (§5) and the self-taught rule hold.
- The Mac stays light: training and data run only on Colab/Kaggle.

## Rules for every Colab notebook
1. **Self-contained.** Inputs are public: this repo at a pinned commit SHA, plus public data URLs. No Drive mount, no
   personal tokens, and no secrets inside the notebook. Nick adds secrets himself.
2. **Prints `grades.json`** in the output cell. It carries the seed, data hash, recipe stamp, code pack id, the
   selection split (valid only), the learning curve and the train/valid/test gap (rules 10-13).
3. **Background-safe.** It writes checkpoints every ≤ 30 min, resumes from the last one, and prints its CU spent
   at the end.
4. **Compute-matched baseline** beside every model (rule 12). Heads are judged by lift over the context-only
   baseline.

## CU budget (A100 ~13 CU/h, L4 ~5, T4 ~2)

| when | job | GPU | hours | CU | lane | EXP |
|---|---|---|---:|---:|---|---|
| Wed night | V8 Mind (in flight, finishes ~10:45 PM ET) | T4 | ~3.6 | ~7 | ① | EXP-006 |
| Wed night | V8 watchdog rerun, uncapped (1436 steps; V8 did ~450 in 40 min on a T4, so ~2.1 h T4 ≈ ~1 h L4, + 686 answers ×2) | L4 | ~1.5 | ~8 | ① | EXP-085 |
| Thu | 32B Teacher (AWQ, vLLM) for the starved CBB / NHL / MLB label queue; stops when the queue is empty | A100 | 2-4 | 26-52 | ① | EXP-086 |
| Thu | V8.2 students from the new Teacher labels, full recipe (reasoner 2 epochs ~33k rows, watchdog 1 epoch, all 5.5k + 686 test rows answered) | L4 | ~3.5 | ~18 | ① | EXP-087 |
| Thu | Factory re-run with the newest pack | Kaggle CPU | n/a | 0 | ① | EXP-049 |
| Thu | Suspense S + WP calibration + one-event-language spec | CPU | n/a | 0 | ①④ | EXP-001/002/003 |
| Fri | Event-token shards from the factory | Kaggle CPU (Colab CPU fallback) | ~5 | 0-5 | ① | EXP-007 |
| Fri | Point-process timing model (breaks, scores, crunch entry/exit); a small model, T4 is enough | T4 or L4 | 2-4 | 5-20 | ① | EXP-022 |
| Fri | Belief tracker + conformal floor | CPU | n/a | 0 | ②③ | EXP-023/024 |
| Sat | **World model v1 pretrain** (~0.5B real event tokens in the ledger estimate; 100M params × 3 epochs ≈ 9e17 FLOPs ≈ 2 h A100 at 40%) plus heads and evals | A100 | ~3-4 | ~40-52 | ① | EXP-047 |
| Sat | Size check first: 25M and 50M runs on the same shards (compute-optimal for ~0.5B tokens is ~25M) | L4 | 2 | 10 | ① | EXP-047 |
| Sun | **Planner v1** (search over the world model + conservative offline RL on real nights) | L4 | 3-6 | 15-30 | ①② | EXP-056/057 |
| Sun | Locked grading (the release read) + shadow mode Sunday night | CPU / Mac | n/a | 0 | Int ③④ | EXP-004 |
| | **Planned total** | | | **~130-200** (lane ① estimate, Nov 5 00:30 UTC) | | |
| | Re-run slack | | | ~200 | | |
| | **Reserve (not to be spent)** | | | **100** | | |

Lane ① CU basis: CU = GPU-hours × rate (A100 13, L4 5, T4 2), with 0.2-0.3 h of setup per notebook. L4 ≈ 2-2.5× T4 and A100 ≈ 2.6× L4 on this workload, so CU per FLOP is about the same on A100 and L4: A100 buys wall-clock, not savings. Every notebook prints its own CU spent at the end, and this table gets corrected from those numbers. Compute note for the pretrain: 6·N·D for 100M params × 1B tokens is 6e17 FLOPs, about 1.3 h on an A100 at
~40% utilization. So 8 h covers ≤ 3 epochs, the heads and evals. For ~1B tokens the compute-optimal size is
~50M params, so the 50M run decides whether 100M is worth it (add tokens before size).

## Day by day

Each gate is pre-registered. A failed gate ships OFF behind its flag, and the next day's work still starts
from what passed.

### Wed night (now → 10:45 PM ET)
- V8 finishes. The V8 watchdog reruns uncapped on L4.
- RC6.1 night: gold data (EXP-008, ③). Sync calibration from tonight's logs (EXP-084, ③).
- **Gate:** V8 lifts NBA crunch on dev over V7 with no leak. Watchdog: per-kind accuracy on held-out
  wordings, 0 unusable answers.

### Thu Nov 5
- The 32B Teacher on A100 for the CBB / NHL / MLB queue, then V8.2 students.
- Factory re-run with the newest pack.
- **Lands:**
  - the suspense line (EXP-001, ④ scorecard + ① S);
  - WP calibration (EXP-002, running; NHL misses on dev);
  - the one-event-language spec (EXP-003).
- **Gate:**
  - the Teacher label agreement is a secondary line only (rule 3);
  - V8.2 lift over V8 on dev;
  - S passes EXP-001 gate 1;
  - the event spec has < 1% unmapped events.

### Fri Nov 6
- Event-token shards from the factory (EXP-007).
- Point-process timing (EXP-022), belief tracker (EXP-023), conformal floor (EXP-024).
- **v3 locked set** (EXP-017, ④): 8 NBA + 8 NHL locked nights, so Sunday's read can PASS.
- **Gate:**
  - shards hashed and in the ledger;
  - the point process beats the trees on break-start log-loss (dev);
  - per-stick delay error p95 ≤ 2 s;
  - the conformal bound and its coverage stated on dev.

### Sat Nov 7
- **World model v1 pretrain** on A100 after the 50M size check (EXP-047).
- Retrieval of similar moments (EXP-052, CPU) if time allows.
- **Gate:** next-event loss and calibration beat the trees on valid, with the train/valid gap reported. Locked
  is not read.

### Sun Nov 8
- **Planner v1** (EXP-056/057), graded **once on locked** under the frozen graders.
- **Shadow mode Sunday night** (EXP-004). NFL Sunday is the test.
- **Gate (master plan v2, Phase 2):** the planner beats the hand-built decider on % of oracle in ≥ 2 leagues on
  locked, and passes the strict bar: 0 mid-play entries, NBA/NHL crunch ≥ live, your teams pass, everything
  else ≥ live, worth it ≥ live.
- If it passes, it **runs in shadow only**. It reaches the TV only through the normal promotion step.

## Lanes
- **①:** every GPU job above. One notebook per job, at a pinned SHA, with grades.json. Correct the CU estimates.
- **②:** the conformal floor (EXP-024), the belief tracker in the decider (EXP-023, with ③), and planner v1's
  decider interface (EXP-056).
- **③:** sync calibration (EXP-084), the belief tracker data and the RC6.1 night (EXP-008), and Sunday-night
  shadow mode on the Mac within RAM (EXP-004).
- **④:** the suspense line (EXP-001), the morning shadow score + DR estimator (EXP-004/005), the event-language
  contract (EXP-003), and v3 locked on Friday (EXP-017).
- **⑤:** the "worth it?" rating (EXP-040, evaluation only), ready before Sunday night so worth it ≥ live can
  be read. Then the stack lands.
- **Integrator:** freezes each day's graders before the run, merges the train daily, and does Sunday's single
  locked read.

## Risks
- **Locked evidence:** NBA/NHL have only 4 locked nights until v3 lands Friday. A Sunday PASS depends on v3
  landing on time.
- **Friday is the most crowded day** (three timing components). If one slips, the planner uses the trees for
  that input.
- **Sync σ ≈ 9 s** limits every catch-up experiment until EXP-084 passes.
- **Market data:** v2 lists "calibrate win probability against betting markets" and "betting-line jumps" as a
  news sensor. Under the playbook's line, closing lines may only be a hidden calibration and evaluation prior.
  Live line moves as a switching signal stay **excluded** (EXP-077) unless Nick says otherwise.
