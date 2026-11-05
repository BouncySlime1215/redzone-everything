# Data Division C: media metadata (which plays got clipped, and how much people watched)

Planner C, from Nick's 12:05 AM Nov 5 reorg. Experiment ids EXP-1200..1299. **No video, audio or image is
ever downloaded**: only public JSON metadata (clip ids, tags, headlines, publish times, durations, views).
Workers run on free GitHub runners via `.github/workflows/media-factory.yml` (push `media-factory/<src>`),
never on the Mac. Every row carries `tag: "personal-use, not for commercial release"` and its licence line.

Standing principle (Nick 12:15 AM): extract features on the fly (numbers, timestamps), never store raw video.

## Why it matters

An editor clipping a play, and how fast people watch it, is a human-made **importance label** per play that
the pbp feeds don't carry. It is a small token count but high new information per GB. Feeds the brain's
importance / worth-it head as a hindsight label, never as a live input it can't have at decision time.

## Priority (Nick 2:00 AM Nov 5): NBA first, then NFL

Queue re-ranked by NBA impact, then NFL. MLB and NHL backfills already running finish (shared data); no new
MLB/NHL/CBB work starts.

1. **C7 ESPN game-summary videos, NBA + NFL** (new, no key, B): clips ESPN attached to each game, 2024-25 and
   2025-26 to now. Runs on `media-factory/espn`.
2. **C4 YouTube @NBA then @NFL** (needs Nick's key): view velocity + daily Top-10.
3. **C3 NBA direct**: blocked. cdn.nba.com answered 403 to our honest client on the first call (Nov 5 04:31
   UTC run); stopped, no bypass. NBA coverage comes from C7 + C4 instead.
4. C1 MLB, C2 NHL: finishing the running backfills only.

Honest scope: media metadata is a hindsight "this play mattered" label for big-plays-seen/live and the
importance head. It does not fix NBA mid-commercial entries or dead air; those are lane 2 timing work.

## Crew and sources

| Worker | Source | What we keep | Link to play | Verdict + licence line | Key | Status |
|---|---|---|---|---|---|---|
| C1 | MLB Stats API `/game/{pk}/content` | highlight id, headline keywords, taxonomy + player/team tags, publish time, duration | `guid` / play id, gamePk | yellow-B: MLBAM notice, non-commercial individual use; plan B personal use | none | backfill 2023-2025 on `media-factory/mlb` |
| C2 | NHL api-web `/gamecenter/{id}/landing` + `/right-rail` | per-goal highlight clip id, strength, shot type, period/time; recap + condensed game ids | `eventId` | yellow-B: NHL.com terms restrict scraping; plan B personal use | none | backfill 2023-24 to now on `media-factory/nhl` |
| C3 | NBA `playbyplayv3` (`videoAvailable`) | which events the NBA clipped | `actionNumber` | yellow-B: NBA.com terms, personal use. Honest UA only; a block or silence stops it (no header spoofing) | none | **blocked**: cdn.nba.com 403 on first call, stopped |
| C4 | YouTube Data API v3, official channels (@NFL @NBA @NHL @MLB @MarchMadness @ESPNCFB) | title keywords, upload time, duration, views/likes/comments per snapshot, view velocity, chapters, daily Top-10 | title keywords -> game (lane 1 joins) | yellow-B: API Services ToS + Developer Policies; stored stats refreshed or deleted within 30 days; personal use | **Nick: `YOUTUBE_API_KEY` Actions secret** | waiting on key |
| C6 | YouTube search `videoLicense=creativeCommon` (sports) | PARKED, low priority (Nick 12:15 AM): only if a use emerges, e.g. teaching a vision model what a dead ball looks like. Features extracted on the fly, raw video never stored | n/a | CC BY per video (attribution) | same key | parked |
| C7 | ESPN site API game summary `videos` (NBA, NFL; CBB/CFB optional) | clip id, headline keywords, duration, publish time, athlete ids, linked play id where present | play id when ESPN tags it | yellow-B: ESPN public API, personal non-commercial use; plan B | none | backfill 2024-25 + 2025-26 on `media-factory/espn` |
| C5 | Video understanding on public links | report only | n/a | see below | n/a | done (report) |

Quota: YouTube snapshot of 6 channels x 1,000 videos = ~130 units (10k/day free). MLB/NHL at 1 request/s.

## Measured (first backfills, Nov 5 2026)

| Source | Window | Clips | Linked to a play | New tokens | Derived tokens | Stored | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| C1 MLB | 2023-2025 seasons | 157,247 | 88,431 (56%) | 629k | 314k | 12.1 MB | unlinked are mostly features/interviews ("other" 34k), condensed 7.4k, recaps 6.3k, plus 8.8k HR clips without a play guid |
| C2 NHL | 2023-10-10 to 2026-04-11 | 30,831 | goals 21,945 of 21,945 (100%) | 123k | 62k | 0.7 MB | + 5.6k recaps, 3.3k condensed games. Run was cancelled at 07:04 UTC mid-window (not the timeout); torn gzip tails repaired with 0 rows lost; resuming 2026-04-11 to now |
| C3 NBA direct | n/a | 0 | n/a | 0 | 0 | 0 | blocked: 403 |

0 duplicate clip ids; licence line + personal-use tag on 100% of rows. Total so far: ~0.75M new-information
tokens, ~0.38M derived, 12.8 MB. Workers now close their gzip files after every game, so a killed run keeps
every finished game.

## Tokens (lane 1 maps to vocab v2 ids)

One row per clip, `tokens` = field:bucket strings. **New-information** fields: `clipped`, `kind`, `dur`,
`pub_lag`, `views`, `vel`, `rank`, `chap`. **Derived** fields: `lg`, `linked`. `scripts/media_pull.py ledger`
counts them apart in `ledger.json` on each `media-out/<src>` branch. Title text never reaches the tokenizer:
keywords only, for lane 1 to map to player/team ids.

Leakage rule: views, velocity and rank are known only **after** the play. They are hindsight labels or
next-day context ("yesterday's most-watched team"), never a feature at the play's own instant.

Estimate (planner, not yet measured): MLB ~25 clips/game x 2,430 x 3 seasons = ~180k clips; NHL ~6/game
plus recaps x ~3,300 games = ~25k; YouTube ~6k videos + daily snapshots. ~1.5-2M new tokens from backfill,
then ~5-10k new tokens/day. Measured numbers replace this after the first run.

## Video understanding on public links (C5, report only, no spend)

- **Gemini API, YouTube URL input**: Google fetches the public video; we download nothing. Preview feature,
  free tier ~8 h of YouTube video/day (~480 one-minute clips). Free-tier inputs may be used by Google and
  seen by human reviewers. Paid Flash-Lite ~$0.25/1M video tokens: 1,000 clips/day x 60 s = **~$2-5/day
  (~$60-150/month)**. Verdict **yellow**: the best fit. Free tier is usable for a small pilot; paid needs
  Nick's OK. Sources: ai.google.dev/gemini-api/docs/video-understanding, /pricing, aistudio terms.
- Google Video Intelligence, AWS Rekognition: need the file uploaded (= we download). **Red.**
- Twelve Labs: indexes and stores the video; URL input unverified. **Yellow-red.**

Proposed (not started): EXP-1204 free-tier Gemini pilot on <=50 official YouTube highlight links/day, output
numbers only (play type, players, crowd-noise 0-3), only if Nick says yes.
