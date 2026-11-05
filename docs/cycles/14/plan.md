# Cycle 14 plan (Start Planner), Thu Nov 5, ~05:00 to 06:00 UTC (60-min rhythm, overnight)

**From:**
- integration/next at 5cb1e75. This cycle merged:
  - the planner docs: #203, #217, #236, #241;
  - grader v11;
  - #235, the sync-truth producer;
  - #237, EXP-023 wired in, flag off;
  - #221, #222 and #224, lane ⑤ display work, flags off.
- Nick's reorg and data decisions:

  | Time (ET) | Decision |
  |---|---|
  | 11:10 PM | the two brain engines |
  | 11:20 PM | data B |
  | 11:35 PM | the scout loop |
  | 11:50 PM | H |
  | 12:05 AM | the Data Division |
  | 12:30 AM | G |

  The org chart is [data division](../../plans/2026-11-05-data-division.md). The G/H additions are in #252.
- The first PRs from the new crews (below).

**Queue:** [docs/experiments.md](../../experiments.md). Pre-register a row before running it. Each unit uses its own
id block:

| Unit | Ids |
|---|---|
| Planner | ≤099, then 800s |
| Lane ① | 100s |
| Lane ② | 200s |
| Lane ③ | 300s |
| Lane ④ | 400s |
| Lane ⑤ | 500s |
| Integrator | 600s |
| Test writers | 700s |
| Crews A-H | 1000s-1800s |
| Scout | 1600s |

**Standing rules:**
- **No deploys and no Mac-side experiments while games are live.**
- **Personal-use tags** on every B dataset and on every model trained on one.
- **Pacing:**

  | Checkpoint (ET) | Usage at or below |
  |---|---|
  | Fri 8 PM | 58% |
  | Sat 8 PM | 67% |
  | Sun 8 PM | 76% |
  | Mon 8 PM | 85% |

  If a checkpoint is missed, crews C, D, F, G and H pause first, then B.

## Status
- **RC6.2** is deployed: 1bf4150, with the benched-feed health rule.
  - The overseer launched it while the TV was off.
  - **NBA keep-or-revert is Nick's call.** #246 is the "live for NBA" variant. #258 is the low-leverage leave gate
    (EXP-208, stacked on #257).
- **RC7 (cut Thu afternoon, before TNF at 8:15 PM ET):**

  | Requirement | Owner | Status |
  |---|---|---|
  | 1. Switches ≤ 3 s | ③ | The real-TV Prime press run is this afternoon, when Nick OKs it in the Mac session (#233, #234) |
  | 2. Sync p95 ≤ 2 s | ② | #235's producer is merged. It's waiting on tonight's Mac sync.jsonl, via the overseer, plus ③'s EXP-084 fit |
  | 3. Never join at a break | ② | EXP-205 is gated on requirement 2. Crew F's break tables (#250) feed it |

  Other RC7 work:
  - #243 is lane ④'s cut runbook and RC7 checklist.
  - The second-tap Go now: #244 (grader v11 trace) and #242 (phone).
- **Compute (the overseer):**
  - Teacher EXP-086 on an A100, plan B.
  - EXP-103 world model v2 on an A100.
  - The all-sources factory relaunch at about 2:37 AM ET.
  - Usage was about 42% at midnight.
- **Nick's open asks (keys he adds himself):**
  - the CFBD key;
  - the Odds API free key;
  - a YouTube Data API key;
  - a Twitch Helix app token;
  - Google Trends alpha access;
  - joining BDB 2027 on Kaggle;
  - the TV overlay path for EXP-038.

## Data Division: the first landings (open PRs)
| crew | PR | rows |
|---|---|---|
| A play-by-play | #259: shared play table, 6 converters, Kaggle crew notebook | EXP-1000 |
| B context and hype | #255: context fact format; first landing (NFL and MLB refs, weather, injuries). Build notes in #245 | EXP-090..093, 1100s |
| C media metadata | #248: clip tags + YouTube workers | EXP-1200..1202 |
| D markets | #253: Polymarket + line-history collectors, Kalshi license check | 1300s |
| E synthetic and teacher | #251: inside the 3x cap | EXP-1400, 1401 |
| F broadcast formats | #250: break tables | EXP-1500 |
| G attention | #256: Bluesky + Twitch counters | EXP-1700..1703 |
| H web text | #260: recap labels vs % of oracle | EXP-1800 |
| Scout | #247: scout ledger, tokenizer v2 source map | EXP-097 |
| Test writers | #249: batch 1 | EXP-700..720 |

**Ranking of the test writers' batch 1 (#249).** They go first because they hit the scorecard's three FAIL lines:
1. **EXP-700**, the mid-play autopsy: 27 entries, a fixed list of causes, 2.34 per 100.
2. **EXP-703**, golden cases from the three FAIL lines. These become F1/F2 material for the integrator.
3. **EXP-704**, the gap between the two harnesses as a gate. This is the RC6.2 lesson.
4. **EXP-701**, world-model landing slack (landings in dead time, 97.7%).
5. **EXP-702**, a possession-start head (91.8%).

The rest of the batch: EXP-705..720 rank after the crews' first landings. EXP-710 fills EXP-064's empty bar, so
those two merge into one row.

## Data ledger
| source (crew) | new real tokens | status |
|---|---:|---|
| NBA 2025 (cdn) + NFL 2019 | 4.0 M | in the ledger |
| nflverse, Retrosheet, NBA.com cdn (A) | ~103 M est. | wave 1, factory relaunch ~2:37 AM |
| hoopR NBA/MBB, ESPN NHL/CFB, NHL api-web + shifts (A, B data) | ~285 M est. | factory relaunch, personal-use |
| wehoop WNBA+WBB (A) | 270 M est. | scout #247, ready |
| MiLB pitch-by-pitch (A) | 300 M est. | scout #247 |
| MLB GUMBO 2008-25 (A) | 84 M est. | scout #247 |
| StatsBomb (A) / Cricsheet (A) | 71 M / 54 M est. | scout #247 |
| Tennis slam pbp (A) / NHL EDGE (A) | 25 M / 5 M est. | scout #247 |
| Polymarket lines (D) | 30 M est. | #253 |
| on probe or hold: NCAA all divisions, AHL/ECHL, FIBA, EuroLeague, G League, NPB, KBO | ~357 M est. | scout probes |
| live nights (gold) | since 9:40 PM | lane ③'s morning check |
| synthetic + Teacher (E) | ≤ 3x real | counted separately |

The scout's count is about 1.3 B new event tokens found. About 0.86 B of that is ready now, so we land near 1 B real
by about Nov 12, on track for EXP-097's 2 B by Nov 30. Derived state (~5.7 B) is counted separately, at zero
novelty.

## Lanes and units: the next ranked work
| unit | 1st | 2nd | 3rd |
|---|---|---|---|
| ① body | Teacher EXP-086 and EXP-103: grades.json and CU lines, then the all-sources factory pack with personal-use tags (EXP-007) | Saturday's world model sized for B (~100 M params): its notebook at a pinned SHA | Land #169 and #230; the EXP-022 design for Friday |
| ② | **Requirement 2, sync p95**, on tonight's sync.jsonl via #235, with ③'s EXP-084 fit | Then requirement 3 (EXP-205), using crew F's break tables (#250) | EXP-208 (#257) verdict; EXP-056 (#240) |
| ③ | The Prime press run this afternoon (#233, #234), daytime only | The EXP-084 fit, handed to ② | The morning gold-capture check (EXP-008); EXP-004 (#187) |
| ④ | The RC7 cut runbook and checklist (#243), with the v10/v11 lines | Land #215 (EXP-003) and #219 (EXP-005 DR) | EXP-017, v3 locked for Friday |
| ⑤ | Land #238, #239 and #242 | EXP-040 "worth it?" rating, before Sunday | EXP-043 follow-ups |
| Int | Merge the crews' first landings (#247-#260) once their license lines pass F8 | Freeze graders for EXP-700 / 703 / 704; F4 two-harness grading Thursday morning | The RC7 gate read; a verdict on #246 / #258 for Nick |
| A-H, scout | Land the first source, with tokens in the ledger | The second source in their table | Run as scheduled jobs, not as always-on agents |
| test writers | EXP-700 / 703 / 704 first | Batch 2 from the crews' rows | — |

## Risks
- **Merge load.** Fourteen new crew PRs landed in one hour. Docs-only and data-only PRs should merge first. The
  integrator should not let them delay the RC7 gate read.
- **Sync can't be measured before RC7 (lane ②, 05:02 UTC).**
  - #235 on tonight's sync.jsonl gives 2 truth rows. v10's minimum is 200 per stick, and the 2 rows have an error of
    about 24 s.
  - The cause: RC6.1 predates the #140/#146 telemetry, so it logs no seeks, no behind and no sync_probe.
  - So requirement 2 needs a night on RC7's telemetry. It can't gate RC7 on data that only RC7 produces.
  - Nick decides how to handle this. Requirement 3 rides on requirement 2.
  - EXP-207 (sync carry) is inconclusive in the sim because the harness starts at the true delay. It needs a
    biased-prior knob.
- **Usage.** Each crew wakes on its job results, not continuously. The overseer reads the checkpoints.
