# Live game-state data for NHL and MLB: what is faster than the ESPN poll, free, and legal

Written 2026-11-04 ~05:00Z (Nov 4, 00:00 ET). Measured live on **ANA @ VAN, Nov 3 2026** (NHL game id `2026020246`,
ESPN event `401892643`), from 3rd period 10:26 remaining to Final: side-by-side polling 04:24:40Z–04:46:32Z
(656 polls per 2-s endpoint), ESPN FastCast websocket 04:30:21Z–04:48:52Z, theScore/Yahoo 04:35:40Z–04:50:40Z.
All raw logs, bodies and scripts are under
`/private/tmp/claude-501/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/db4845a1-e6cf-4448-8874-21dc8f52883f/scratchpad/research/live/`
(`raw/poll/`, `raw/fastcast/`, `raw/poll_others/`, `raw/analysis_final.txt|json`, `scripts/`). Only read-only GETs
(one request every 2–5 s per endpoint) and one watch-only websocket subscription; nothing in the app or repo was touched.
MLB is out of season (World Series ended Oct 29), so MLB is endpoint probing + the last WS game's archive, not live.

**Reference clock.** The NHL's `/v1/wsc/play-by-play/{id}` feed stamps every event with `utc` (ms). ESPN's `wallclock`
on the same play is that stamp truncated to the second (45/45 matched, median difference −0.6 s), so `utc` is the
league's own "the scorer logged it" time and the earliest moment any public source could know. All absolute lags
below are **first time the event was visible in the source − `utc`**. Hits, giveaways and takeaways are back-entered
by the scorer minutes later (their `utc` is game-clock-derived), so the headline numbers use only live-entered types
(faceoff, stoppage, goal, shots, period-end). Local clock vs server `Date`: −0.1 to −0.8 s; ignored.

---

## 1. Measured lags (NHL, live)

### 1a. Absolute lag behind the league's own event stamp (`utc`), live-entered events, n=27

| Source (how polled) | p10 | **p50** | p90 | Notes |
|---|---|---|---|---|
| **NHL `api-web.nhle.com/v1/wsc/play-by-play/{id}`** (every 3 s) | 8.6 s | **16.7 s** | 38.3 s | Fastest public source. 32 of its 53 new versions arrived as Cloudflare `BYPASS` (`max-age=0`), i.e. straight from origin; new versions as close as 3.0 s apart |
| ESPN FastCast push (`gp-hockey-nhl-401892643`) | 17.7 s | **25.3 s** | 117.5 s* | Patch arrives 0.067 s (p50) after ESPN's publish `ts`; P messages every 5.2 s. *p90 inflated by 2 plays ESPN back-edited; p75 = 35.4 s |
| ESPN core `…/competitions/{id}/plays?limit=1000` (every 3 s) | 21.3 s | **27.5 s** | 40.0 s | Varnish `max-age` ≤10 + `stale-while-revalidate=7200`; new version only when plays change (median 21 s) |
| NHL `/v1/gamecenter/{id}/play-by-play` (every 2 s) | 18.3 s | **27.7 s** | 43.6 s | Cloudflare `max-age=19`: a new version exactly every 20.0 s, `Age=0` when it appears |
| **ESPN `site.api.espn.com … /summary?event=` (every 2 s)** | 34.8 s | **43.1 s** | 56.6 s | New version every 10.0 s; `max-age` counts 9→1. This is the app's current source: the "~49 s behind the play" is real (all-types p50 = 52 s) |
| theScore `api.thescore.com/nhl/events/{id}/play_by_play_records` (every 4 s) | −3.5 s† | 26.4 s | 37.6 s | n=15; †records sometimes appear with a pre-filled clock before the NHL feed shows the event, matching is by period+clock+type |
| Yahoo `api-secure.sports.yahoo.com/v1/editorial/s/scoreboard` `gamelast_play` (every 5 s) | 2.7 s | 28.1 s | 37.1 s | n=8; new version every 25 s (`max-age=20`+swr) |

Same thing as "ESPN summary minus X" (how much earlier than today's source):
- NHL **wsc** pbp: ESPN summary is **≈ +26 s later** (43.1 − 16.7 on the `utc` scale; wsc leads the gamecenter pbp by 11.4 s p50 pairwise, and gamecenter leads the summary by 14.2 s p50)
- NHL gamecenter pbp: summary later by **+14.2 s** (live types, p50; p25 +12.1, p75 +22.2) / +18.9 s (all 52 events); never more than 2.7 s the other way
- ESPN core plays: summary later by **+15.5 s** (p50; p10 +9.6, p90 +23.5), n=52
- FastCast: summary later by **+18.6 s** (p50; p25 +15.1, p75 +22.1, p90 +23.7 once the two back-edited plays are excluded), n=27

**What this means for the screen.** YouTube TV on the sticks runs 22–26 s behind live (repo measurement; the archive's
rule of thumb is 25–50 s). With the ESPN summary (43 s) the app learns of a whistle **~17–21 s AFTER Nick sees it**.
With the wsc feed (17 s) it learns **~5–9 s BEFORE** the whistle reaches the TV; FastCast/core/gamecenter (25–28 s)
are roughly simultaneous with the screen. The restart (faceoff) is still only *observed* 17 s after it happened, so the
re-entry has to be predicted from the stoppage type (see §6).

### 1b. Update cadence and cache behaviour (every endpoint, measured)

| Endpoint | Cache headers seen | New version every (p50, min–p90) | Poll RTT p50 |
|---|---|---|---|
| `api-web.nhle.com/v1/gamecenter/{id}/landing` | `cache-control: no-transform, max-age=19, s-maxage=19`, `cf-cache-status: HIT`, `Age` 0–18 | **20.0 s** (19.9–20.1) | 66 ms |
| `…/gamecenter/{id}/play-by-play` | same | **20.0 s** (19.9–20.1) | 65 ms |
| `…/gamecenter/{id}/boxscore` | same; 19/438 polls `BYPASS` with `max-age=0` | 21.0 s (17.9–39) | 68 ms |
| `…/v1/wsc/play-by-play/{id}` | `max-age=14, s-maxage=14` HIT, **82/438 polls `BYPASS`/`max-age=0`** | 17.9 s (**3.0**–47.6) | 69 ms |
| `…/v1/score/{date}` (`/score/now` → 307 to the date) | `max-age=19`, HIT | 20.0 s (19.9–40) | 66 ms |
| `site.api.espn.com …/summary` | `cache-control: max-age=N` counting 9→1, no `Age` | **10.0 s** (6.0–10.2) | 201 ms |
| `site.api.espn.com …/scoreboard` | `max-age` 1–9 | 9 s (3–12) | 187 ms |
| `sports.core.api.espn.com …/plays?limit=1000` | `max-age≤10, stale-while-revalidate=7200`, `via: Varnish/7.5` | 21 s (8.8–42), only on change | 202 ms (700 KB) |
| ESPN FastCast websocket | n/a (push) | P message every **5.2 s**; `op:B` heartbeat ~10 s; checkpoint `fcast.espncdn.com … /checkpoint` `max-age=3598`, 441 KB | 0.067 s behind `ts` |
| ESPN "fauxcast" (`meta.syncUrl` → `client.espncdn.com/fauxcast/stats/90/{id}/en/us/latest` → 302 to a timestamped snapshot) | `max-age=7–10`, CloudFront | snapshot every ~60–70 s | slower than the summary; ignore |
| `api.thescore.com/nhl/events/{id}/play_by_play_records` | `public, stale-while-revalidate=3, stale-if-error=86400`, `cf-cache-status` HIT/UPDATING | 12.1 s (11.9–27.8) | 75 ms |
| `api.thescore.com/nhl/box_scores/{id}` | same, `Age` 0–59 | 12.0 s | 77 ms |
| `api-secure.sports.yahoo.com/v1/editorial/s/scoreboard?leagues=nhl&date=…&format=json` | `public,max-age=20,stale-while-revalidate=300`, `Age` 0–25 | **25.0 s** | 183 ms |
| `api.foxsports.com/bifrost/v1/nhl/event/{id}/data?apikey=…` | `public, max-age=5`, Varnish | not polled over time (943 KB per hit) | — |
| `statsapi.mlb.com/api/v1.1/game/{pk}/feed/live` (archive game) | `max-age=10, public, stale-while-revalidate=30, stale-if-error=86400`, `via: 1.1 google, 1.1 varnish`, `Age` 0–10 | origin: `metaData.wait: 10`; in-game timestamps every 17 s p50 (see §5) | 764 KB |

### 1c. Clock and state fields

- NHL `clock.timeRemaining`/`secondsRemaining` on landing/boxscore/score/pbp is a **20-second snapshot**, not a running
  clock: 33 changes while running, every 20.0 s, jumping 9–32 game-seconds each time. `clock.running` therefore flips
  with up to 20 s of quantisation (15 flips observed, all on the 20-s grid). Landing, boxscore, pbp and `/score/{date}`
  carry the same version within ≤1.5 s of each other (same ETag family), so one of them is enough.
- For the same clock value, ESPN's `displayClock` appeared **+12.4 s** (p50, n=21) after the NHL landing on the
  summary, **+6.7 s** on the scoreboard, **−0.1 s** via FastCast patches (`/header/competitions/0/status/displayClock`,
  38 patches in 18.5 min). theScore box clock +13 s, Yahoo +21.6 s (n=5–6).
- `gameState` sequence observed: `LIVE` → `CRIT` (final minutes) → `FINAL` (04:44:01.7Z on `/score`) → `OFF`. ESPN's
  scoreboard went "End of 3rd Period" at 04:43:50 and `Final/post` at 04:44:20 (18 s after the NHL); Yahoo "Final"
  04:44:12; theScore `final` 04:44:27.
- Stoppage reasons that arrived live in `plays[].details` (gamecenter pbp): `visitor-timeout`,
  `goalie-stopped-after-sog` (+`secondaryReason: tv-timeout`), `icing` ×3, `puck-in-netting` (+`tv-timeout`),
  then `period-end` and `game-end`. **`secondaryReason == "tv-timeout"` is the commercial flag**, published together
  with the stoppage row. `situationCode` stayed `1551` (5v5) → no power play in the window, so PP start/end could not
  be timed tonight; the field is `situationCode` on every play plus `situation{homeTeam,awayTeam}.situationDescriptions`
  on landing (null at even strength, as seen all night).
- ESPN NHL play ids are `{espnEventId}0000{nhlEventId}0` (e.g. `401892643000009760` ↔ eventId 976) and ESPN play
  `clock` is elapsed time = NHL `timeInPeriod`: ESPN is downstream of the same NHL feed, so it can never lead it except
  through the NHL CDN's 20-s cycle (it did once tonight: goal 977 was on ESPN core 18 s before the gamecenter pbp).

### 1d. Event-by-event (live-entered events; UTC times; "fc" = FastCast arrival)

| eventId | type / reason | P3 clock | `utc` | wsc | gamecenter pbp | ESPN core | FastCast | ESPN summary |
|---|---|---|---|---|---|---|---|---|
| 977 | goal | 09:34 | 04:24:18 | 04:24:44 | 04:25:01 | 04:24:43 | (pre-sub) | 04:24:58 |
| 515 | stoppage visitor-timeout | 09:34 | 04:24:19 | — | 04:25:21 | 04:25:24 | (pre-sub) | 04:25:48 |
| 978 | faceoff | 09:34 | 04:25:58 | 04:26:14 | 04:26:21 | 04:26:27 | (pre-sub) | 04:26:48 |
| 516 | stoppage goalie-stopped, **tv-timeout** | 12:11 | 04:28:34 | — | 04:29:01 | 04:28:55 | (pre-sub) | 04:29:08 |
| 1053 | faceoff (after TV timeout) | 12:11 | 04:30:46 | 04:31:02 | 04:31:01 | 04:31:10 | 04:31:06 | 04:31:30 |
| 517 | stoppage icing | 13:21 | 04:31:57 | — | 04:32:41 | 04:32:45 | 04:32:39 | 04:33:01 |
| 1072 | faceoff | 13:21 | 04:32:36 | 04:32:44 | 04:33:01 | 04:33:06 | 04:33:00 | 04:33:21 |
| 518 | stoppage puck-in-netting, **tv-timeout** | 14:26 | 04:33:41 | — | 04:34:21 | 04:34:19 | 04:34:12 | 04:34:31 |
| 1088 | faceoff (after TV timeout) | 14:26 | 04:35:50 | 04:35:59 | 04:36:01 | 04:36:16 | 04:36:10 | 04:36:31 |
| 1002 | stoppage goalie-stopped | 15:04 | 04:36:28 | — | 04:37:01 | 04:36:58 | 04:36:51 | 04:37:11 |
| 519 | faceoff | 15:04 | 04:37:20 | 04:37:32 | 04:37:41 | 04:37:49 | 04:37:43 | 04:38:03 |
| 520 | stoppage icing | 15:48 | 04:38:04 | — | 04:38:21 | 04:38:31 | 04:38:24 | 04:38:43 |
| 1105 | faceoff | 15:48 | 04:38:37 | 04:39:00 | 04:39:01 | 04:39:03 | 04:38:55 | 04:39:13 |
| 521 | stoppage icing | 17:30 | 04:40:19 | — | 04:40:41 | 04:40:49 | 04:40:39 | 04:41:03 |
| 1127 | faceoff | 17:30 | 04:41:05 | 04:41:14 | 04:41:21 | 04:41:19 | 04:41:21 | 04:41:45 |
| 522 | period-end | 20:00 | 04:43:36 | 04:43:44 | 04:44:01 | 04:43:58 | 04:44:02 | 04:44:15 |

(Stoppage rows are absent from the wsc list because that feed does not carry `stoppage` events; it carries
faceoff/shots/hits/goals/penalties/period markers with `utc`. Pair it with the gamecenter pbp for `reason`.)
Full 52-row table: `raw/analysis_final.txt` section 2.

---

## 2. Ranked list (impact ÷ effort, legal for personal use)

1. **NHL `api-web.nhle.com/v1/wsc/play-by-play/{id}` at 3 s** — median 16.7 s behind the whistle, 26 s ahead of the
   current ESPN summary poll, carries per-event `utc`, `secondsRemaining`, `situationCode`, `homeTeamDefendingSide`;
   the repo already has this URL in `redzone/sources/leagues.py`. Free. **OK-personal**, NO-commercial (NHL ToS;
   api-web's own robots.txt allows `*`). Effort: ~2 h (promote it to the primary NHL source, add `utc`).
2. **NHL `…/gamecenter/{id}/play-by-play` at 2 s for `stoppage.details.reason/secondaryReason`** (tv-timeout, icing,
   offside, puck-frozen, visitor/home-timeout, goalie-stopped) and `period-end`/`game-end`/`gameState` — 27.7 s median,
   still 14 s ahead of ESPN summary, and the only free source of the stoppage *reason*. Same legality. ~2 h.
3. **ESPN FastCast for NHL (topic `gp-hockey-nhl-{id}`, verified live tonight)** — 18.6 s ahead of the polled summary,
   patches every 5.2 s, `plays` is a plain list (no `$key` refs for NHL), adds arrive as `add /plays/-` and
   `replace /plays/N/{id,text,wallclock,clock/displayValue,...}`; status patches `/header/competitions/0/status/*`.
   Gray-personal under Disney ToU (same as the summary); keep as the ESPN-side source. ~1 h (flag on + NHL parse check).
4. **MLB `statsapi.mlb.com/api/v1.1/game/{pk}/feed/live` at 10 s (honour `metaData.wait`)** — origin feed, pitch
   `startTime/endTime`, `linescore.inningState` Middle/End, `playEvents.details.event` (Mound Visit, Pitching
   Substitution, Batter Timeout, Game Advisory, Status Change…), `details.violation.type` (`pitcher_pitch_timer`,
   `batter_timeout`), `gameData.review`/`absChallenges`/`moundVisits`. **OK-personal, non-bulk** per MLBAM's
   copyright.txt. In-game update cadence from the Oct 28 WS game: 440 timestamps, every 17 s p50 (6–34 s p10–p90),
   1.7 updates per pitch. ~4 h; nothing to measure live until spring training.
5. **MLB push: `wss://ws.statsapi.mlb.com/api/v1/game/push/subscribe/gameday/{gamePk}`** — exists, no auth, Cloudflare
   front, HTTP/1.1 upgrade → `101 Switching Protocols` verified; for a finished game the server sends one text frame
   "Game is not available for subscription at this time." and closes with **code 4400** (4200 = game final, per the
   community Go client that also documents the `"Gameday5"` keep-alive every 10 s and a `{updateId}` push that is
   followed by `feed/live/diffPatch?startTimecode=…`). Same legality as statsapi. Verify live in Feb 2027.
6. **ESPN core `plays` as a cheap cross-check** — median equal to gamecenter pbp (±), 15.5 s ahead of the summary;
   700 KB per hit, so only if bandwidth is fine. Gray-personal.
7. Not worth it (slower or forbidden): ESPN "fauxcast" snapshots (~60 s), Yahoo scoreboard (25-s versions, ToS NO),
   theScore (12-s versions but robots `Disallow: /` + ToS NO), FOX bifrost (5-s TTL, 943 KB, robots `Disallow: /` +
   ToU NO), CBS (React live app, ToU NO), Google (no public endpoint, ToS NO), NHL EDGE (season aggregates only).

---

## 3. Candidate table

| # | Source / endpoint | Gives | Measured latency (behind `utc`) | Cadence | Free | Legal: personal / commercial | Status |
|---|---|---|---|---|---|---|---|
| A | NHL `GET https://api-web.nhle.com/v1/wsc/play-by-play/{gameId}` (top-level JSON **list**; fields `id, eventId, period, timeInPeriod, secondsRemaining, situationCode, typeCode, typeDescKey, homeTeamDefendingSide, sortOrder, utc(ms)`, plus `xCoord/yCoord/zoneCode/reason` on shots) | faceoff restart, goals, shots, penalties, period markers, exact event wall time | **16.7 s p50** (8.6–38.3) | 3–48 s, many origin BYPASS hits | yes | **OK-personal** (gray: NHL ToS forbids "unauthorized … automated means"; robots.txt on the API host: `User-agent: * / Allow: /`) / **NO** without an NHL data licence | measured live |
| B | NHL `…/v1/gamecenter/{gameId}/play-by-play` (`plays[].typeDescKey`, `details.reason`, `details.secondaryReason`, `situationCode`, top-level `clock`, `gameState`) | stoppage **reason** incl. `tv-timeout`, intermission (`period-end`), reviews (not seen tonight), PP via `situationCode` | 27.7 s p50 (18.3–43.6) | 20.0 s fixed | yes | as A | measured live |
| C | NHL `…/gamecenter/{id}/landing`, `/boxscore`, `/v1/score/{date}` (`clock.running`, `inIntermission`, `situation`, `summary.scoring/penalties`) | coarse live/dead flag, PP descriptions, goal clips | same 20-s cycle as B; clock is a 20-s snapshot | 20.0 s | yes | as A | measured live |
| D | ESPN FastCast `wss://…/FastcastService/pubsub/profiles/12000` topic `gp-hockey-nhl-{espnId}` / `gp-baseball-mlb-{espnId}` (from `summary.meta.gp_topic`) | the ESPN summary document, pushed | 25.3 s p50 (17.7–35.4 p75) | 5.2 s | yes | gray-personal (Disney ToU §x) / NO | NHL topic verified live; MLB topic name verified on a final game only |
| E | ESPN `site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event=` (today's source) | plays with `wallclock` (= NHL `utc`), status | **43.1 s p50** (34.8–56.6) | 10.0 s | yes | gray-personal / NO | measured live |
| F | ESPN `sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/events/{id}/competitions/{id}/plays?limit=1000` | plays + `modified` | 27.5 s p50 | 9–63 s | yes | gray-personal / NO | measured live |
| G | MLB `statsapi.mlb.com/api/v1.1/game/{pk}/feed/live` (GUMBO) | everything in §5 | n/a live (off-season); origin cadence 17 s p50 per `timestamps` | `metaData.wait: 10`, CDN `max-age=10` | yes | **OK-personal, non-bulk** (copyright.txt) / NO without MLBAM authorisation | probed |
| H | MLB `…/feed/live/diffPatch?startTimecode=YYYYMMDD_HHMMSS[&endTimecode=…]` | JSON-Patch list `[{diff:[{op,path,value}…]}…]`; falls back to the full GUMBO when it cannot diff | — | — | yes | as G | probed (range form returned 3 diff groups of 32/39/82 ops; start-only on a final game returned the full 764 KB feed) |
| I | MLB `…/feed/live/timestamps` | list of every GUMBO version stamp (`20261029_031311`) | — | — | yes | as G | probed |
| J | MLB `wss://ws.statsapi.mlb.com/api/v1/game/push/subscribe/gameday/{gamePk}` | push `{updateId}` → diffPatch | unverified live | — | yes | as G (no robots.txt: 404 JSON; `/` is an Okta login page) | handshake verified, close 4400 off-season |
| K | theScore `api.thescore.com/nhl/events/{id}/play_by_play_records`, `/nhl/box_scores/{id}` (`progress.clock`, `team_on_power_play`, `under_review`, `home/away_strength`) | pbp, PP flag, review flag | 26.4 s p50 | 12 s | yes | **NO** (robots `Disallow: /`, ToU "spiders, crawlers, robots") | measured live |
| L | Yahoo `api-secure.sports.yahoo.com/v1/editorial/s/scoreboard?leagues=nhl&date=YYYY-MM-DD&format=json` (`games.*.status_display_name`, `power_play_strength_*`, `gamelast_play`) | score/clock/last play | 28.1 s p50 | 25 s | yes | **NO** (ToS: no automated means "for any purpose without our express, prior permission") | measured live |
| M | FOX `api.foxsports.com/bifrost/v1/nhl/league/scores-segment/{YYYYMMDD}` and `/nhl/event/{id}/data` (`statusLine2` clock, `pbp.sections`) with the static `apikey` embedded in foxsports.com JS (`jE7yBJ…`) | scoreboard, pbp, clock | not timed; `max-age=5`, 943 KB | 5 s TTL | yes | **NO** (robots `Disallow: /`; ToU "may not … scrape") | probed |
| N | CBS Sports live scoreboard (`sportsfly.cbsistatic.com/…/live-app-scoreboard-nhl.js`, React/Redux, polling transports) | — | — | — | — | **NO** (Paramount ToU) | not pursued |
| O | Google | no public live-sports endpoint | — | — | — | NO (ToS) | n/a |
| P | NHL EDGE `api-web.nhle.com/v1/edge/{skater,goalie,team}-…/{id}/now` → 307 → `/{season}/{gameType}` (`max-age=0/1`) | season aggregates (shot/skating speed, zone time, `gameDate` of last game) | not live | daily | yes | as A | probed: **no per-game or live tracking endpoint** |
| Q | NHL websocket | none found: nhl.com gamecenter loads 5 bundles (3.5 MB) with zero `wss://`/WebSocket references and polls `api-web`; the EDGE web app (`wsr.nhle.com`, `/config` is a 403 S3 object) has none either; mobile app not inspected | — | — | — | — | not found |
| R | Sportradar NHL/MLB push feeds (`developer.sportradar.com/ice-hockey/docs/nhl-ig-push`) | official licensed push | — | — | **no** | what a product would buy | reference only |

---

## 4. Terms quoted (fetched 2026-11-04; files in `research/live/tos/`)

**MLB (statsapi.mlb.com).** Every statsapi response carries
`"copyright": "Copyright 2026 MLB Advanced Media, L.P.  Use of any content on this page acknowledges agreement to the terms posted here http://gdx.mlb.com/components/copyright.txt"`.
That file (473 bytes, HTTP 200) reads in full:
> "The accounts, descriptions, data and presentation in the referring page (the "Materials") are proprietary content of MLB Advanced Media, L.P ("MLBAM"). Only individual, non-commercial, non-bulk use of the Materials is permitted and any other use of the Materials is prohibited without prior written authorization from MLBAM. Authorized users of the Materials are prohibited from using the Materials in any commercial manner other than as expressly authorized by MLBAM."

MLB.com Terms of Use (`mlb.com/official-information/terms-of-use`) add, in the conduct list: "(xi) use automated scripts
to collect information from or otherwise interact with the MLB Digital Properties" and "Except for downloading one copy
of the MLB Digital Properties on any single device for your personal, non-commercial home use, you must not reproduce…".
`statsapi.mlb.com/robots.txt` → 404 (`{"error":"Not Found"…}`); `mlb.com/robots.txt`: `User-agent: * / Disallow: /api/`.
**Verdict: OK-personal** (one household, live games only, no season mirroring = "non-bulk"); **commercial: NO** without MLBAM.

**NHL (api-web.nhle.com).** `nhl.com/info/terms-of-service`: "You may access, use, and display the Services, but only
for non-commercial, informational, personal use"; prohibited: "Engage in unauthorized spidering, scraping, or harvesting
of content or information, or use any other unauthorized automated means to compile information"; "provided for your
private, non-commercial use, and you may not distribute, modify, translate, rebroadcast, transmit, perform, or create
derivative works of them". `api-web.nhle.com/robots.txt` (Cloudflare content-signals file):
`User-agent: * / Content-Signal: search=yes,ai-train=no,use=reference / Allow: /` (training crawlers listed separately
with `Disallow: /`). `nhl.com/robots.txt`: `User-agent: * / Allow: /`.
**Verdict: OK-personal** (the API host's robots explicitly allows all agents; the ToS "unauthorized automated means"
clause is the gray part — stay polite, one household); **commercial: NO** (licensed via Sportradar et al.).

**ESPN (site/core API, FastCast).** Disney Terms of Use: "we grant you a limited, non-exclusive, non-sublicensable,
non-transferable license to access and use … for your personal, noncommercial use only"; you may not "viii. use the
Disney Products for any commercial or business-related use or build a business utilizing the Disney Products" nor
"x. access, monitor, copy or extract the Disney Products using a robot, spider, script, or other automated means,
including … data mining or web scraping or otherwise compiling … any collection of data". `site.api.espn.com/robots.txt`
and `sports.core.api.espn.com/robots.txt` return 403. **Verdict: gray-personal** (as today), **commercial: NO**.

**theScore.** ToU (Score Media and Gaming Inc., "Last Revised: January 1, 2023", via the Zendesk page behind a
Cloudflare challenge): licence "for your personal, non-commercial use only"; you shall not "use spiders, crawlers,
robots or any other similar means to access the Service or substantially download, reproduce or archive any portion of
the Service" nor "take any action that imposes an unreasonable or disproportionately heavy load". `api.thescore.com/robots.txt`:
`User-Agent: * / Disallow: /`. **Verdict: NO.**

**FOX Sports.** ToU: "Fox grants you a non-exclusive limited license to use the Fox Services … for personal,
non-commercial purposes only"; "you may not copy, download, stream, scrape, capture, reproduce, duplicate, archive …
any Content". `api.foxsports.com/robots.txt`: `User-agent: * / Disallow: /`. **Verdict: NO.**

**Yahoo.** Terms of Service: you may not "access or collect data, or attempt to access or collect data, from our
Services using any automated means, devices, programs, algorithms or methodologies, including but not limited to robots,
spiders, scrapers, data mining tools, or data gathering or extraction tools, for any purpose without our express, prior
permission"; "you may not access or reuse the Services, or any portion thereof, for any commercial purpose".
`sports.yahoo.com/robots.txt`: `User-agent: * / Disallow: /_x_/ /_xhr/ /site/ …`. **Verdict: NO.**

**CBS Sports (Paramount ToU, effective May 27 2025).** "You are only permitted to access and view the Content for
personal, non-commercial purposes … and may not build a business or other enterprise utilizing any of the Content";
prohibited: "Engage in unauthorized spidering, 'scraping,' data mining or harvesting of Content, or use any other
unauthorized automated means to gather data from or about the Services"; "4.2 Commercial Licenses You must obtain our
written permission for commercial use". `cbssports.com/robots.txt`: `User-agent: * / Disallow: /data/* …`. **Verdict: NO.**

**Google.** Terms of Service: don't use "automated means to access content from any of our services in violation of
the machine-readable instructions on our web pages (for example, robots.txt files that disallow crawling, training, or
other activities)". No public live-sports API exists. **Verdict: NO / n/a.**

**Real (realapp.com):** excluded per brief (ToS forbids automated access). **Pirated streams:** excluded.

---

## 5. MLB specifics (statsapi.mlb.com, probed on gamePk 849799 = WS G5 Oct 28/29 and 9 regular-season games of Sep 20)

- **Feed:** `GET /api/v1.1/game/{pk}/feed/live` → 764 KB; `metaData: {wait: 10, timeStamp: "20261029_031311",
  gameEvents: [...], logicalEvents: [...]}`. `wait: 10` is Gameday's own poll hint (10 s); CDN `cache-control:
  max-age=10, public, stale-while-revalidate=30, stale-if-error=86400` (Fastly behind Google front, `Age` 0–10).
- **Cadence:** `/feed/live/timestamps` returned 445 stamps; between first pitch (00:08:57Z) and last play (03:13:11Z):
  440 versions, gaps p10 6 s / p25 10 / **p50 17** / p75 23 / p90 34 / max 312 s; 255 pitches → 1.73 versions per pitch.
  So the origin publishes roughly on every pitch plus every count/baserunner change; the public cache adds ≤10 s.
- **diffPatch:** `?startTimecode=A&endTimecode=B` → list of `{diff:[{op:"replace",path:"/metaData/timeStamp",…},
  {op:"add",path:"/metaData/gameEvents/0",value:"called_strike"}, {path:"/liveData/plays/allPlays/32/about/endTime"}…]}`
  (3 groups, 24 KB). `?startTimecode=` alone on the finished game returned the full feed every time (documented
  fallback when the server cannot diff). `feed/live?timecode=…` returns the historical snapshot (495 KB).
- **Websocket:** `wss://ws.statsapi.mlb.com/api/v1/game/push/subscribe/gameday/{gamePk}`; DNS → Cloudflare
  (`ws.statsapi.mlb.com.cdn.cloudflare.net`); `curl --http1.1` upgrade → `HTTP/1.1 101 Switching Protocols`; python
  `websockets` connect 0.29 s, then close **4400 "Game is not available for subscription at this time."** (game final).
  Community client (`scm.dairydemon.net/filifa/mlbstats`, `cmd/websocket.go`): `GameFinalCode = 4200`,
  `GameUnavailableCode = 4400`, push struct `{UpdateId string}`, keep-alive text `"Gameday5"` every 10 s, and on each
  push fetch `diffPatch?startTimecode=<last>`. `https://ws.statsapi.mlb.com/` itself serves an Okta "Please Login" page.
- **Pitch clock:** there is **no countdown/timer field** in GUMBO. The clock shows up only as outcomes: pitch
  `details.call.code` `VP` "Automatic Ball - Pitcher Pitch Timer Violation", `AB` "Automatic Strike - Batter Timeout
  Violation", `VB` "Automatic Ball - Intentional", with `details.violation: {type: "pitcher_pitch_timer" | "batter_timeout",
  description, player}`; `playEvents[].type` `stepoff` ("Pitcher Step Off") and `pickoff` count disengagements. "Next
  pitch" = `playEvents[i+1].startTime` once it exists; between pitches the feed is silent, so the pitch is predicted
  from `startTime` of the last pitch + 15/18 s (+ `count`/runners), not observed.
- **Breaks/stoppages:** `liveData.linescore.inningState` ∈ Top/Bottom/**Middle/End**; actions `playEvents[].details.event`:
  `Mound Visit` (44 in 9 games), `Pitching Substitution` (52), `Batter Timeout` (164), `Defensive Switch/Sub`,
  `Offensive Substitution`, `Game Advisory` with descriptions "Status Change - Warmup/In Progress", "On-field Delay.",
  "Injury Delay.", "Status Change - Delayed Start: Rain"; `gameData.moundVisits{away,home}{used,remaining}`,
  `gameData.review{hasChallenges, away/home{used,remaining}}`, `gameData.absChallenges{…usedSuccessful/usedFailed/remaining}`
  (ABS challenge system, 2026), per-pitch `details.hasReview`.
- **ESPN MLB:** summary plays carry `pitchCount`, `atBatId`, `type.text` e.g. "End Inning", `wallclock`; FastCast topic
  `gp-baseball-mlb-{espnId}` (`meta.gp_topic` on event 401908046). Not measured live.

---

## 6. Integration steps (for Nick to do in the repo later; nothing changed tonight)

1. **Make `/v1/wsc/play-by-play/{id}` the primary NHL event source at a 3-s cadence** (about 2 h). Parse the top-level
   list; key by `eventId`; keep `utc` as the event's true time and log `arrival − utc` per event into
   `data/recordings/<day>/plays.jsonl` so the lag model has a reference. Send `If-None-Match` with the last ETag to
   cut the 100-KB body on unchanged versions. Treat `typeDescKey == "faceoff"` as the restart marker (it arrives
   ~17 s after the drop, so it confirms, never triggers, an entry).
2. **Poll `/gamecenter/{id}/play-by-play` every 2 s for stoppage semantics** (about 2 h): on a new `stoppage` row read
   `details.reason` (icing, offside, puck-frozen, goalie-stopped-after-sog, puck-in-netting/benches/crowd,
   visitor-/home-timeout) and `details.secondaryReason == "tv-timeout"` → commercial of ~120 s starting at `utc` of the
   stoppage; `period-end` → intermission (18 min); `game-end`; `gameState` `CRIT` as the crunch-time flag; `situationCode`
   change away from `1551` → PP/EN start, back to `1551` → end. Expect the row 18–44 s after the whistle, i.e. roughly
   when it reaches the YTTV screen; so for CUT decisions pair it with the wsc `shot`/`faceoff` stream (a wsc faceoff
   always follows a stoppage; a gap in wsc events plus a stopped `secondsRemaining` is the earliest dead-ball hint).
3. **Stop using the landing `clock` as a timing signal**: it is a 20-s snapshot (`running` flips land on the 20-s grid).
   Use it only as the live/dead sanity flag. Derive game-clock↔wall-clock from wsc `secondsRemaining` + `utc` per event.
4. **Turn FastCast on for NHL** (`sources.fastcast: true`; ~1 h to confirm `to_summary()` handles the plain `plays` list
   and `add /plays/-` + `replace /plays/N/*` ops that the NHL topic uses; replays on subscribe were 0 tonight, checkpoint
   441 KB). It is the fastest *ESPN-side* path (−18.6 s vs the summary) and gives `displayClock` at the NHL's own cadence.
5. **MLB (before April):** poll `feed/live` every 10 s (`metaData.wait`), diff on `metaData.timeStamp`, and read
   `inningState`, actions and `violation`; then try the websocket (keep-alive `Gameday5`/10 s, expect `{updateId}`, call
   `diffPatch?startTimecode=<last timeStamp>`; handle close 4200/4400 by falling back to polling). Measure
   `arrival − playEvents[].endTime` the same way as tonight.
6. **Lag book per source per night** (reuse tonight's `scripts/analyze.py` logic): report p10/p50/p90 of `arrival − utc`
   for wsc, gamecenter, FastCast, summary; the switch logic should subtract the per-source median from its stream-delay
   estimate (with wsc at ~17 s the data leads a 22–26-s YTTV stream by 5–9 s; with the summary it trails by ~17–21 s).
7. **Product path (later):** none of the above is licensable as-is. A commercial version needs NHL data under licence
   (Sportradar's NHL push feeds, `developer.sportradar.com/ice-hockey/docs/nhl-ig-push`, or Genius/Stats Perform), MLB
   under MLBAM written authorisation, and must drop ESPN/theScore/FOX/Yahoo/CBS entirely.

---

## 7. Method notes and gaps

- Polite rates: 2 s (landing, pbp, ESPN summary), 3 s (boxscore, wsc, ESPN scoreboard, core), 5 s (`/score`), 4–6 s
  (theScore), 5 s (Yahoo); every response was 200; bodies saved only when the SHA-1 changed.
- "First seen" excludes everything present in the initial snapshot; FastCast first-seen is seeded from its checkpoint
  so re-sort patches of old plays do not count.
- Not measured tonight: a power play (none occurred in the window), a review, an intermission start (the game ended
  instead; `period-end` → `game-end` was 20 s apart on the NHL feed), MLB anything live, the NHL mobile app's transport.
- The absolute numbers depend on the NHL scorer's `utc` being ~the whistle; a cross-check using the running 20-s clock
  snapshots gave wsc +5.5/+9.9 s (p10/p25) and summary +23/+36 s, consistent with the `utc`-based table given the
  snapshot's ±20-s quantisation.
- Scripts: `scripts/poll_nhl_vs_espn.py`, `scripts/fastcast_record.py`, `scripts/poll_others.py`, `scripts/analyze.py`
  (all under the `research/live/` directory named at the top).
