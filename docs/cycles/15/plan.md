# Cycle 15 plan (Start Planner), Thu Nov 5, ~06:00 to 07:00 UTC (overnight)

**From:**
- integration/next at 85ed86a, which merged:
  - the Cycle 14 plan (#261) and Data Division G/H (#252);
  - scout #247, H's EXP-1800 (#260) and the F8 terms classification (0df3b81);
  - grader v13 (bc71cda);
  - #169 (the NFM shards and ledger), #149, #219 (the EXP-005 DR estimator), #244, #257 (EXP-208's floor fix) and
    #238, #239, #242 (lane ⑤).
- The scout's cycle 14 update (#263).

**Standing rules:**
- No deploys and no Mac-side experiments while games are live.
- Personal-use tags on all B data.
- Usage checkpoints, all ET:

  | When | Usage at or below |
  |---|---|
  | Fri 8 PM | 58% |
  | Sat 8 PM | 67% |
  | Sun 8 PM | 76% |
  | Mon 8 PM | 85% |

  Crews C, D, F, G and H pause first if a checkpoint is missed.

## Waiting on Nick
- **RC7 and sync p95** (the card in the planner thread). RC7's own telemetry is the only way to measure sync. My
  recommendation: ship RC7 Thursday afternoon and grade sync on TNF night, with the sync flag going off if it misses.
- **NBA keep-or-revert (now the integrator's RC6.3 card):**
  - #258 is "NBA set + EXP-208 gate", the RC6.3 option.
  - #246 is "live for NBA".
  - Under `--resync`, the NBA set still trails live on big plays seen and crunch live (lane ②).
- **Keys:** CFBD, the Odds API, YouTube, a Twitch app token, Google Trends alpha, a CFL key, and joining BDB 2027.

## Data ledger (scout, cycle 14)
| item | tokens |
|---|---:|
| New-information tokens found | **1.41 B** (1.39 B events + 24 M context-new) |
| Landed so far (StatsBomb, crew A count) | 87 M |
| Ready this cycle: EuroLeague (34 M), SportVU 2015-16 (48 M, yellow-B) | 82 M |
| Probe: Liiga 7 M. Waits on Nick's key: CFL 1.5 M | 8.5 M |
| Derived state (zero novelty, counted separately) | 5.7 B |

- **Red this cycle:** the Sports-Reference family (its terms §5 ban bots and AI use), and HockeyTech AHL/ECHL (the
  AHL's terms bar bots, the same precedent). HockeyTech stays out unless Nick overrides.
- **Parked:**
  - F1 timing;
  - AFL (no play-by-play);
  - FIBA LiveStats (enforcement on its betting data).
- **Skipped (Nick):** Sports-1M and UCF101.
- **terms.json:** 0 unclassified sources.
- **EXP-097 is on track:** ~1 B real by ~Nov 12, and 2 B by Nov 30.

## Next ranked work
| unit | 1st | 2nd | 3rd |
|---|---|---|---|
| ① body | Teacher EXP-086 + EXP-103: their grades.json and CU spent; the all-sources factory pack (~2:37 AM ET) with tags | Saturday's world model for B (~100 M params). #264 (portable trainer, 5B MoE config): keep Saturday at the size the data supports, and treat the 5B config as later work, with any extra CU checked against the reserve first | EXP-022 design for Friday |
| ② | EXP-205 with crew F's break tables (#250) | A biased-prior knob so EXP-207 (sync carry) can be graded | EXP-209 delay-and-react arms (#265) with ③'s EXP-301 grader (#266); then EXP-056 (#240) |
| ③ | The Prime press run this afternoon (daytime, Nick's OK) | EXP-301 grader (#266) | Morning gold check (EXP-008) |
| ④ | #243, #215, #219 and #149 are merged (05:05). The RC7 cut, once Nick answers the sync question | EXP-017, v3 locked for Friday | — |
| ⑤ | #262: `phone.worth` on by default, evaluation only. Its decisions.md line goes in the same PR | EXP-040 rating flow ready for Sunday | EXP-043 follow-ups |
| Int | Merge the crews' landings (#248, #250, #251, #253, #255, #256, #259, #263) once F8 passes | Freeze graders for EXP-700, 703 and 704 (test writers #249) | Verdicts on #246 / #258 for Nick; the RC7 gate read |
| A | Land EuroLeague and SportVU (scout ready list), after #259's converters | MiLB pitch-by-pitch | wehoop |
| B | #255 first landing; then EXP-090 rows across all six leagues | Wikimedia hourly pageviews | GDELT |
| C | Clip tags (MLB / NHL, no key) from #248 | YouTube waits on Nick's key | The video-understanding API terms and price check |
| D | Polymarket history (#253) | The Kalshi license read | The Odds API waits on Nick's key |
| E | Teacher tokens from EXP-086, within the 3x cap (#251) | Self-play dose (EXP-1401) | — |
| F | Break tables (#250) handed to ② for EXP-205 | Network-specific media timeouts | — |
| G | Bluesky / Twitch counters (#256); Twitch waits on Nick's token | Public minute-ratings reports | — |
| H | EXP-1800 on dev (recap labels vs % of oracle) | The FineWeb sports filter | — |
| Scout | Liiga probe; the CFL key goes to Nick | Next-ranked additions | — |
| Test writers | Batch 1 ranked: EXP-700, 703, 704, 701, 702 | Fold EXP-710 into EXP-064 | Batch 2 from the crews' rows |

## Risks
- **Compute creep.** #264 adds a 5B MoE config. The Colab Pro+ budget is about 500 CU, with 100 held in reserve.
  Anything beyond the 4-day plan's Saturday run needs its CU shown first.
- **Crew merge load** on the integrator. Data-only PRs go first, and the RC7 read is never blocked by them.
