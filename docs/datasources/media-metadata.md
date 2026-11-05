# Data Division C: media metadata (which plays got clipped, and how much people watched)

Planner C, from Nick's 12:05 AM Nov 5 reorg. Experiment ids EXP-1200..1299. **No video, audio or image is
ever downloaded**: only public JSON metadata (clip ids, tags, headlines, publish times, durations, views).
Workers run on free GitHub runners via `.github/workflows/media-factory.yml` (push `media-factory/<src>`),
never on the Mac. Every row carries `tag: "personal-use, not for commercial release"` and its licence line.

## Why it matters

An editor clipping a play, and how fast people watch it, is a human-made **importance label** per play that
the pbp feeds don't carry. It is a small token count but high new information per GB. Feeds the brain's
importance / worth-it head as a hindsight label, never as a live input it can't have at decision time.

## Crew and sources

| Worker | Source | What we keep | Link to play | Verdict + licence line | Key | Status |
|---|---|---|---|---|---|---|
| C1 | MLB Stats API `/game/{pk}/content` | highlight id, headline keywords, taxonomy + player/team tags, publish time, duration | `guid` / play id, gamePk | yellow-B: MLBAM notice, non-commercial individual use; plan B personal use | none | backfill 2023-2025 on `media-factory/mlb` |
| C2 | NHL api-web `/gamecenter/{id}/landing` + `/right-rail` | per-goal highlight clip id, strength, shot type, period/time; recap + condensed game ids | `eventId` | yellow-B: NHL.com terms restrict scraping; plan B personal use | none | backfill 2023-24 to now on `media-factory/nhl` |
| C3 | NBA `playbyplayv3` (`videoAvailable`) | which events the NBA clipped | `actionNumber` | yellow-B: NBA.com terms, personal use. Honest UA only; a block or silence stops it (no header spoofing) | none | probe on `media-factory/nba` |
| C4 | YouTube Data API v3, official channels (@NFL @NBA @NHL @MLB @MarchMadness @ESPNCFB) | title keywords, upload time, duration, views/likes/comments per snapshot, view velocity, chapters, daily Top-10 | title keywords -> game (lane 1 joins) | yellow-B: API Services ToS + Developer Policies; stored stats refreshed or deleted within 30 days; personal use | **Nick: `YOUTUBE_API_KEY` Actions secret** | waiting on key |
| C5 | Video understanding on public links | report only | n/a | see below | n/a | done (report) |

Quota: YouTube snapshot of 6 channels x 1,000 videos = ~130 units (10k/day free). MLB/NHL at 1 request/s.

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

Proposed (not started): EXP-1203 free-tier Gemini pilot on <=50 official YouTube highlight links/day, output
numbers only (play type, players, crowd-noise 0-3), only if Nick says yes.
