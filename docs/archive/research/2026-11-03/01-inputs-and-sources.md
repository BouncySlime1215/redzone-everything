# 01 · Inputs and data sources for the RedZone models

Research angle: what the models should SEE, live and in training, to get better at (a) dead ball vs live
play and how long the stoppage lasts, (b) the restart moment (snap / inbound / faceoff / pitch), (c) big
moments before they happen, (d) hype / leverage (WP swing, crunch time, your teams, bets), (e) catch-up
clips. Six leagues: NFL, CFB, NBA, NCAA MBB, NHL, MLB. Budget $0, legal data only.

Written 2026-11-03 ~11:30 PM ET. Every endpoint below was checked against (1) the repo's own saved
responses in `tests/fixtures/` and `data/archive/` (field names are real, Nov 2026), and/or (2) a web
source cited in §6. "Measured" means a number the repo itself logged. Honest gaps are marked **UNVERIFIED**.

What the app already has (so this report builds on it, not beside it):
- ESPN `site.api.espn.com` scoreboard + summary, polled; plays carry `wallclock` in ALL six leagues
  (archive: NFL 6/6, NBA 494/494, NHL 316/316, MLB 548/548, CBB 546/546, WCBB 467/467 plays stamped).
- ESPN FastCast websocket, built and verified on WCBB, flag off (`sources.fastcast`). Measured: push
  0.01–0.21 s behind ESPN's publish time; 1-second polling of the summary saw a new play ~3.7 s later
  because the summary is CDN-cached up to 10 s (`redzone/sources/fastcast.py` header).
- League feeds partly wired in `redzone/sources/leagues.py`: NHL `api-web.nhle.com` (score, wsc pbp),
  NBA `cdn.nba.com` liveData via the S3 mirror (cdn.nba.com 403s scripts), MLB `statsapi.mlb.com`.
- Rules + measured break lengths in `redzone/scoring/breaks.py`; the data runs ~25–50 s ahead of the screen.
- Stick-side: YouTube TV media-session title flips at quarter hours (`screen/boundary.py`, measured
  22–26 s delay), Prime score-bug OCR every 2 s (`screen/scorebug.py`; YTTV screencaps are DRM-black).
- Training parquet from sportsdataverse releases (NBA 1.1 M rows / 2,649 games, 2 seasons; nfl, cfb, cbb,
  mlb files present) in `data/training/`.

---

## 1. Top 10 recommendations (ranked by impact ÷ effort)

| # | Do this | Why it moves the needle | Jobs | Effort |
|---|---|---|---|---|
| 1 | **Turn FastCast on for every league and log per-source lag** (`sources.fastcast: true`; `data/recordings/<day>/plays.jsonl` already logs each play once per source). Verify NFL/NHL/MLB topics (the docstring says only WCBB was verified: NFL `drives` `$key` refs and top-level `situation` are untested). | It is the ONLY free feed faster than the ESPN poll for NFL, CFB and college basketball. Measured 3.5 s ahead of 1-s polling and 5–15 s ahead of a 5–10 s poll; every second is switch lead time. | dead-ball onset, restart | ~1 h to enable + one game night to confirm per league |
| 2 | **NHL: poll `api-web.nhle.com/v1/gamecenter/{id}/landing` (or `/boxscore`) every 1–2 s for `clock.running` / `clock.inIntermission`, and read `plays[].typeDescKey == "stoppage"` + `details.reason` / `secondaryReason` from the pbp.** | An explicit live/dead flag from the league's own clock, plus the stoppage reason ESPN never gives (`icing`, `offside`, `puck-frozen`, `tv-timeout`, `goalie-stopped-after-sog`, `puck-in-netting/benches/crowd`). Faceoff restart = the moment `running` flips true. NHL was the worst league for mid-play entries (165 → 11 after the timing fix); this is how it gets to 0. | dead-ball, restart, length | ~3 h (fixtures exist: `tests/fixtures/nhl_api_gamecenter_*`) |
| 3 | **NBA: read `cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gameId}.json` (`actions[].timeActual`, ms-precision UTC; `actionType` timeout/`subType` full|challenge; `period` start/end) and `boxscore_{gameId}.json` (`gameClock`, `gameStatusText`) through the S3 mirror the repo already uses.** | `timeActual` is the TRUE wall time of every event, so (a) restart labels are exact (timeout → first clock-moving action), (b) you can measure ESPN's and your own lag per game, (c) free-throw sequences become clean entry windows. | restart, dead-ball, labels | ~4 h (fixture `nba_cdn_playbyplay_0022600157.json` has 646 actions) |
| 4 | **MLB: use `statsapi.mlb.com/api/v1.1/game/{pk}/feed/live` pitch-level `playEvents[].startTime/endTime`, `linescore.inningState` (Top/Bottom/**Middle/End** = between innings), `gameData.moundVisits`, `status.detailedState`, and `feed/live/diffPatch?startTimecode=` for cheap incremental polling.** | MLB's own feed is the origin of everyone else's data; each pitch and each action (`Mound Visit`, `Pitching Substitution`, `Batter Timeout`, `Game Advisory`, `stepoff`) is stamped. Between-innings restart = `startTime` of the next half's first pitch. | dead-ball, restart, length | ~4 h (fixture `mlb_api_feed_live_813024.json`) |
| 5 | **Build the historical label factory from free archives** (nflfastR 1999+ with `time_of_day` 2011+ and `play_clock`; hoopR NBA/MBB with `wallclock`; cfbfastR/CFBD `wallclock` 2014+; fastRhockey 2010+ with `reason`/`secondaryReason`/`xg` but **no wall time** → pair with the ESPN NHL summary `wallclock` the repo archives; MLB statsapi `startTime` back to the PITCHf/x era). Label every stoppage with true duration and the true restart instant. | Round 2 wants "millions of ESPN moments"; these sources add the TRUE durations and restart times that ESPN alone blurs (ESPN only stamps plays, not the moment the ball goes live). Depth: 10–25 seasons per league. | all five jobs (training) | 1–2 days inside Kaggle |
| 6 | **Add the broadcast-market feature** (national vs local): ESPN `broadcasts[].market` / `geoBroadcasts[].market.type` (already parsed into `national_tv`), NHL `tvBroadcasts[].market` (`N`/`H`/`A`), MLB `schedule?hydrate=broadcasts`. | Break length is RULE-driven by market: NBA mandatory timeouts 2:45 local vs 3:15 national, between-quarter breaks 2:30 local vs 3:30 national; MLB inning breaks 2:15 local, 2:40 national, 3:10 postseason. One feature, a big chunk of the length error. | length, restart | ~2 h |
| 7 | **Encode the exact break schedules per league** (details in §4): NBA mandatory marks + which team was charged + Q4 timeout caps; NHL 14/10/6 with all exceptions (goal, icing, PP, last 30 s P1/P2, last 2:00 P3, within 1 min of previous TV timeout, faceoff violation; none in OT); NCAA men 16/12/8/4 + "first team timeout of 2nd half becomes the media timeout" + 30-s window + conference experimental formats; NFL 4 breaks/quarter, mandatory at end Q1/Q3 and two-minute warnings, 40/25-s play clock; CFB 2024 two-minute timeout in Q2/Q4; MLB 15/18/30-s timers. | `breaks.py` has most marks but not the exceptions or the counting rules that decide whether the NEXT whistle is a commercial. These are deterministic lift. | dead-ball odds, length | ~3 h |
| 8 | **Live bet/leverage features from ESPN core**: `sports.core.api.espn.com/v2/sports/{sport}/leagues/{lg}/events/{id}/competitions/{id}/probabilities?limit=1000` gives per-play `homeWinPercentage`, `secondsLeft`, **`spreadCoverProbHome`, `totalOverProb`, `tiePercentage`**; `.../odds` and `.../odds/{providerId}/history/0/movement?limit=100` give line movement. | Nick's bets are a priority tier. A spread/total bet's live leverage is `spreadCoverProbHome`/`totalOverProb` swing, not WP. Free, already fixtured (`espn_core_nfl_probabilities_401772894.json`, 158 rows). | hype/leverage, bets | ~3 h |
| 9 | **Screen truth on the game you are watching**: keep Prime score-bug OCR (2 s) and add "bug gone ≥ 6 s = commercial on screen NOW" as both a live gate and a label source; run a 10-minute experiment on YouTube TV with `adb shell uiautomator dump` + `logcat` while a known ad break plays, looking for the "Ad" overlay/DAI markers Google inserts into some breaks. | The screen is the thing you cut; it is the only zero-data-latency signal. YTTV frames are DRM-black, but the app's own UI tree and logs are not video, so reading them is not circumvention. | dead-ball confirm, delay calibration | ~2 h + a 10-min TV test |
| 10 | **Clips beyond ESPN**: ESPN `summary.videos[]` already gives MP4 `links.source.mezzanine/HD/full` + HLS with a 48-h `expirationDate` and `settop` in the device whitelist; add NHL `landing.summary.scoring[].goals[].highlightClipSharingUrl` / `highlightClip` (every goal) and MLB `statsapi.mlb.com/api/v1/game/{pk}/content` highlights. Measure publish lag per league from `originalPublishDate` − play `wallclock`. | NHL goals and MLB plays are not in ESPN's clip list for those leagues (archive: NHL 4 clips, MLB 0). | catch-up reel | ~4 h; TOS gray (personal use) |

Lower-ranked but worth a line: CFBD **Academic tier is free with a .edu e-mail (3,000 calls/month vs 1,000)** —
Nick has `unc.edu`; `/plays` has `wallclock`, `/live/plays` is Patreon-only. Weather (Open-Meteo / NWS) and
injuries (ESPN `summary.injuries`, NBA PDF reports, nflverse) are cheap but low-lift for these five jobs.
YouTube TV's "Broadcast Delay → Decrease for 48 hours" cuts the stream delay ~10 s but expires after 48 h
and SHRINKS the data-ahead-of-screen lead the cut logic relies on; use it only if the model is re-tuned for it.

---

## 2. Tonight's question: what reveals a stoppage FASTER than the ESPN poll

The ESPN summary poll sees a play ~3.7 s after publish at 1-s polling (CDN cache ≤ 10 s), later at the
real poll cadence. Everything here is free.

| Rank | Signal | Faster by (estimate) | How you know it is faster | Covers |
|---|---|---|---|---|
| 1 | ESPN FastCast push (same document as the summary, JSON-patched) | 3–15 s vs the poll | Measured 0.01–0.21 s behind ESPN publish | all 6 (NFL/NHL/MLB topics unverified) |
| 2 | NHL `landing`/`boxscore` `clock.running` at 1–2 s cadence | the whistle itself, not the next logged play; typically several seconds before ESPN's pbp row | The clock object exists in both fixtures; NHL's scorer stops it at the whistle | NHL |
| 3 | NBA liveData `playbyplay` (`timeActual`) and `boxscore.gameClock` via the S3 mirror | a few seconds; **UNVERIFIED** until measured, but `timeActual` lets you measure it exactly in one night | NBA's own stat crew feed; ESPN's NBA pbp is downstream | NBA |
| 4 | MLB `feed/live` (+`diffPatch`) `inningState` Middle/End, pitch `endTime`, `Mound Visit`/`Pitching Substitution` actions | seconds; it is the origin feed | Every pitch stamped to the second | MLB |
| 5 | **Screen truth** (Prime score bug disappears; YTTV UI-tree "Ad" marker if the experiment works) | not earlier in real time (the screen runs 25–50 s behind the data) but EXACT for "a commercial is on the screen now" — it removes the delay-estimate error that causes mid-play cuts | Prime OCR already in the repo | the game on screen |
| 6 | Clock-stopped heuristic on any clock feed: if `displayClock`/`gameClock` has not moved for ≥ N s while in progress, the ball is dead (NBA/NHL/CBB; not NFL, where the clock runs between plays) | same latency as the feed, but fires on EVERY whistle, including ones ESPN logs late (fouls, out-of-bounds) | trivial to backtest on the archive | NBA, NHL, CBB, WCBB |

Nothing free is faster than FastCast for **NFL and CFB** (the NFL's own `api.nfl.com` needs a token from
the site and is not licensed; CFBD live plays are Patreon-only; NCAA LiveStats is a Genius Sports commercial
feed). For **college basketball** the same holds: ESPN/FastCast is the ceiling.

---

## 3. Full inventory

Columns: live latency · historical depth · how to label from it · expected lift (which job) · effort ·
free/legal status. "Fixture" = verified in this repo's saved responses.

### 3A. Live game feeds

| # | Signal | Source (URL) | Live latency | History | Label from it | Lift | Effort | Free / legal |
|---|---|---|---|---|---|---|---|---|
| A1 | ESPN scoreboard: `status.type.detail`, `displayClock`, `period`, NFL `situation` (`down`, `distance`, `possession`, `yardLine`, `homeTimeouts`/`awayTimeouts`, `lastPlay`) | `https://site.api.espn.com/apis/site/v2/sports/{football/nfl, football/college-football, basketball/nba, basketball/mens-college-basketball, hockey/nhl, baseball/mlb}/scoreboard` (`?dates=YYYYMMDD`, college `&groups=`) | poll; CDN-cached ~10 s | archive via `?dates=` back years (scoreboards only) | game phase, clock, timeouts left (NFL) | medium: dead-ball (NFL `lastPlay.type` Timeout / Two-minute warning / End of Quarter) | in place | undocumented; Disney TOU §2.B(x) forbids automated access → personal, non-commercial gray zone |
| A2 | ESPN summary plays with `wallclock`, `clock`, `period`, `type.text`, `scoringPlay`, `scoreValue`; NFL `drives.current/previous` (`timeElapsed`, `start/end` down-distance-yardline), `situation`; `header.competitions[].status` | `.../summary?event={id}` | poll; same cache | ESPN event ids back to ~2002 (college) / 1990s (pro); `wallclock` present since ~2012–2015 depending on league | stoppage onset = wallclock of timeout/end-period/review/injury row; stoppage end ≈ wallclock of the next clock-moving play (an UPPER bound for the restart, since ESPN stamps the logged play, not the snap/inbound) | high for all jobs (already the backbone) | in place | as A1 |
| A3 | ESPN `winprobability[]` per play (`homeWinPercentage`, `playId`) | in summary; also core `.../probabilities?limit=1000` with `secondsLeft`, `spreadCoverProbHome`, `totalOverProb`, `tiePercentage`, `lastModified` | with the play | same as plays | WP delta per play → big-moment and leverage labels; `spreadCoverProbHome`/`totalOverProb` → bet leverage | high: hype/leverage, bets | ~3 h for core fields | present for NFL, NBA, CBB, WCBB, MLB; **absent for NHL** (fixture) |
| A4 | ESPN odds: summary `pickcenter[]`/`odds[]` (`spread`, `overUnder`, `details`); core `.../odds`, `.../odds/{provider}/history/0/movement?limit=100` | `https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{lg}/events/{id}/competitions/{id}/odds` | pregame + movement rows with timestamps | movement history per game | pregame expected closeness; live line moves | medium: hype | ~2 h | same gray zone |
| A5 | ESPN `videos[]` (MP4 `links.source.mezzanine/HD/full`, HLS, `duration`, `originalPublishDate`, `timeRestrictions.expirationDate` ≈ +48 h, `deviceRestrictions` whitelist incl. `settop`, `geoRestrictions` incl. US) | in summary | clips appear minutes after the play (measure: `originalPublishDate` − play `wallclock`) | 48-h window only | which plays have clips; publish lag distribution | high: catch-up reel | in progress (`reel.py`) | playing CDN files in your own player = TOS gray; personal use |
| A6 | ESPN `injuries[]` (per team: `athlete`, `status`, `type`, `details`, `date`) | in summary (NFL, NBA, NHL, MLB fixtures have it); core `.../teams/{id}/injuries` | pregame | none useful | star-out flag | low: hype | ~1 h | gray |
| A7 | ESPN FastCast websocket (JSON-patch stream of the summary document) | `GET https://fastcast.semfs.engsvc.go.com/public/websockethost` → `wss://{ip}:{securePort}/FastcastService/pubsub/profiles/12000?TrafficManager-Token=…`, topic `gp-<sport>-<league>-<eventId>` | **0.01–0.21 s after ESPN publish (measured)** | none (live only; record it yourself: `--record`) | same labels as A2, but 3–15 s earlier | **highest for dead-ball onset timing** | built; flag off; verify NFL/NHL/MLB docs | undocumented protocol; may change any time |
| A8 | NBA liveData play-by-play: `actions[].timeActual` (UTC ms), `clock` (`PT07M19.00S`), `actionType` (`timeout` full/challenge, `period` start/end, `foul`, `freethrow`, `jumpball`, `substitution`, `violation`, `2pt/3pt`, `rebound`, `turnover`), `description`, `scoreHome/Away`, `possession`, `edited` | `https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gameId}.json` (403 for scripts → S3 mirror `nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/...` as in `leagues.py`); `boxscore/boxscore_{gameId}.json` (`gameClock`, `gameStatusText`, `period`); `scoreboard/todaysScoreboard_00.json` | a few seconds; **UNVERIFIED** — `timeActual` makes it measurable | files exist for recent seasons (≈2019-20 on; check per game); older via `stats.nba.com` `playbyplayv3` (undocumented, needs browser headers) | exact stoppage duration = `timeActual(timeout)` → `timeActual(first action whose clock < stop clock)`; FT windows = `freethrow 1 of 2` → `2 of 2`; quarter break = `period end` → `period start` | high: NBA restart + dead-ball; measures ESPN lag | ~4 h | NBA.com TOU: personal, non-commercial; undocumented |
| A9 | NHL gamecenter: `landing`/`boxscore` `clock{timeRemaining, secondsRemaining, running, inIntermission}`, `gameState`, `tvBroadcasts[].market` (N/H/A); `play-by-play` `plays[].typeDescKey` (`faceoff`, `stoppage`, `penalty`, `goal`, `shot-on-goal`, `period-start/end`, `delayed-penalty`), `details.reason`/`secondaryReason` (`tv-timeout`, `icing`, `offside`, `puck-frozen`, `goalie-stopped-after-sog`, `puck-in-netting/benches/crowd`), `situationCode` (PP / empty net), `timeInPeriod` | `https://api-web.nhle.com/v1/gamecenter/{id}/landing`, `/boxscore`, `/play-by-play` (repo uses `/v1/wsc/play-by-play/{id}`), `/v1/score/{date|now}`, shifts `https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={id}` | poll; seconds | pbp back to 2010-11 (full events); **no per-event wall time in the new API** (fixture: only `timeInPeriod`/`timeRemaining`) | dead = `running:false`; restart = flip to `running:true` (timestamp the transition at 1–2 s cadence); stoppage type = `reason`; TV timeout = `secondaryReason:"tv-timeout"`; PP = `situationCode` | **high: NHL dead-ball/restart/PP** | ~3 h | undocumented, unsupported, community-documented; free |
| A10 | NHL goal clips: `landing.summary.scoring[].goals[].highlightClip`, `highlightClipSharingUrl`, `discreteClip`, `pptReplayUrl` | same landing call | minutes after the goal | per game | clip per goal | high for NHL catch-up (ESPN has few NHL clips) | ~2 h | free to view; embedding gray |
| A11 | MLB live feed: `liveData.plays.allPlays[].about{startTime,endTime,isScoringPlay,hasReview,captivatingIndex}`, `playEvents[]{type pitch|action|no_pitch|stepoff, startTime, endTime, details.event (Mound Visit, Pitching Substitution, Batter Timeout, Defensive Switch, Game Advisory), pitchData, count}`, `linescore.inningState` (Top/Bottom/Middle/End), `outs/balls/strikes`, `offense/defense`, `gameData.moundVisits`, `gameData.review`, `gameData.weather`, `status.detailedState` | `https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live`; incremental `…/feed/live/diffPatch?startTimecode=YYYYMMDD_HHMMSS`; timestamps `…/feed/live/timestamps`; schedule `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=…&hydrate=broadcasts,team` | seconds (origin feed) | pitch-level `startTime` back to ~2008 (PITCHf/x era); events to 1900s via Retrosheet (no times) | between-innings = `about.endTime` of last out → first `playEvents[0].startTime` of the next half; pitching change = `Pitching Substitution` action → next pitch; pitch-to-pitch gaps vs the 15/18-s timer | high: MLB dead-ball/restart (league #6 for Nick, but near-free) | ~4 h | MLBAM terms (`gdx.mlb.com/components/copyright.txt`): "individual, non-commercial, non-bulk use" |
| A12 | MLB win probability + leverage: `homeTeamWinProbability`, `homeTeamWinProbabilityAdded`, `contextMetrics{homeWinProbability, …}` | `https://statsapi.mlb.com/api/v1/game/{pk}/winProbability` (optional `timecode`), `…/contextMetrics` | per play | per game, historical | WPA per play → big moment; leverage | medium | ~1 h | as A11 |
| A13 | MLB highlight clips | `https://statsapi.mlb.com/api/v1/game/{pk}/content` → `highlights.highlights.items[].playbacks[]` | minutes | per game | clip per play | medium: catch-up | ~2 h | as A11; Baseball Savant `gf?game_pk=` / `sporty-videos?playId=` exist but are "not intended for public consumption" |
| A14 | CFBD plays (`wallclock`, `clock`, `period`, `playType`, `scoring`, `down`, `distance`, `yardsToGoal`, `ppa`, `offenseTimeouts`/`defenseTimeouts`) | `https://api.collegefootballdata.com/plays?year=&week=` (Bearer key) | **not live on free tiers**; `/live/plays` is Patreon-only; Tier 3 ($10/mo) adds GraphQL realtime | 2014+ (cfbfastR release `cfbfastR_cfb_pbp`), CFBD further back | post-game labels with wall times | medium: CFB training | ~2 h | Free 1k calls/mo; **Academic 3k/mo with a .edu e-mail (free)**; live = paid |
| A15 | Stream deep links (Prime GTIs, ESPN watch ids) | Apple UTS + ESPN Watch Graph (`redzone/sources/eventlinks.py`) | n/a | n/a | which app/channel carries the game → stream-delay prior per app | low for models, high for switching | in place | unofficial; may violate providers' terms |

### 3B. Historical / training datasets

| # | Dataset | URL | Depth | Timing fields | Label recipes | Lift | Effort | Free / legal |
|---|---|---|---|---|---|---|---|---|
| B1 | nflverse / nflfastR play-by-play | `https://github.com/nflverse/nflverse-data/releases/tag/pbp` (`play_by_play_{season}.parquet`); Python `nfl_data_py` | 1999+ ; `time_of_day` 2011+ | `time_of_day` ("Time of day of play in UTC HH:MM:SS, 2011+ source nfl"), `play_clock` ("Time on the playclock when the ball was snapped"), `end_clock_time`, `quarter_seconds_remaining`, `drive_game_clock_start/end`, `drive_time_of_possession`, `timeout`, `timeout_team`, `replay_or_challenge(_result)`, `play_type_nfl`, `no_huddle`, `qb_spike`, `wp`, `vegas_wp`, `wpa`, `vegas_wpa`, `spread_line`, `total_line`, `weather`, `temp`, `wind`, `roof`, `surface` | inter-snap wall gap = Δ`time_of_day` by situation (after incomplete / after score / after timeout / two-minute / end quarter / review); `play_clock` at snap tells how much of the 40/25 s the offense used (restart prior); `vegas_wpa` ≥ threshold or TD/turnover = big moment; `vegas_wp` in [0.2,0.8] late = crunch | **high: NFL restart + dead-ball length + big-moment + leverage** | ~4 h to normalize (`lab/sportsdata.py` already reads it) | MIT-licensed package; data free, non-commercial-friendly |
| B2 | hoopR ESPN NBA + MBB pbp (sportsdataverse-data releases) | `https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_pbp/play_by_play_{season}.parquet`; MBB via `load_mbb_pbp()` release (tag family `espn_mens_college_basketball_pbp`), 2003 & 2006+ | NBA 2002+; MBB 2003, 2006+ | `wallclock`, `clock_display_value`, `type_text`, `period`, `game_play_number`, coordinates, WP columns | timeout row → first clock-moving row = measured dead time (upper-bound restart); media-timeout detection by clock marks; FT windows | high: NBA/CBB training | in place for NBA (manifest), add MBB/WBB | MIT; ESPN data personal use |
| B3 | cfbfastR pbp (CFBD-sourced) | `https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/cfbfastR_cfb_pbp` | 2014+ | `wallclock` (from CFBD), clock, `wp_before/after`, timeouts | as B1 (wall-gap labels), WP labels | medium-high: CFB (league #2) | ~3 h | MIT / CFBD terms |
| B4 | fastRhockey NHL pbp (full, with shifts) | `load_nhl_pbp()` from sportsdataverse-data releases | 2010-11+ (season end-year) | `period_time`, `period_seconds_remaining`, `game_seconds`, `event_type`, `reason`, `secondaryReason`, `strength_state`, `home_skaters`, `xg` | stoppage reason + strength labels; **no wall time → join to the ESPN NHL summary `wallclock` the repo archives, or to the old statsapi `about.dateTime` (2010–2023; UNVERIFIED that any mirror kept it)** | medium: NHL dead-ball types, xG for big moments | ~4 h | MIT; NHL data personal use |
| B5 | MoneyPuck shots | `https://moneypuck.com/data.htm` (zip of CSV per season, "All Past Seasons 2007-2024") | 2007-08+ | shot-level, no wall time | xGoals per shot → "danger" labels for big-moment prediction | medium: NHL big moments | ~2 h | free non-commercial; credit MoneyPuck |
| B6 | MLB statsapi history (same endpoints as A11/A12, by `gamePk`) | `https://statsapi.mlb.com/api/v1/schedule?sportId=1&season=…` → `feed/live` | pitch timestamps ≈2008+ | `startTime/endTime` per pitch and plate appearance | exact between-innings and pitching-change durations by broadcast market | medium | ~3 h (bulk is slow; be gentle) | "non-bulk" clause → sample, don't mirror |
| B7 | NFL Big Data Bowl tracking (Kaggle) | `https://www.kaggle.com/competitions/nfl-big-data-bowl-2025/data` (and 2023/2024 editions) | a few weeks per edition, 10 fps frames | per-frame `event` tags: `huddle_break_offense`, `line_set`, `man_in_motion`, `ball_snap`, `pass_forward`…; 2025 edition includes all pre-snap frames | seconds from line-set to snap, by down/distance/clock → a snap-timing prior for live NFL (ESPN never shows the play clock) | medium: NFL restart | ~6 h | Kaggle account; competition terms (non-commercial) |
| B8 | nflverse injuries | `https://github.com/nflverse/nflverse-data/releases/tag/injuries` (`injuries_{season}`) | 2009+ | weekly | star-out features | low | ~1 h | free |
| B9 | NBA official injury reports (PDF, hourly on game days; deadlines 5 PM day-before, 1 PM game-day) | `https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HHAM.pdf`; archive release `nba_stats_injury_report` (2018-12-17+) in sportsdataverse-data | hours | 2018+ | star-out | low | ~2 h (PDF parse) | official, free |
| B10 | Closing lines history | nflfastR `spread_line`/`total_line` (PFR); `https://www.sportsoddshistory.com` (NFL 1978+); SportsBookReview archive (gray) | n/a | decades (NFL), partial others | pregame "expected close game" | low-medium: hype prior | ~2 h | free; SBR scraping gray |
| B11 | Your own recordings | `data/recordings/<day>/plays.jsonl` (per-source lag), FastCast `--record`, Prime scorebug samples, YTTV boundary samples | n/a | from tonight on | ground truth for FEED LAG and STREAM DELAY, which no public set has | **high: the calibration nobody else can give you** | in place; just keep them | yours |

### 3C. Rules and schedules (deterministic features; see §4 for the exact text)

| # | Rule set | Source | What to encode | Lift |
|---|---|---|---|---|
| C1 | NBA Rule 5 (timing) | `https://official.nba.com/rule-no-5-scoring-and-timing/` | mandatory TO at first dead ball if none before 6:59 / 2:59; 2:45 local / 3:15 national; other TOs 1:15; 7 TOs, ≤4 in Q4, ≤2 after the later of 3:00 or the 2nd mandatory; 2:30 between periods (3:30 national TV, Q1-2 & Q3-4); 2:30 before OT; 15:00 halftime; 2 TOs per OT | high: NBA dead-ball odds + length |
| C2 | NHL TV timeouts | `https://en.wikipedia.org/wiki/Television_timeout` (+ NHL rulebook) | first stoppage after 14/10/6 min remaining, exactly 120 s, even strength only; none after a goal, after an icing, during a PP, in the last 30 s of P1/P2, last 2:00 of P3, within one minute of the previous TV timeout, after faceoff violations; none in OT/shootout (regular season); playoffs: one at the first stoppage after 10:00 of each OT | high: NHL dead-ball odds |
| C3 | NCAA men's basketball media timeouts | NCAA D-I media timeout format PDF (`https://ncaaorg.s3.amazonaws.com/championships/sports/basketball/rules/men/2019-20PRMBB_D1RSMediaTimeoutFormat.pdf`), Wikipedia, OVC 2023-24 experimental release | first dead ball after 16/12/8/4 each half; a team TO within 30 s before a mark becomes it (2015-16); **the first team timeout of the 2nd half becomes the full media timeout** (a 5th "floater"); some conferences test 17/14/11/8/4 in the 2nd half — detect the format from the first two breaks of the half | high: CBB (UNC) |
| C4 | NCAA women's | same | first whistle after 5:00 each quarter; earlier team TO becomes it; first team TO of 2nd half becomes a full media TO | medium |
| C5 | NFL breaks + play clock | Wikipedia (16 breaks/game, 8 per half, mandatory at end Q1/Q3 + two-minute warnings; networks may break during replay reviews); 2017 league change to 4 per quarter at ~2:20 (Athletic Business); play clock `https://operations.nfl.com/the-rules/nfl-video-rulebook/play-clock/` | 40 s from end of play; 25 s from ready-for-play after team timeouts, two-minute warning, end of period, penalties, replay; count breaks per quarter against the quota (a quarter that already has 4 is unlikely to break again except mandatory) | high: NFL length + restart |
| C6 | CFB 2024 two-minute timeout | NCAA / CBS / theScore (April 2024) | fixed media break at the 2:00 mark of Q2 and Q4 (clock stops at the next dead ball); networks hold a media timeout for it; no fixed per-quarter count otherwise → learn from B3 | medium |
| C7 | MLB pace-of-game regulations | `https://www.mlbplayers.com/pace-of-game-regulations`; 2024 changes (CBS/SI) | 15 s bases empty, 18 s runners on (2024), 30 s between batters; inning breaks 2:15 local / 2:40 national / 3:10 playoffs; pitching change resets the break timer (2:15 in 2023, 2:00 in 2024 per SI); 4 mound visits (2024); batter in box by 8 s | high: MLB restart |

### 3D. Stick-side and screen-side signals (the Fire TV sticks over ADB; the TV via SmartThings)

| # | Signal | How | Latency | Label use | Lift | Effort | Legal |
|---|---|---|---|---|---|---|---|
| D1 | Media session state (`dumpsys media_session`: `state` 0 none/1 stopped/2 paused/3 playing, `position`, `speed`, `lastPositionUpdateTime`, metadata `title`) | in `screen/boundary.py` | instant (local) | YTTV show-title flips at :00/:15/:30/:45 → stream delay (measured 22–26 s); per-channel origin → delay from one read | high for delay; nil for dead-ball (video keeps playing through ads) | in place | your device |
| D2 | Screencap → score-bug OCR (Apple Vision) on Prime | `screen/scorebug.py`, 2 s cadence | instant | bug present/absent; score change → delay sample | **high: commercial-on-screen truth for Prime games** | in place; add the "bug gone" gate | your device; Prime frames are not DRM-black (measured) |
| D3 | Screencap on YouTube TV | same | — | **DRM-black (measured)** → no luma, no OCR | none | — | do not try to defeat DRM (DMCA §1201) |
| D4 | UI tree / logs on YTTV during ads (`uiautomator dump`, `logcat`) | 10-min experiment | instant | if the YTTV app renders an "Ad" badge/DAI marker (Google inserts its own ads into some breaks since 2018), that is a direct commercial flag | potentially high; **UNVERIFIED** | 10 min + 2 h | reading your own device's UI tree is not circumvention |
| D5 | Audio (Mac mic surge) | `screen/audio.py` | — | delay samples | — | exists, but **Nick said no mic/camera** → keep off | — |
| D6 | Focused activity / app health (`dumpsys activity top`, `window`) | watchdog | instant | home-screen / crash detection | watchdog | in place | your device |
| D7 | HDMI capture of the TV picture (luma, captions, audio) | needs a capture dongle/HDHomeRun (~$30–$110) | — | ground-truth commercial detection | high but **not $0** and HDCP-protected sources are black | — | capturing HDCP output is circumvention; OTA ATSC via antenna+tuner is legal but costs hardware |

### 3E. Betting and odds (free only)

| # | Source | What | Live? | Status |
|---|---|---|---|---|
| E1 | ESPN core `probabilities` (A3): `spreadCoverProbHome`, `totalOverProb`, `tiePercentage`, `secondsLeft` | live per-play bet-outcome odds | yes | free, gray TOU |
| E2 | ESPN core `odds` + `history/0/movement` (A4) | pregame and in-game line movement by provider id | yes (movement rows) | free, gray TOU |
| E3 | The Odds API | pregame + in-play odds, 500 credits/month on the free tier; each request costs markets × regions credits (3 markets × 1 region = 3 credits) → ~160 live calls a month | technically yes, practically no | free tier too thin for live; fine for one pregame pull per day |
| E4 | nflfastR `spread_line`/`total_line`, SportsOddsHistory | closing lines for training priors | no | free |
| E5 | Sportsbook sites' own JSON (DraftKings/FanDuel) | live lines | yes | **not legal to scrape under their terms; skip** |

### 3F. Weather, injuries, misc. context

| # | Source | URL | Use | Lift |
|---|---|---|---|---|
| F1 | Open-Meteo | `https://api.open-meteo.com/v1/forecast?latitude=…&longitude=…&current=temperature_2m,precipitation,wind_speed_10m` (no key; free non-commercial < 10k calls/day) | outdoor NFL/CFB/MLB scoring environment; MLB/CFB delay risk | low |
| F2 | NWS | `https://api.weather.gov/points/{lat},{lon}` → forecast; `/alerts/active?area={state}` (User-Agent required; no key) | lightning/rain delay alerts | low (MLB `status.detailedState` already says "Delayed") |
| F3 | nflfastR `weather/temp/wind/roof`, MLB `gameData.weather` | in B1 / A11 | training features | low |
| F4 | Injuries (A6, B8, B9) | — | star-out → lower hype | low |
| F5 | Officials (`gameInfo.officials`), venue (`venue_info.indoor`) | ESPN summary | referee tendencies; dome vs outdoor | low |

### 3G. Closed captions / subtitles — honest answer

- Captions ARE in the broadcasts (FCC-mandated) and decoding them for personal viewing is legal, but on
  YouTube TV / Prime they travel inside the DRM-protected stream and are drawn by the app. Pulling them off
  the stick means defeating Widevine → DMCA §1201 territory. **Not an option.**
- Legal routes cost money: an OTA antenna + ATSC tuner (HDHomeRun, ~$110) gives CEA-708 captions for
  CBS/NBC/FOX/ABC games only; not $0 and not for ESPN/TNT/Prime.
- What IS allowed: D4 above — if the YTTV app exposes caption or ad text in its own accessibility tree
  (`uiautomator dump`), reading it is screen-reading your own device, not circumvention. Test before assuming.

### 3H. The stream-delay offset per app

| App | What is known | How to keep it calibrated |
|---|---|---|
| YouTube TV | measured 22–26 s behind live on the sticks (boundary flips); the "Broadcast Delay → Decrease for 48 hours" option (three-dot menu while watching, TV only) cuts roughly 10 s but reverts after 48 h and "may increase playback interruptions" | D1 boundary samples + media-session position origin per channel (in place). Do NOT enable "Decrease" unless the delay book is re-measured: a smaller delay means less lead time between the data and the screen |
| Prime Video (TNF, NBA) | not linear → no quarter-hour boundaries; delay measured from score-bug changes (D2); Prime pauses when hidden (RESUME.md) | D2 scorebug samples each score change; the "I just saw it" tap as fallback |
| Both | the archive's rule of thumb: the data runs 25–50 s ahead of the screen | log `delay_s` per app per night → a prior per app/channel for the model |

---

## 4. Per-league cheat sheet (what the model should see, live)

| League | Fastest free stoppage signal | Restart signal | WP / leverage | Break rules to encode | Clips | Training depth with wall times |
|---|---|---|---|---|---|---|
| NFL | FastCast; scoreboard `situation.lastPlay`; `drives` | 40/25-s play clock from `wallclock` of the previous play + B1/B7 priors; ESPN has no play clock | ESPN WP + core `spreadCoverProbHome`; nflfastR `vegas_wp` for training | 4 breaks/quarter (~2:20), mandatory end Q1/Q3 + two-minute warnings, breaks during reviews, no TV timeout after a team timeout usually | ESPN `videos` (12 in MNF) | nflfastR 2011+ (`time_of_day`, `play_clock`) |
| CFB | FastCast (CFBD live is paid) | same as NFL; 2024 two-minute timeout in Q2/Q4 | ESPN WP; cfbfastR `wp_before/after` | no fixed quota; learn from cfbfastR `wallclock` gaps | ESPN `videos` | cfbfastR/CFBD `wallclock` 2014+ (Academic tier free with unc.edu) |
| NBA | NBA liveData (`timeActual`, `gameClock`) + FastCast | first clock-moving `action` after the timeout/FT; mandatory TO 2:45/3:15; others 1:15; 2:30/3:30 between quarters | ESPN WP; core probabilities | Rule 5 §VI (6:59/2:59 marks, charged team, Q4 caps) | ESPN `videos`; `videoeventsasset` (unofficial) | hoopR 2002+ (`wallclock`); NBA liveData `timeActual` ≈2019+ |
| NCAA MBB | FastCast + clock-stopped heuristic (NCAA LiveStats is not free) | first clock-moving play after the whistle | ESPN WP | 16/12/8/4 + first-TO-of-2nd-half floater + 30-s window + conference formats | ESPN `videos` | hoopR MBB 2003, 2006+ (`wallclock`) |
| NHL | `clock.running` at 1–2 s + pbp `stoppage.reason` | `clock.running` → true (faceoff) | **none from ESPN** → analytic/learned WP on fastRhockey + MoneyPuck xG; `situationCode` for PP/EN | 14/10/6 TV timeouts with 7 exceptions; 120 s | NHL `highlightClipSharingUrl` per goal | fastRhockey 2010+ (no wall time) + ESPN NHL `wallclock` archive |
| MLB | `feed/live` `inningState` Middle/End, pitch `endTime`, actions | next pitch `startTime`; 2:15/2:40/3:10 inning breaks; 15/18-s timer | statsapi `winProbability` + `contextMetrics` | pace-of-game regs | statsapi `/content` highlights | statsapi pitch `startTime` ≈2008+ |

---

## 5. Notes

### 5.1 Label definitions the factory should emit (same schema, every league)
- `dead_start_wall`: first evidence the ball is dead (ESPN row `wallclock` or NBA `timeActual` or MLB
  `endTime` or NHL `running:false` transition). `dead_end_wall`: first evidence it is live again (NBA first
  clock-moving `timeActual`; MLB next `startTime`; NHL `running:true`; NFL/CFB next play `wallclock` MINUS
  the play's own duration, or better, the nflfastR `time_of_day` of the next play). Store the evidence
  type, since ESPN-only ends are upper bounds.
- `stoppage_type`: timeout_team / timeout_tv_or_mandatory / end_period / halftime / review_or_challenge /
  injury / free_throws / pitching_change / between_innings / score_break / whistle_short / unknown.
  Source of truth by league: NHL `reason`, NBA `actionType/subType`, MLB `details.event`, NFL/CFB
  `type.text` + nflfastR `timeout`/`replay_or_challenge`.
- `market`: national / local (A1 `national_tv`, NHL `tvBroadcasts.market`, MLB broadcasts) — drives length.
- `big_moment`: |ΔWP| ≥ league threshold (nflfastR `vegas_wpa`, ESPN WP delta, MLB `WinProbabilityAdded`,
  NHL goal/PP-goal/xG ≥ 0.2) within the next 60 s of game wall time.
- `leverage`: WP in a band + `secondsLeft`; for bets, `spreadCoverProbHome` / `totalOverProb` swing.
- `restart_in_s`: `dead_end_wall − now` for every snapshot inside a stoppage (the regression target).

### 5.2 Legal and cost honesty
- **ESPN** (site + core + FastCast): undocumented; Disney Terms of Use §2.B(x) prohibit accessing or
  extracting with "a robot, spider, script, or other automated means" and limit everything to "personal,
  noncommercial use". Fine for one household at low volume; **a product would need a licensed feed**.
- **NBA / NHL** feeds: undocumented, unsupported, community-documented; personal use tolerated in practice.
- **MLB statsapi**: MLBAM copyright allows "individual, non-commercial, non-bulk use" → poll live games,
  do not mirror seasons.
- **CFBD**: free 1,000 calls/month; Academic 3,000/month free with .edu; live data paid (Patreon). Not a
  live source at $0.
- **NCAA LiveStats (Genius Sports)**, **Sportradar**, **SportsDataIO**, **NFL NGS**, **Second Spectrum**:
  not free. **The Odds API** free tier: real but too small for live.
- **Clips**: ESPN/NHL/MLB CDN files are public URLs; playing them in your own LAN player is personal use
  but not licensed redistribution. Keep the reel private; never re-host.
- **DRM**: never capture or decode protected video/captions from YTTV/Prime. Nick's own YTTV recordings
  of NFL RedZone are the only broadcast video allowed, and screenshots may be black.

### 5.3 What to measure in the first night with the new feeds (so the gates can be judged)
1. Per-source lag per league: FastCast vs poll vs NBA liveData vs NHL clock vs MLB feed, using the
   events' own timestamps (`timeActual`, `startTime`, `wallclock`) as the reference. Store p50/p90.
2. Stream delay per app per night from D1/D2 (p50, sigma).
3. Stoppage duration error of the rules vs measured (`dead_end_wall − dead_start_wall`) by type × market.
4. Restart error: predicted vs actual `dead_end_wall`, the number that decides mid-play entries.

### 5.4 Things that looked promising but are not worth it now
- cdn.nba.com direct access (403 for scripts; the S3 mirror works) — keep the mirror.
- Old NHL statsapi `about.dateTime` per event (2010–2023): the API is dead; whether any free mirror kept
  the wall times is unverified. The ESPN NHL summary `wallclock` already archived is the practical source.
- YouTube Data API for highlights: minutes-to-hours late; useless for in-game catch-up.
- Weather beyond a flag: MLB/CFB delay states already come from the league feeds.

---

## 6. Sources
- Repo (read-only): `redzone/sources/espn.py`, `fastcast.py`, `leagues.py`, `eventlinks.py`; `redzone/scoring/breaks.py`, `brain.py`; `redzone/screen/boundary.py`, `scorebug.py`, `audio.py`; `redzone/decider/screen.py`; `redzone/lab/sportsdata.py`; `tests/fixtures/*` (ESPN, NBA cdn, NHL api-web, MLB statsapi, FastCast); `data/archive/*`; `data/training/manifest.json`; `docs/build-plan-2026-11-03.md`; `docs/handoff.md`.
- ESPN hidden API gists: https://gist.github.com/nntrn/ee26cb2a0716de0947a0a4e9a157bc1c ; https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b
- Disney Terms of Use (covers ESPN): https://disneytermsofuse.com/english/
- nflreadr pbp dictionary: https://github.com/nflverse/nflreadr/blob/main/data-raw/dictionary_pbp.csv ; nflverse releases: https://github.com/nflverse/nflverse-data/releases ; nflfastR WP columns: https://rdrr.io/cran/nflfastR/src/R/ep_wp_calculators.R ; `load_injuries`: https://rdrr.io/cran/nflreadr/man/load_injuries.html
- CFBD tiers: https://collegefootballdata.com/api-tiers ; API v2 GA post: https://blog.collegefootballdata.com/api-v2-is-now-in-general-availability/ ; Play model: https://github.com/CFBD/cfb.js/blob/master/docs/Play.md ; PlaysApi (live = Patreon): https://github.com/CFBD/cfb.js/blob/master/docs/PlaysApi.md
- hoopR `load_mbb_pbp`: https://hoopr.sportsdataverse.org/reference/load_mbb_pbp.html ; cfbfastR `load_cfb_pbp`: https://cfbfastr.sportsdataverse.org/reference/load_cfb_pbp.html ; fastRhockey `load_nhl_pbp`: https://fastrhockey.sportsdataverse.org/reference/load_nhl_pbp.html ; releases: https://github.com/sportsdataverse/sportsdataverse-data/releases
- NBA liveData wrappers (hoopR `nba_live_pbp`): https://search.r-project.org/CRAN/refmans/hoopR/html/nba_live_pbp.html ; NBA Rule 5: https://official.nba.com/rule-no-5-scoring-and-timing/ ; 2017 timeout changes: https://pr.nba.com/nba-rules-changes-2017-18 ; NBA injury report: https://official.nba.com/nba-injury-report ; PDFs e.g. https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-11-26_12PM.pdf ; `videoeventsasset`: https://github.com/swar/nba_api/blob/master/src/nba_api/stats/endpoints/videoeventsasset.py
- NHL api-web community refs: https://free-api-explorer.pages.dev/api/nhl-api.html ; https://docs.rs/crate/nhl_api/0.7.1/source/CLAUDE.md ; hockeyR / API change: https://github.com/danmorse314/hockeyR ; old API docs: https://gitlab.com/dword4/nhlapi/-/raw/master/stats-api.md ; MoneyPuck data: https://moneypuck.com/data.htm
- MLB statsapi: https://pymlb-statsapi.readthedocs.io/en/latest/schemas/game.html ; https://statsapi.mlb.com/api/v1.1/game/823934/feed/live ; content endpoint example: https://statsapi.mlb.com/api/v1/game/631204/content ; MLBAM terms: http://gdx.mlb.com/components/copyright.txt ; pace-of-game regs (2:15/2:40/3:10): https://www.mlbplayers.com/pace-of-game-regulations ; 2024 changes (18 s, 4 mound visits): https://www.cbssports.com/mlb/news/mlb-rule-changes-pitch-clock-tweak-widened-runners-lane-more-coming-in-2024-despite-mlbpa-objection ; Baseball Savant `gf`: https://reddit.birdcat.cafe/r/Sabermetrics/comments/plb8b9/using_mlb_statcast_api/
- TV timeout rules: https://en.wikipedia.org/wiki/Television_timeout ; NFL 2017 break restructuring: https://www.athleticbusiness.com/operations/media-technology/news/15149742/nfl-pitches-broadcast-other-changes-to-please-fans ; NFL play clock: https://operations.nfl.com/the-rules/nfl-video-rulebook/play-clock/ ; NCAA men's media timeout format: https://ncaaorg.s3.amazonaws.com/championships/sports/basketball/rules/men/2019-20PRMBB_D1RSMediaTimeoutFormat.pdf ; OVC experimental 2nd-half format: https://ovcsports.com/news/2023/12/4/mens-basketball-teams-to-use-experimental-rule-for-second-half-media-timeouts-in-conference-play.aspx ; CFB two-minute timeout: https://www.cbssports.com/college-football/news/ncaa-approves-2-minute-warning-in-college-football-games-helmet-communications-beginning-in-2024-season ; https://www.ncaa.org/news/2024/4/19/media-center-technology-rules-approved-in-football.aspx
- YouTube TV Broadcast Delay: https://9to5google.com/2023/12/07/youtube-tv-broadcast-delay-setting ; https://www.tomsguide.com/entertainment/streaming/how-to-change-your-youtube-tv-broadcast-delay-setting-for-super-bowl-2026 ; YTTV dynamic ad insertion: https://cordcuttersnews.com/youtube-tv-is-working-on-improving-its-ads/
- Android media session states: https://androidtv.readthedocs.io/en/v0.0.24/androidtv.constants.html
- The Odds API: https://the-odds-api.com/ ; credit math: https://oddspapi.io/blog/the-odds-api-free-tier-limits
- Open-Meteo terms: https://open-meteo.com/en/terms ; NWS API: https://www.weather.gov/documentation/services-web-api
- Big Data Bowl events: https://arxiv.org/pdf/2502.16313 ; https://unravelsports.readthedocs.io/en/latest/tutorials/american_football.html
- NCAA LiveStats / Genius (not free): https://www.businesswire.com/news/home/20250425480652/en/NCAA-and-Genius-Sports-Expand-Partnership-Through-2032
- Closing-lines history: https://www.sportsoddshistory.com/about-us ; https://github.com/pwu97/bettingtools
