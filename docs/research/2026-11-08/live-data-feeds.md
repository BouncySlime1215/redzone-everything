# Lowest-latency free live feeds per league (research, 2026-11-08)

Scope: free play-by-play and game-state feeds, how late each is versus the action and the broadcast, how to read
"the ball is dead" out of each, and how to measure feed lag. No code was changed. Everything marked **[probe]** was
fetched by me on Sun 2026-11-08 ~15:47 ET (a handful of single GETs, no loops); **[doc]** = read on the page cited in
section 4; **[local]** = the project's own measurements. Anything I could not open or test is marked **UNVERIFIED**.

## 0. Findings in five lines

1. The best free feed per league is **not** ESPN polling. ESPN play-by-play reaches the app 35-50 s after the whistle
   [local, docs/timing.md]. The NBA liveData mirror and NHL api-web are much faster (see table); ESPN fastcast is the only
   push channel and is the main route for NFL/CFB, where no free league feed exists.
2. There is **no free official NFL live feed**. The old nfl.com `liveupdate/scores.json` URLs reportedly 404, and the NFL
   sells real-time play data through one exclusive partner [doc: sportsapis.dev, secondary source, 2026-11-02].
3. **No feed carries a "commercial break" flag.** Breaks must be inferred: official/TV timeout events (NFL, NBA, NHL
   `tv-timeout`), period-end events, MLB `inningState` = Middle/End, plus clock-stopped state, plus stick-side OCR.
4. Only two feeds give a real event wall clock to measure lag against: NBA `timeActual` and MLB `playEvents[].endTime`.
   ESPN has `wallclock` (set at ESPN's end, sometimes the moment play resumed). NHL api-web has none.
5. ToS: every league site forbids automated scraping or limits data to personal, non-commercial use. This stays a
   personal-use project; keep polling polite and cached (see section 3 risk R2).

## 1. Per-league table

Lag = time from the real event until the feed shows it. Broadcast reference: over-the-air is ~19 s behind the action,
cable ~38 s, streaming 48-62 s (Stats Perform, Super Bowl, Feb 2026 [doc: TV Technology]; the article's own title says
LX while its text says LIX, so treat the event label as unclear). Nick's YouTube TV channels measured 22-42 s
[local, docs/timing.md]. A feed "leads" the screen when its lag is below the channel's delay.

| League | Best free source (URL stem) | Typical lag (source) | Stoppage signals in that feed | Reliability | ToS notes |
|---|---|---|---|---|---|
| **NFL** | ESPN fastcast push `gp-football-nfl-<id>` (fallback ESPN `summary?event=`) | ESPN polled: median ~45 s after the whistle [local]. Fastcast: 0.01-0.21 s behind ESPN's publish on WCBB; NFL/NHL/MLB documents not yet verified [local, fastcast.py docstring]. Median lead over polling 19 s [task brief]. | ESPN play `type.text`: "Official Timeout", "Timeout", "Two-minute warning", "End Period", "End of Half", "Penalty", "Kickoff", "Punt" **[probe]** (all present in a live LAR@WSH summary today). `scoringPlay` flag, text with "challenged/review". | Medium: undocumented, can change without notice. Summary is CDN-cached max-age 2 s, scoreboard max-age 9 s **[probe]**. | ESPN/Disney terms prohibit robots, scripts and automated extraction **[doc]**. No official free alternative. |
| **CFB** | Same as NFL (ESPN `football/college-football`) | Same as NFL; no league feed exists. | Same ESPN play types; college logs halftime as "End Period" in period 2 (already handled in `espn.stoppage_for`). | Medium. | As ESPN. NCAA's own hosted `ncaa-api` (henrygd) offers scoreboard and box score, 3 req/s per IP, no SLA, advises self-hosting **[doc]**; it scrapes NCAA.com so inherits that site's terms (not checked). |
| **NBA** | NBA liveData play-by-play via the S3 mirror `nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/playbyplay/playbyplay_<gameId>.json` (cdn.nba.com itself returns 403 to curl) | One live sample **[probe]**: a `timeout` action had `timeActual` 20:46:58.05Z and `edited` 20:47:05.14Z, i.e. published ~7 s after it happened; the file's `Last-Modified` was 20:47:15Z. Project fixture said 4-12 s median [local, decisions.md Nov 4]. ESPN NBA: 38 s median [local]. | `actionType` = `timeout` (+`subType` full/…), `period` start/end, `jumpball`, `freethrow` ("1 of 2"), `instantreplay`, fouls/violations **[probe][doc]**. `clock` as ISO duration and `timeActual` (wall clock) on every action. `orderNumber`, `edited` allow dedupe and corrections. | Good: S3 supports `ETag`/`Last-Modified` **[probe]**. Schema doc last validated 2020-08 [doc], but live fields matched in Nov 2026 [probe]. | NBA.com terms s.9: stats for "private, non-commercial purposes" with credit to NBA.com; no gambling or fantasy use; no comprehensive database product [doc]. Terms have no explicit scraping clause [doc]. The S3 bucket host is undocumented in anything I found. |
| **NCAAB** | ESPN (`basketball/mens-college-basketball`), fastcast verified on WCBB | ESPN summary pushed within ~0.2 s of ESPN's publish; even 1 s polling saw plays ~3.7 s after push [local, fastcast.py]. CBB polled ~35 s [local]. | ESPN type text (`Official TV Timeout`, `MediaTimeOut`, `End Period`, free-throw "1 of 2" in text). Men's play halves, handled in `espn.stoppage_for`. | Medium. | As ESPN. |
| **NHL** | `api-web.nhle.com/v1/gamecenter/<id>/play-by-play` and `/v1/score/now` | Response header **[probe]**: `cache-control: no-transform, max-age=19, s-maxage=19`, `cf-cache-status: HIT`, so the CDN can serve a copy up to ~19 s old. Nick's live check: median 16.7 s vs ESPN 43.1 s [local, docs/timing.md]. | Per play: `typeDescKey` = `stoppage` with `details.reason` (`offside`, `high-stick`, `goalie-stopped-after-sog`, `tv-timeout` is already coded in `leagues.enrich_nhl`) **[probe]**; `faceoff` marks restart; top-level `clock.running` and `clock.inIntermission` booleans **[probe]**; `situationCode` for strength. No wall clock per play. | Good but cached (see lag). Undocumented; community reference only [doc: Zmalski]. | NHL ToS s.2 prohibits "unauthorized spidering, scraping, or harvesting", and s.7 limits use to "non-commercial, informational, personal" **[doc]**. data-sources.md already keeps api-web OFF for bulk harvest; live single-game polling is a different use but still under that clause. |
| **MLB** | `statsapi.mlb.com/api/v1.1/game/<pk>/feed/live` (GUMBO) and `.../feed/live/diffPatch` | No published latency. MLB calls GUMBO "updated after every pitch and every play" with an internal cross-region SLA under 2 s [doc: Google Cloud, Aug 2025]; that is back-end replication, not what the public endpoint shows. Schedule endpoint was `max-age=20` **[probe]**. Not measurable today: MLB is in the offseason, so no live GUMBO probe. Gameday notifications run ahead of the stream; MLB added a 30 s delay option in 2024 [doc: Six Colors]. | `playEvents[]` (each pitch, `isPitch`, `endTime`), `linescore.inningState` (Top/Middle/Bottom/End) and `isTopInning`, `currentPlay.result`, pitching-change/substitution events; between pitches is always "dead" but only the pitch-count rhythm tells you which. Already read in `leagues.enrich_mlb` (`inningState`, `playEvents`). | Good historically; UNVERIFIED for 2026 because I could not hit a live game. | MLB's site terms ban "automated scripts" [doc, mlb.com/tou]. The data copyright notice (personal, non-commercial, non-bulk) I only saw quoted by third parties; I did not open gdx.mlb.com, so treat as UNVERIFIED. data-sources.md already notes MLB terms restrict automated access. |

### Free-tier commercial feeds (none beats the league feeds above)

| Provider | What the free tier offers | Verdict |
|---|---|---|
| Sportradar trial | Docs: NFL/NBA/NHL/MLB PBP TTL 3 s live; push feed streams "ahead of the television picture"; filters `timeout`, `two_minute_warning`; resume window 10 min [doc]. I could not read the trial terms (the page does not state key limits). | Best event model (explicit timeout events). Trial only, production use not confirmed: UNVERIFIED. $0 budget means trial-only research use. |
| SportsDataIO | Claims updates "within 20-30 seconds of the cable & OTA broadcast", real-time sync every 3-5 s on paid plans [doc]. | Slower than free league feeds; skip. |
| BALLDONTLIE | Free: 5 requests/min, one league, "Basic" data; live PBP not stated for free [doc]. | Too slow and limited. |
| MySportsFeeds | "Extremely affordable or even FREE" for strictly personal use, 14-day trial; live PBP delay and rate limits not stated [doc]. | Worth a 14-day trial for NFL only if ESPN fastcast fails the NFL check. UNVERIFIED delay. |
| nflverse | Data updated by GitHub Actions, files updated the day after games [doc: sportsapis.dev, nflreadr page]; no live PBP. | Training data only (already in use), not live. |

## 2. Detecting dead balls and commercial breaks per feed

A feed shows an event; the app needs the *state* "ball is dead and will stay dead long enough". Per league:

- **NFL / CFB (ESPN).** Dead ball = last play type is Penalty, Official Timeout, Timeout, Two-minute warning, End Period,
  End of Half, scoring play (extra point and kickoff follow) or Punt/FG result. Commercial break = Official Timeout (media),
  two-minute warning, end of quarter, score break; there is no flag, so use the project's break-length priors. Already done in
  `espn.stoppage_for`. New idea: in fastcast documents the `situation` and `drives.current` change the moment a snap is
  logged, so "no new play for N s after a play with yards gained" is a huddle estimate; verify on NFL first (the fastcast
  docstring lists NFL as not yet verified).
- **NBA (liveData).** `timeout` action, `period` `end`, free throw not last ("n of m") = dead; last free throw or made basket =
  restarts soon. `timeActual` of the *next* action after a timeout gives the real break length retrospectively (feeds the
  break-length priors). Caveat from the project: in ESPN's NBA feed a timeout's wallclock is often the resume time
  [local, docs/timing.md]; liveData's `timeActual` is the real event time, so it is the better source for this.
- **NHL (api-web).** `stoppage` event with `details.reason`, then the next `faceoff` = restart. `clock.running == false`
  appears before the CDN may deliver the stoppage play, so it is the earliest api-web whistle hint (already in
  `enrich_nhl`). `reason == tv-timeout` = commercial break; `inIntermission` = long break.
- **MLB (statsapi).** Between pitches = dead; the break is real at `linescore.inningState` in {Middle, End} (commercial
  break, ~2:00-2:30 local and national slots; length priors are project data), pitching change events, and mound visits /
  reviews in `playEvents`. The `isTopInning` flip plus 'Middle' is the earliest signal. UNVERIFIED in-season for 2026.
- **Cross-league detector (any feed).** Clock-stall change point: game clock not advancing for >2-3 s while period state
  is live (NBA/NHL/NFL clocks stop at whistles). Already described in docs/research/2026-11-03/03-switch-timing.md.
- **Commercial state itself.** No free feed has it. Only stick-side evidence (OCR of score bug disappearing, ad detection)
  or the break-length model can say "ads are airing". The Sportradar NHL feed exposes `tvtimeout` in event descriptions
  [doc, search result only; not opened], confirming that the dead-ball class is league-reported but the ad itself is not.

### Measuring feed lag (methods ranked by cost)

1. **Event wall clock vs first-seen.** For NBA `timeActual` and MLB `endTime` (already logged by `fastfeeds.py` as `real`
   and `seen`): lag = seen - real. The one NBA sample today gave publish lag ~7 s before our network hop [probe].
2. **Race between sources.** `redzone/sources/merge.py` already logs which source reports each score change first;
   `scripts/fast_feed_race.py` and `scripts/fastcast_check.py` line up ESPN vs league feeds vs fastcast per play.
3. **Header-based staleness.** Use `ETag`/`Last-Modified` on S3 (NBA) and `Age` on api-web (`age: 9` of `max-age 19` seen
   [probe]); the CDN age is a free upper bound on how stale a cached copy is.
4. **Screen-side.** OCR of game clock on Prime/YouTube TV frames vs feed clock yields a delay sample every ~2 s while the
   clock runs [local, 03-switch-timing.md]. This measures feed-to-screen, which is what Rule #1 needs.
5. **Community measurements.** The only public figure I found for a data feed vs a TV signal is a betting-site feed at least
   4 s ahead of a Freeview soccer broadcast, polled every 3 s, 2018 [doc: benjojo]. Northwestern's study found a direct feed
   systematically faster than a cable broadcast but I could not open that paper (search snippet only). I found **no** public
   measurement of api-web, liveData or GUMBO latency; the project's own numbers are the primary evidence.

## 3. Recommendations mapped to RedZone code

| # | Recommendation | Where | Why / how to check (North Star line moved) |
|---|---|---|---|
| 1 | Make the NBA liveData mirror the fast NBA stoppage source, using `timeActual` and `edited` to log true publish lag per game. Today `sources.nba_primary` ships OFF with a shadow (`polling.nba_pbp_s: 3`). The 7 s publish lag I saw agrees with the Nov 4 fixture (4-12 s) vs ESPN 38 s. Promote per the 95% gate. | `redzone/sources/leagues.py` (`NBA_PBP`, `nba_plays`, `nba_stoppage`, `enrich_nba`), `redzone/sources/fastfeeds.py` (`nba_rows`) | Raises lead; cuts "dead air" and "plays missed on entry". Gate on `scripts/fast_feed_race.py`. |
| 2 | Treat NHL api-web as 17-19 s feed with a hard CDN floor (`s-maxage=19`): polling faster than ~5 s only wastes requests. Prefer `clock.running` and the `stoppage` reason; keep ETag/If-Modified-Since. | `redzone/sources/leagues.py` (`enrich_nhl`, `nhl_plays`), `redzone/sources/fastfeeds.py` (`nhl_rows`, `nhl_s` 2 s) | Reduces request load without adding lag. Consider `nhl_s` 5 s. Verify with `docs/timing.md` NHL row. |
| 3 | Finish the fastcast NFL/CFB/NHL/MLB verification: the module says only WCBB/NBA were verified. NFL is where nothing else is free and ESPN polled is ~45 s. Record one NFL window (games were live today) and run the check. | `redzone/sources/fastcast.py` (`--record`, `check`), `redzone/sources/fastcast_check.py`, `scripts/fastcast_check.py` | Gate `sources.fastcast` on `pass`; if NFL `usable()` fails keep polling. |
| 4 | Add a protocol-drift tripwire. The fastcast handshake in my probe still returned a host document (`websockethost` HTTP 200 **[probe]**), but the 2020 gist's subscribe topic and the commenter's note that compression handling changes show the wire format has moved before [doc: gist]. Log unknown `op` values and non-200 `rc` to health. | `redzone/sources/fastcast.py` (`parse_message`, `FastcastClient`), `redzone/sources/feeds.py` (`FeedBook` staleness) | Silent failure costs only head start, but surfacing it speeds repair. |
| 5 | Break-length from `timeActual` deltas: for NBA, compute timeout-to-next-action wall time from liveData and use as ground truth instead of ESPN wallclock. | `redzone/sources/leagues.py` (`nba_plays`), `scripts/measure_break_lengths.py` | Improves break priors (EXP-727 mid-commercial NBA entries). |
| 6 | MLB: when the season returns, run `fastfeeds.mlb_rows` in shadow first and measure `endTime` vs seen; add `diffPatch` (startTimecode/endTimecode params [doc: MLB-StatsAPI wiki]) only if full `feed/live` payloads cost bandwidth. MLB is last in priority and out of season; no work now. | `redzone/sources/fastfeeds.py` (`mlb_rows`), `redzone/sources/leagues.py` (`enrich_mlb`) | Deferred. |
| 7 | Keep Sportradar push (trial) as a research benchmark only if Nick wants a cheap truth source for explicit `timeout` / `two_minute_warning` events. Needs key handling by Nick; I did not touch secrets. | n/a (new, would sit beside `sources/feeds.py`) | Optional; $0 rule applies. |

## 4. Risks

- **R1 Undocumented endpoints.** ESPN site API and fastcast, NHL api-web, the NBA S3 mirror and MLB GUMBO are all unofficial
  for third parties. Field names changed before (nba_api doc "last validated 2020-08" [doc]); mitigation is fixtures in tests
  and the `usable()` check in `fastcast.py`.
- **R2 Terms of service.** ESPN/Disney: automated extraction prohibited [doc]. NHL: no spidering/scraping, personal use only
  [doc]. MLB: automated scripts prohibited on its digital properties [doc]. NBA: personal non-commercial use, no database
  product [doc]. Personal use at low volume is the realistic posture; note that data-sources.md already turns NHL bulk pulls OFF.
  Never redistribute, never publish raw rows.
- **R3 Block or rate-limit.** cdn.nba.com returns 403 to curl (the S3 mirror does not) **[probe]**. S3 and CDN behaviour can
  change; the shared `Fetcher` in `redzone/sources/http.py` already backs off per host.
- **R4 Cache masks freshness.** NHL `s-maxage=19`, ESPN scoreboard `max-age=9` and summary `max-age=2` mean a fast poll can
  return a stale body without error [probe]; staleness logic in `feeds.py` should use `Age`/`Last-Modified`.
- **R5 Lead only exists if lag < stream delay.** Feed-to-screen lead = stream delay - feed lag; at YouTube TV 22-26 s a
  45 s ESPN feed is already behind the screen [local]. Per-feed lag matters more than any other feed property.
- **R6 Secondary evidence.** The "NFL feed is gone / exclusive partner" claim comes from one blog (sportsapis.dev, tested
  2026-11-02) and is not confirmed by an NFL statement. Do not build on the claim until I or Nick tests those URLs directly.
- **R7 Unverified in-season.** MLB statsapi latency, NFL fastcast and the Sportradar trial terms are UNVERIFIED. They need one
  live game each (NFL Sunday, MLB April) before any gate.
- **R8 Single-sample numbers.** The 7 s NBA publish lag and 19 s NHL cache are one sample and one header read; the
  project's multi-night recordings should override them.

## 5. Bibliography (opened or fetched; date shown is the page's own date if given)

1. ESPN fastcast gist, m4rk4, created 2020-10-30, comments to 2025-02 (older source). https://gist.github.com/m4rk4/706745226f5a89cb1833dbb4507605e0
2. Stats Perform Super Bowl latency, TV Technology, 2026-02-09. https://www.tvtechnology.com/platform/study-ota-broadcasts-offered-lowest-latency-of-super-bowl-lx-feeds
3. "Beating the broadcast delay", benjojo, 2018-07-10 (older). https://blog.benjojo.co.uk/post/beating-the-broadcast-delay-world-cup
4. SportsDataIO real-time data refresh guidance. https://sportsdata.io/developers/content-update-frequency
5. Sportradar NFL push feeds guide. https://developer.sportradar.com/football/docs/nfl-ig-push
6. Sportradar NBA update frequencies (3 s TTL live). https://developer.sportradar.com/basketball/docs/update-frequencies
7. Sportradar MLB update frequencies (3 s TTL live). https://developer.sportradar.com/baseball/docs/mlb-ig-update-frequencies
8. Sportradar MLB monitoring data changes. https://developer.sportradar.com/baseball/docs/mlb-ig-monitoring-data-changes
9. Google Cloud, MLB real-time GUMBO, Rahul Joshi, 2025-08-04. https://cloud.google.com/blog/products/databases/mlb-delivers-real-time-data-to-fans/
10. Zmalski unofficial NHL API reference (play-by-play endpoint). https://github.com/Zmalski/NHL-API-Reference
11. nba_api live playbyplay docs, last validated 2020-08 (older). https://github.com/swar/nba_api/blob/master/docs/nba_api/live/endpoints/playbyplay.md
12. "Accessing live NBA play-by-play, real timestamps", The F5, 2021-07-16 (older). https://thef5.substack.com/p/how-to-pbp2
13. MLB-StatsAPI wiki, endpoints (game feed, diffPatch params). https://github.com/toddrob99/MLB-StatsAPI/wiki/Endpoints
14. sportsapis.dev NFL API page, tested 2026-11-02 (secondary source). https://sportsapis.dev/nfl-api
15. henrygd/ncaa-api README (3 req/s, MIT, self-host advice). https://github.com/henrygd/ncaa-api
16. NBA.com Terms of Use (sections 1 and 9). https://www.nba.com/terms
17. NHL.com Terms of Service (sections 2 and 7). https://www.nhl.com/info/terms-of-service
18. MLB Terms of Use (automated scripts clause, fetched with curl, HTTP 200). https://www.mlb.com/tou
19. Disney Terms of Use, English (robots/scripts clause, fetched with curl). https://disneytermsofuse.com/english/
20. Six Colors, MLB app delayed notifications, 2024-03-20. https://sixcolors.com/post/2024/03/mlb-app-will-slow-itself-down-let-your-media-stream-catch-up/
21. BALLDONTLIE pricing (free 5 req/min). https://www.balldontlie.io/
22. MySportsFeeds data feeds page. https://www.mysportsfeeds.com/data-feeds/
23. Live endpoint probes by this research, 2026-11-08 ~15:47 ET: ESPN NFL scoreboard and summary (cache headers, play types), api-web `score/now` and play-by-play (cache headers, `clock.running`, stoppage reasons), NBA S3 scoreboard and play-by-play (timeActual/edited/Last-Modified), cdn.nba.com 403, MLB schedule (cache header), fastcast `websockethost` (HTTP 200).
24. Project docs read: docs/data-sources.md, docs/timing.md, docs/memory/decisions.md (Nov 4 1:15 AM), docs/research/2026-11-03/03-switch-timing.md, redzone/sources/{fastcast,fastfeeds,leagues,espn,merge,feeds,http}.py.

Not opened or not confirmed (do not cite as fact): Northwestern feed-vs-cable study (search snippet), gdx.mlb.com copyright text
(third-party quotes), Sportradar NHL `tvtimeout` classification (search snippet), ryanjmarcus/sportsfeed (404), Sportradar trial terms.
