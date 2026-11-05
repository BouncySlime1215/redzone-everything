# Data Division: org chart, id blocks, first sources and pacing

**Source:** Nick, 12:05 AM ET Nov 5: "THE BRAIN IS THE MAIN FOCUS → REORG."
- A new **Data Division** is always on. It has one data planner per type, and each planner runs a crew of worker
  agents.
- The crews pull data, convert it to event tokens (vocab v2), and land it in the factory and the ledger.
- **Test writers** continuously write pre-registered experiments and graders.
- **Lanes 1-4 keep the body.**

**Rules that still hold:**
- **Workers' outputs are trusted** with no second review. The exception is a **license line for every source**
  in docs/datasources, under the personal-use B rules (decisions.md, 11:20 PM).
- **Tag** every B dataset and every model trained on one "personal-use, not for commercial release".
- **B guardrails:**
  - public endpoints only;
  - no logins, paywalls, CAPTCHAs or bot-block bypasses;
  - respect rate limits and robots, and cache and back off;
  - no pirated streams;
  - articles are read for derived numbers only.
- **No video downloads, ever.**
- **No credentials.** Nick adds every key as a Kaggle secret himself.
- **Paid** APIs need Nick's OK.
- **Compute:** pulls run on Kaggle or Colab CPU, or on GitHub Actions on a schedule. Never on the Mac.
- **Grading is unchanged** for anything that changes the brain:
  - pre-register first;
  - the integrator freezes the graders;
  - locked is read once per release.

## Org chart

| Unit | Thread | Owns | Id block |
|---|---|---|---|
| Start Planner | `Start Planner` | the queue, the cycle plans, ranking, and the planner rows | ≤ 099, then 800-899 |
| **A. Play-by-play** | `A, play-by-play` | every league and level → event tokens (vocab v2) | 1000-1099 |
| **B. Context and hype** | `B, context and hype` | odds as inputs, injuries, refs, weather, standings, GDELT, Wikipedia hourly pageviews; EXP-090..093 move here from lane ① | 1100-1199 |
| **C. Media metadata** | `C, media metadata` | YouTube Data API metadata, league highlight tags, a video-understanding API check | 1200-1299 |
| **D. Markets** | `D, markets` | live odds and line jumps, prediction-market public prices | 1300-1399 |
| **E. Synthetic and teacher** | `E, synthetic and teacher` | 32B Teacher commentary per play, and self-play (capped at ~3x real) | 1400-1499 |
| **F. Broadcast formats** | `F, broadcast formats` | TV timeout and media-break rules per league and network | 1500-1599 |
| **Scout** | `Scout` | finding and ranking new sources (EXP-097, scout.json #247), and feeding A-F | 1600-1699 |
| **Test writers** | `Test writers` | pre-registered experiments and grader proposals; the integrator freezes them | 700-799 |
| Lane ① | lane 1 | the body: Reflex, Mind, Teacher runs, world model training (EXP-086, EXP-103, Saturday's world model) | 100-199 |
| Lanes ②-④ | as before | cutting and sync, controls, hardening | 200s, 300s, 400s |
| Lane ⑤ | as before | the phone (display only) | 500s |
| Integrator | integrator | graders, the freeze, merges, the test factory (F1-F7) | 600-699 |

Each planner runs its crew in parallel, as workers or scheduled jobs. Each crew lands its output in:
- the private Kaggle dataset (raw and tokens);
- docs/datasources/data-ledger.md (tokens per source, every cycle);
- scout.json (license line and B tag).

## First sources per type
| type | first sources | terms / notes |
|---|---|---|
| A | hoopR NBA + MBB, ESPN CFB / NBA pbp, NHL api-web + shifts, MLB GUMBO 2008-25 (wall clocks), MiLB pitch-by-pitch (MLB Stats API), wehoop WNBA + WBB, Cricsheet, StatsBomb open data | B (personal use) except Cricsheet (green) and StatsBomb (yellow, credit) |
| B | nflverse lines, injuries and officials; Wikimedia hourly pageviews + EventStreams; GDELT GKG; Wikidata; Open-Meteo historical weather; our standings and playoff-odds sims | green or yellow; lines are importance inputs only, never shown |
| C | YouTube Data API v3 (official league and team channels: titles, upload time, views and velocity, chapters, the daily top 10); MLB Stats API game content (highlight play tags); NHL api-web highlights; NBA video events | YouTube needs a free API key that Nick adds (10k units a day), and its policy requires stored metadata to be refreshed or deleted within 30 days. **Metadata only, no downloads.** A video-understanding API on public links (Gemini's YouTube URL input first) is checked for terms and price before any use; paid needs Nick |
| D | Polymarket public market data; Kalshi public market data; The Odds API free tier (Nick's key); the SBRO archive for history | public read endpoints, rate limits; never shown and never a betting prompt (decisions.md, 11:10 PM) |
| E | Teacher 32B commentary per play from the EXP-086 run (A100 tonight); self-play from the factory | synthetic ≤ ~3x real, counted separately in the ledger |
| F | public league rulebooks and media-timeout rules (NFL, NCAA football and basketball, NBA, NHL, MLB pace of play), measured break lengths from our own logs (EXP-019) and #181's BreakBook | public documents; tables only |

## Pacing (usage 42% weekly at 12:05 AM Thu; hard stop 89%; target ≤ 85% at Mon 8 PM ET)
- **The crews' 24/7 is jobs, not agents.**
  - Workers write the pull and convert code once. Kaggle, Colab or GitHub Actions jobs then run it around the
    clock.
  - Agents wake on job results and once a cycle, not continuously.
- **Checkpoints** (straight line from 42% to 85%; the overseer reads them):

  | Checkpoint | Usage at or below |
  |---|---|
  | Fri 8 PM ET | 58% |
  | Sat 8 PM ET | 67% |
  | Sun 8 PM ET | 76% |
  | Mon 8 PM ET | 85% |

- **When a checkpoint is over:**
  1. Crews C, D and F pause first.
  2. Then crew B.
  3. A, E, the body lanes and the integrator keep running.
- The hard stop at 89% still applies.

## What lands first (Cycle 14)
Each data planner does three things:
1. posts its crew (the worker list);
2. pre-registers its first row in its block, with a ledger bar (tokens landed, a license line, and the B tag);
3. lands its first source.

The test writers draft their first 5 rows in 700-799.
