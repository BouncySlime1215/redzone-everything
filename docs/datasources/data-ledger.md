# Data ledger

Planners fill in one block per cycle. Sources and verdicts: [sources-catalog.md](sources-catalog.md).
Waves and targets: [ingest-plan.md](ingest-plan.md).

Rules: count realistic values (rows x populated fields), de-duplicated against what is already
held. Synthetic is always its own row. Never list a red source. Never write keys or tokens here.

## Cycle template

Copy this block, set the cycle number and date, and fill every column.

### Cycle NN (YYYY-MM-DD)

| Source | Wave | Verdict | Status (planned / pulling / done / blocked) | Seasons held | Rows | Fields | Values added this cycle | Values held (cumulative) | Stored GB | Kaggle shard / version | HF backup (yes / no, not allowed) | Attribution in README + model card (yes / no) | Nick action needed | Notes |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| example: Retrosheet events | 1 | green | done | 1950-2025 | 15.0M | 100 | 1.50B | 1.50B | 2.0 | rz-data/v3 shard retro-01 | yes | yes | none | notice text added |

### Cycle totals

| Bucket | Values added | Values held | Stored GB |
|---|---:|---:|---:|
| Green, observed | | | |
| Yellow, observed | | | |
| Derived (green + yellow) | | | |
| ESPN-gated (only if Nick approved) | | | |
| Synthetic (cache) | | | |
| **Total** | | | |
| Quota used / 215 GB (working cap 120 GB) | | | |

### Saturation check

| Check | This cycle | Last cycle | Stop? |
|---|---|---|---|
| Distinct real events held | | | |
| Distinct real slate-nights held | | | |
| Synthetic : real token ratio (stop near 3x) | | | |
| Held-out real-night world-model loss after the last 2x synthetic step (stop if < 1% better twice) | | | |
| Distinct synthetic slates (stop near 1-2M) | | | |

### Blockers and decisions

- Waiting on Nick (keys, Rules tabs, sign-offs), with the date asked:
- Decisions logged in docs/memory/decisions.md this cycle:
- License changes spotted (re-check any source whose terms changed):

## Synthetic and teacher rows (Data planner E, EXP-1400..1499)

Every synthetic row is counted apart from real, tagged synthetic=true and "personal-use, not for commercial
release", and the cap is code: `python scripts/synth_build.py check SYN` fails if any league's synthetic tokens
exceed 3x its real tokens (EXP-097). Self-play token equivalents are passed in with `--other-synthetic`.

| Source | Kind | Built from | Where | License | Counts toward 3x cap | Status |
|---|---|---|---|---|---|---|
| Home/away mirror | augmentation, vocab v1 | factory token shards, train nights only (`--train-before`) | SYN/<league>/<year>.mirror.npy | inherits the source shard; personal-use | yes | code ready (EXP-1401) |
| Tempo re-time (live gaps x 0.9-1.1, breaks untouched) | augmentation, vocab v1 | same | SYN/<league>/<year>.retime.npy | inherits; personal-use | yes | code ready (EXP-1401) |
| Teacher 32B answers | teacher-v1 side channel (aux target only) | EXP-086 teacher_out.jsonl, train nights only | SYN/teacher/<league>.npz + text.jsonl | personal-use (Qwen3 Apache-2.0 model; plan B moments) | yes | waiting on the EXP-086 run (EXP-1400) |
| Teacher 32B causal commentary | teacher-v1 mode 2 (may be context) | build_moments train plays, brief only | SYN/teacher/<league>.npz + text.jsonl | personal-use (plan B moments) | yes | notebook ready (EXP-1402) |
| Factory self-play | PICK rows (#122, #152) | real seasons, re-timed slates | factory shards | personal-use | yes, as token equivalents | lane ① runs it |
