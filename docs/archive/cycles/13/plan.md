# Cycle 13 plan (Start Planner), Thu Nov 5, ~04:00 to 05:00 UTC (60-min rhythm, overnight)

**From:**
- integration/next at 90b6c98 (the RC train: #227, #229, #231, #232, grader freeze v10);
- the Cycle 12 plan (#236);
- lane 2's Cycle 12 report;
- Nick's 10:55 PM ET decisions.

**Queue:** [docs/experiments.md](../../../experiments.md). Every run is pre-registered with its success bar and
nights needed before it starts (grading rule 2). Use your own id block (planner ≤099 then 800s, ① 100s, ② 200s, ③ 300s,
④ 400s, ⑤ 500s, integrator 600s, test writers 700s, Data Division A-F 1000s-1500s, scout 1600s). Before starting a go-wide id, check for a `claude/exp-<id>` branch.

## Nick's 10:55 PM decisions (binding)
- **RC6.2 (1bf4150) is deploying now from the overseer session.**
  - It goes out through `deploy.py` with the benched-feed health rule, the usual health watch and auto-rollback.
  - The TV is off, so no viewer is at risk.
- **A second Go now tap switches instantly, even mid-play.**
  - This is Nick's explicit control. A single tap still waits for a dead ball.
  - A grader must not count a second-tap switch as a decider mid-play entry. The integrator labels these rows.
- **New standing rule: no deploys and no Mac-side experiments while games are live and Nick is watching.**
  - The Mac stays plugged in on game nights.
  - This applies to every lane. Mac work is scheduled for the daytime: tomorrow afternoon, before TNF.
- **Launches (the overseer):**
  - EXP-103 (green world model, A100, c5d4b7c) starts tonight.
  - **Nick picked data B (11:20 PM).** Teacher 5a903ca (32B, the CBB / NHL / MLB queue) launches on an A100 tonight
    once lane ① unblocks it (EXP-086).
  - The factory event-token run (d55d661) starts when the current Kaggle factory run ends, around 2:30 AM ET. It
    uses **all** sources (ESPN-derived, NHL api-web + shifts, Statcast), tagged personal-use.
  - EXP-022 stays on Friday.
- **Lane 2's top priority for RC7 is the sync fix.** It unblocks "never join at a break": 35% of today's entries
  landed mid-commercial.
- **The real-TV Prime press run is tomorrow afternoon, before games.** Nick OKs it in the Mac session then.

## Data Division (Nick, 12:05 AM reorg: "THE BRAIN IS THE MAIN FOCUS")
The org chart, id blocks, first sources per type and pacing are in
[data division](../../../plans/2026-11-05-data-division.md).
- **Planners:** one data planner per type, A-F (play-by-play, context and hype, media metadata, markets,
  synthetic and teacher, broadcast formats). They work with the scout.
- **Test writers:** EXP-700-799.
- **Lanes 1-4 keep the body.**
- **Pacing:** at or below 85% weekly usage by Mon 8 PM ET. The overseer reads the checkpoints.

| Checkpoint | Usage at or below |
|---|---|
| Fri 8 PM ET | 58% |
| Sat 8 PM ET | 67% |
| Sun 8 PM ET | 76% |

## Data scout loop (Nick, 11:35 PM, standing): "100x more unique data sources and data types, agents hunting 24/7"
- **Owners:** lane ① and a dedicated scout thread, which the coordinator is opening.
- **The loop, every cycle:**
  1. find a source;
  2. license-check it: green, yellow or red, with the B rules for personal use;
  3. rank it by **new** information per GB;
  4. add it to the factory;
  5. add its tokens to the ledger.
- **Priority order:**
  1. other leagues and levels: MiLB pitch-by-pitch (MLB Stats API), G League, AHL/ECHL, WNBA, every college
     division plus college baseball, soccer open event data (StatsBomb open data and others), EuroLeague/FIBA,
     NPB/KBO. These are world-model pretraining data, because all sports share clocks, momentum and breaks;
  2. tracking: the NFL Big Data Bowl releases, NHL EDGE, public Next Gen Stats aggregates;
  3. odds history and live line moves, free tiers first. Any paid feed needs Nick's OK;
  4. the hype layer (EXP-090..093);
  5. our own live-night logs as gold, every night;
  6. synthetic self-play, capped at about 3x real.
- **Target:** the ledger in docs/datasources grows every cycle, to several billion tokens this month (EXP-097).
- **Out:** broadcast video and audio. They need licensing, so they wait for the company stage.
- **Tonight (the overseer):** Teacher EXP-086 (A100, plan B) and EXP-103 world model v2 (A100) are running. The
  all-sources factory relaunches at about 2:37 AM ET.

## Status
- **RC6.2:** deploying (1bf4150). RC6.1 9322c28 is the rollback target.
- **Gold capture since 9:40 PM:** the overseer posts the check. Lane 3 confirms it in the morning (EXP-008).
- **RC7: Nick's three hard requirements, gated by the integrator's v10 lines.**

  | # | Requirement | Owner | v10 line |
  |---|---|---|---|
  | 1 | Switches ≤ 3 s | ③ | the Prime press sub-gate (#234, f7e13e7) |
  | 2 | Sync p95 ≤ 2 s | ② (lane ③'s EXP-084 fit is an input) | per-stick `delay_err_p95` (#235 producer) |
  | 3 | Never join at a break | ② | `mid_break` (real breaks only). EXP-205 is gated on requirement 2 and ships OFF until then |

- **Merged in the RC train:**
  - #227: league-scoped NBA + NFL defaults, with NHL kept on live behavior;
  - #231: the EXP-204 late-and-close cap at 0.95 for NFL + NBA;
  - #232: a benched feed is not a failing feed;
  - #229.
- **Go-wide:**
  - #237 wires #223 (EXP-023) behind `noggin.belief.enabled`, which stays OFF; it failed its bar honestly.
  - #240 is EXP-056 (the planner interface).
  - #218, #225, #226 and #228 still need verdicts.
- **Still waiting on Nick:**
  - the NHL api-web risk call;
  - the CFBD key;
  - the TV overlay path for EXP-038.

## Data ledger
| league / source | tokens | status |
|---|---:|---|
| NBA 2025 (cdn) | 3.76 M | in the ledger |
| NFL 2019 | 0.27 M | in the ledger |
| nflverse NFL 1999-2024 | ~7 M est. | wave 1 green, #169 |
| Retrosheet MLB 1974-2024 | ~70 M est. | wave 1 green |
| NBA.com cdn 2019-25 | ~26 M est. | wave 1 green (converter, ①) |
| hoopR NBA / MBB, ESPN NHL / CFB | ~285 M est. | red; only if Nick picks B |
| NHL api-web 2011-19 | ~35 M est. | waiting on Nick's risk call |
| CFB (CFBD) | PENDING | waiting on Nick's key |
| live nights (gold) | first since 9:40 PM | lane ③ check in the morning |

**Data B (Nick, 11:20 PM):** the red rows are now in, for personal use only. Every dataset and model trained on them
is tagged "personal-use, not for commercial release". The factory run (d55d661 or newer) fills all rows overnight,
about 400 M tokens (plan B in the 4-day plan).

## Lanes: the next ranked work
| lane | 1st | 2nd | 3rd |
|---|---|---|---|
| ① | **Data B now:** unblock Teacher 5a903ca for the A100 tonight (EXP-086); the factory pack (d55d661 or newer) on ALL sources, personal-use tags on every shard, hashed into the ledger (EXP-007); EXP-103's grades.json and CU line | Land #169 and #230 (EXP-103 pre-registration); get the Teacher 5a903ca and EXP-102 notebooks ready for either A/B answer | EXP-022 point-process design ready for Friday; correct the CU table from the printed numbers |
| ② | **Sync p95 ≤ 2 s (requirement 2, top priority):** #235's producer on tonight's sync.jsonl, fold in lane ③'s EXP-084 fit, report delay_err_p95 per stick on dev | Then re-grade EXP-205 (never join at a break) against its frozen bar; land #237 behind its flag | EXP-056 planner interface (#240) for Sunday |
| ③ | **Switches ≤ 3 s (requirement 1):** prepare the real-TV Prime press run for tomorrow afternoon before games (Nick OKs it in the Mac session), #233 + #234 ready | EXP-084 delay fit from the RC6.1 / RC6.2 logs, handed to lane ② | The morning gold-capture check (EXP-008); EXP-004 Mac shadow (#187) for Sunday, daytime only |
| ④ | Watch the RC6.2 deploy health with the overseer; record RC6.2 in the RC history | The RC7 cut checklist: the v10 lines, the second-tap rule, the no-deploy-while-live window (cut by the afternoon, before TNF 8:15 PM ET) | Land #215 (EXP-003) and #219 (EXP-005 DR); then EXP-017 v3 locked for Friday |
| ⑤ | Land #221, #222, #224, #238, #239 with tests (flags off) | The phone's second Go now tap: show "switches now" on the second tap so it reads as a choice (display only) | EXP-040 "worth it?" rating, ready before Sunday night |
| Int | Label second-tap switches in the graders so they aren't counted as decider mid-play entries (needs a freeze bump) | Merge #235, #237, #233, #234, #215, #219, #169, #230, #206, #187, #186 | Gate the RC7 cut on the three requirements; a verdict per go-wide PR |

## Risks
- **Requirement 3 rides on requirement 2.** If sync p95 isn't ≤ 2 s by the afternoon, RC7 ships without EXP-205,
  and "never join at a break" stays a known miss in the RC7 notes.
- **The no-deploy-while-live rule** leaves RC7 a window only from the afternoon until TNF at 8:15 PM ET. The
  Prime press run uses the same window.
- **Data B adds personal-use risk only.** Each source keeps its guardrails: public endpoints, rate limits, no logins
  or bot-block bypasses. A company version retrains on green data later.
