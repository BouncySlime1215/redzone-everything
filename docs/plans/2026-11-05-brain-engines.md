# Two 24/7 brain engines: the context and hype data engine, and the test factory

**Source:** Nick, 11:10 PM ET Nov 4: "the brain is the biggest issue." Two engines run around the clock.
- **Lane ① owns the data engine.** It runs on Kaggle or Colab CPU only, never on the Mac.
- **The integrator owns the test factory.**
- Both start in Cycle 13 or 14.
- YouTube TV DVR and screen capture are out (DRM).

**Binding:**
- grading.md applies: pre-register every run, the integrator freezes the graders, and locked is read once per
  release.
- The self-taught rule applies: Nick's taps are evaluation only.
- Each source's terms apply. No scraping where the terms forbid it, and no paid feeds without Nick.
- Keys are added by Nick as Kaggle secrets.

## 1. The context and hype data engine (lane ①)

**Goal:** for every game and team in NFL, CFB, NBA, CBB, NHL and MLB, numbers that say how much the game
matters and how people feel about it. The output feeds Watch Value and game importance:
- `pregame_importance`: one value per game, plus its parts;
- live buzz features: per game, updated during the night.

**The betting rule (Nick, 11:10 PM).**
- Betting lines and line moves become **inputs** to importance: the market's expectation and the odds of an
  upset.
- They are never shown on the TV or the phone, never a betting prompt, and never used to nudge betting.
- This widens the Nov 4 "hidden check only" rule. It is logged in decisions.md, the catalog note is updated,
  and EXP-077 and EXP-078 are rewritten to match.

**How it runs:**
- A Kaggle or Colab CPU job, scheduled, resumable and rate-limited per source, with a User-Agent on every
  request.
- It writes a private Kaggle dataset `context-features`, with one parquet file per day.
- The repo gets only the code, the schema and the source licenses. No raw rows go in the public repo.

### The LLM reader (previews, recaps, fan analysis)
- It reads a page live and extracts **numbers only**:
  - `hype` 0-1;
  - storyline tags from a fixed list (rivalry, revenge, streak, milestone, debut, playoff race, upset watch,
    injury return);
  - key players.
- It stores the derived numbers, the source URL, the fetch time and the model id. **It never stores the
  article text.**
- **Allowlist first.**
  - A domain is read only after its terms and robots.txt are checked, and the result is written as its license
    line in docs/datasources.
  - Skip paywalls, and skip sites whose terms forbid scraping or AI use.
  - RSS and official feeds come first.
- **Discovery:** article URLs come from GDELT. Only allowlisted domains are fetched.
- **The model:** the Teacher or a small open model on the same Colab or Kaggle job. Never on the Mac.

### First sources (wave C1: all are already in the sources catalog with a verdict)
| # | source | signal | verdict in the catalog | status |
|---|---|---|---|---|
| 1 | nflverse schedules (spread_line, total_line, moneylines), injuries, rosters | NFL market expectation, upset odds, injuries | Yellow: CC-BY-4.0, credit nflverse | start now |
| 2 | Wikimedia pageviews (REST for live game nights, dumps for history) | per-team and per-player interest, spikes | Green (User-Agent, few parallel calls) | start now |
| 3 | Wikipedia EventStreams + revision metadata (no text, no usernames) | live edit velocity on team and player pages | Green | start now |
| 4 | GDELT 2.0 GKG + Events/Mentions (15-min CSVs) | news volume and tone per team and game | Yellow: free, cite GDELT | start now |
| 5 | Wikidata sports subgraph | rivalries, records in reach, geography, ids | Green: CC0 | start now |
| 6 | Our own standings and playoff-odds Monte Carlo (from green results) | stakes: playoff odds, seeding, records in reach | Yellow (derived; inputs must be green or yellow) | start now |
| 7 | The Odds API, free tier, forward only (~1 snapshot a day) | lines + moves for all six leagues | Yellow: **needs Nick's free key as a Kaggle secret** | waiting on Nick |
| 8 | CFBD `/lines`, `/media` (national TV slots) | CFB lines, TV slots | Yellow: **needs Nick's CFBD key** | waiting on Nick |
| 9 | SBRO archive via Wayback (2007-2022) | historical lines for training | Yellow, Nick-approved in the catalog | backfill |
| 10 | Google Trends, official alpha API only | DC and NC regional interest | Yellow: **Nick applies; never pytrends** | waiting on Nick |
| 11 | National-TV slots | from league schedule feeds already in the catalog | per source | start now (NFL) |
| 12 | Allowlisted RSS (league and team official feeds first) through the LLM reader | hype 0-1, storyline tags, key players | each domain gets its own license line | allowlist PR first |

**Data B (Nick, 11:20 PM) applies here too.** ESPN-derived and other restricted public sources may feed
the engine for personal use, tagged "personal-use, not for commercial release". The guardrails still hold:
public endpoints only; no logins, paywalls, CAPTCHAs or bot-block bypasses; respect rate limits and robots.

**Lane ① re-checks the catalog's red rows under B.** A row comes back only if it passes the guardrails. These stay
out until that check says otherwise:
- sportsbook and line-movement sites (bot blocking, and terms that forbid automation);
- Reddit (API approval and login) and Pushshift dumps;
- pytrends (Google blocks it).

The ESPN odds and predictor feeds are public endpoints, so they may come back under B.

These are listed with reasons in the catalog's "Not using" table.

### Experiments (pre-registered in docs/experiments.md)
- **EXP-090:** the engine's skeleton and schema, with sources 1-6 flowing daily.
- **EXP-091:** `pregame_importance` as a WV input, behind a flag.
- **EXP-092:** live buzz features as a WV input, behind a flag.
- **EXP-093:** the LLM reader on the allowlist.

## 2. The test factory (integrator)

The integrator's plan is [test factory](2026-11-05-test-factory.md), F1-F7:

| Item | What | When |
|---|---|---|
| F4 | two-harness grading (honest + `--resync`) | Thu morning |
| F1/F2 | the golden nightmare suite and its ratchet (grader freeze v12) | Thu |
| F3 | the edge-case set | Thu-Fri |
| F5 | off-policy grading of every live night | Fri |
| F7 | the brain-change PR hook | Fri |
| F6 | the experiment writer | Sat |

The engine 1 features are graded only through their effect on the brain (EXP-091/092 lift), never on their own.

## Gates and risks
- **Overfitting.** Context features carry dates, so every split is by whole night. A feature is computed only
  from data timestamped before the decision (no look-ahead). G-checks add a leakage test for the
  `context-features` timestamps.
- **Hype is not the objective.** The objective stays "worth it". Hype is an input, scored only by its lift
  over the context-only baseline on dev and locked.
- **Terms drift.** Each source's license line is rechecked when a source fails. A domain that changes its
  terms comes off the allowlist.
