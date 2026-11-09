---
id: D-PRIORITY
title: "RedZone, viewers and video production: what real data exists against 250M per area"
date: 2026-11-09
stream: data
author: "Claude Opus 5.5 (data research agent)"
status: draft
supersedes: []
sources: 74
verified: 53
affects: []
gate: []
---

# RedZone, viewers and video production: what real data exists

No dataset was downloaded. Sizes come from each source's own page, API metadata, or the paper. Page text was
treated as data. Claims marked [snippet] were seen only in a search result, not on the opened page. No text
is quoted from any source; everything is paraphrased.

## Bottom line against 250 million

| Area | True total that passes the licence check | Against 250M | Honest reading |
|---|---|---|---|
| 1. How RedZone does it | **0** public records of what aired | 0% | No episode log, cut list or per-show stats table exists in anything searched |
| 2. Consumer preferences | **153,801,332,683** rows (Wikimedia pageviews 153,479,054,298 + KuaiRand-27K 322,278,385) | Met, 615x | Almost none of it is sports TV. Open sports audience data is 48 rows |
| 3. Video production | **1,108,754** labels with a licence tag (MIT tags on repos may cover code only); **1,606,813** adding labels with no stated licence | 0.44% to 0.64% | 250M does not exist here for labels. Frames would inflate it; not counted |

The 250M target is reachable only for general attention and short-video behaviour. Nothing with a clean licence
shows a person watching live US sports minute by minute.

## Area 1: how NFL RedZone does it

### Summary table

| Dataset | True data points | License OK | Kaggle fetch OK | Brain task |
|---|---|---|---|---|
| Public log of RedZone cuts or episodes | **0 found** | n/a | n/a | n/a |
| 300th-episode graphic (press) | 2 figures: 17,800 touchdowns, 2,000+ hours since 2009 | Facts, usable | n/a | Cut pacing (sanity check only) |
| Repo 19-Sunday measurement (R-NUMBERS) | 92 window-hours; about 3,275 events (35.6 per hour x 92, my arithmetic) | Internal, ESPN personal use | Only as our own derived file | Cut pacing |
| nflverse play-by-play 1999-2025 (catalog) | Row count not on page; about 0.5 GB; **not counted** | Yes, CC-BY-4.0 | Yes | Moment value, reconstruction |

### What the show does, from people who were in the room

Control room and roles:
- 2014: lead producer Brian Nettles called the show, a director turned his calls into cues, and at least seven
  other producers watched games and replays, looked up stats and checked rules. At least 59 monitors, four
  replay banks ([NPR via KBIA](https://www.kbia.org/sports/2014-12-15/seven-chaotic-hours-behind-the-scenes-at-nfl-redzone), opened).
- Earlier Grantland account: one producer in the chair directs and is the only voice in Hanson's ear; the other
  is a co-pilot who watches every game and proposes the next one. They split the day at about 3.5 hours. Roughly 15
  editorial and research staff prepare all week ([Grantland](https://grantland.com/features/watching-day-nfl-games-redzone-studio/), opened).
- Hanson asks questions and suggests shots, so the host also produces (same source). He reduces 200 to 300 pages of notes to
  about 20 ([Boardroom](https://boardroom.tv/scott-hanson-nfl-redzone-behind-the-scenes/), opened).
- An 8 a.m. meeting covers storylines (NPR). Production stayed with the NFL after ESPN's deal closed in January 2026
  ([Barrett Media](https://barrettmedia.com/2026/09/04/scott-hanson-nfl-redzone-espn-deal/), [snippet]).

What triggers a cut:
- No written rulebook is public. The rules below are inferred from accounts.
- Switch when a team reaches the red zone, for a score, a strong drive, a close game or a key replay. Go to split
  screens of two to ten games when several are about to score. Late in the day one-possession games (within 8 points) get
  priority ([Wikipedia](https://en.wikipedia.org/wiki/NFL_RedZone), opened, secondary).
- The stated goal is every touchdown plus anything viewers will talk about tomorrow; pre-game plans are held loosely (Boardroom).
- The direct competitor, DirecTV's Red Zone, favoured scores, drives into the red zone, two-point tries and close
  games, and skipped punts ([SI 2016](https://www.si.com/nfl/2016/11/30/nfl-directv-red-zone-channel-andrew-siciliano-embed), opened).
- DirecTV's room also ran a GSIS scoreboard that changes colour by state: red for red zone, green for touchdown, black for game over
  ([Gear Patrol](https://www.gearpatrol.com/?p=274067), opened).

Timing:
- Up to three switches a minute at peak; all 64 touchdowns shown on the Dec 7 2014 show (NPR, 2014, may be dated).
- A touchdown that is missed live needs about 16 seconds of rollback, effect and timing (NPR).
  Some scores air 30 to 60 seconds late (Wikipedia).
- Grantland describes switching at the last possible moment.
- Measured by us: median 5 switches needed per 10 minutes, 9 at p90, 14 at most, 10.1 touchdowns per hour; RedZone's lifetime figure is
  about 8.9 per hour (R-NUMBERS, `docs/research/2026-11-08/redzone-numbers.md`, checked in on other branches).

Octobox and per-season stats:
- The Octobox shows up to eight games (Wikipedia, Grantland). A [Front Office Sports](https://frontofficesports.com/article/scott-hanson-agrees-to-long-term-deal-for-nfl-redzone/) 300th-episode interview reportedly covers how it is produced, and Hanson's Oct 30 2026 Up & Adams interview exists; neither text was readable, so no production detail from them is used [snippet].
- No season table is published. The one per-season figure found is a 2020 Drew Brees touchdown as the 1,087th shown, then a record ([NFL.com](https://www.nfl.com/videos/brees-td-breaks-nfl-redzone-record-for-most-shown-in-season), [snippet]).

Commercials and overlaps:
- 2025 added 15-second split-screen spots. Hanson says they run in lulls, while teams walk back to the huddle
  ([Yahoo](https://sports.yahoo.com/articles/scott-hanson-reveals-nfl-redzone-214429018.html), opened).
- Week 1 2026: four spots, about 9 seconds per hour. Nov 30 2025: eight 15-second spots plus eight 20-second banners
  ([Stacker/KEYT](https://keyt.com/stacker-football/2026/10/21/nfl-redzone-hits-300-watching-fewer-commercials-doesnt-mean-seeing-fewer-ads/), opened, citing SBJ).
  Awful Announcing reports a higher afternoon total that does not reconcile ([snippet](https://awfulannouncing.com/nfl/redzone-ups-ad-load-airs-16-spots.html)).
- Season reach: 20.3M US households in 2025, Samba TV estimate (Stacker). No per-game or minute audience is public.

### Equivalents

- **NBA CrunchTime:** producer Bert Bondi, director Alisa Deanes-Davis, tape producer Dan Eisner. League contracts cap live visits per game,
  first at three, later five: about one per quarter and one held for the last two minutes. One-play highlights are
  unlimited ([Awful Announcing](https://awfulannouncing.com/league-networks/bert-bondi-nba-crunchtime-redzone-production-team.html), opened).
  Crew shares a Slack channel; when few games are close it shifts to stat lines and betting angles
  ([Uproxx](https://uproxx.com/dimemag/nba-crunchtime-jared-greenberg-interview-app), opened). [NBA.com](https://www.nba.com/news/crunchtime-shows-the-top-nba-moments-of-the-night-as-they-happen) (opened) gives only general format.
- **NBC Gold Zone:** PCR12 control room at Stamford ([SVG](https://www.sportsvideo.org/2024/08/02/paris-2024-behind-the-scenes-at-nbc-sports-record-setting-at-home-operation-in-stamford), opened); up to 60 feeds [snippet].
- **Goal Rush / Goal Zone (Premier League):** Goal Rush is Peacock's whip-around ([NBC release](https://www.nbcsports.com/pressbox/press-releases/first-place-arsenal-host-manchester-united-this-sunday-jan-25-at-11-30-a-m-et-exclusively-on-peacock-headlining-this-weekends-premier-league-coverage), opened). Goal Zone's format is not described there.
- **ESPN Goal Line** ran 2010 to 2019 [snippet]. **Frozen Frenzy** and **Golazo Goal Rush:** no usable production detail found.
- **Sky/DAZN Konferenz:** the director picks which game is in focus and the conference commentator is audible only then [snippet, German press];
  the opened [Schwäbische](https://www.schwaebische.de/sport/ohne-die-konferenz-hat-sky-keine-zukunft-3124552) piece has no mechanics. No director interview found.

### Reconstruction and the public record

Could be rebuilt from play-by-play: every touchdown, turnover and red-zone entry, wall-clock overlaps, and the minimum switches
needed. R-NUMBERS already did this.

Cannot be rebuilt: actual cut times, dwell, box layouts, replay choices, ad slots, host lines.

**No public record of what aired was found.** Searched by API: Zenodo, Harvard Dataverse, Figshare, DataCite, Hugging Face,
Kaggle, OpenML, AWS Open Data registry, data.europa.eu. TVMaze search returned no NFL RedZone entry. The
[viral fanbase ranking](https://sports.yahoo.com/articles/nfl-fanbases-most-benefit-redzone-170022497.html) (opened) uses kickoff times, not air logs.
Video of shows exists but is copyrighted and is rejected by the brief.

Weak links: data.gov's CKAN endpoint returned 404; re3data returned only a registry list; Google Dataset Search was
not machine-readable; Academic Torrents returned a bot check.

## Area 2: consumer preferences

### Summary table

| Dataset | True data points | License OK | Kaggle fetch OK | Brain task |
|---|---|---|---|---|
| Wikimedia hourly pageviews | 153,479,054,298 rows over 24,795 hourly files, all wikis, only to about 2018; newer files exist, not totalled | Yes, CC0 | Yes | Moment value, viewer preference |
| KuaiRand-27K | 322,278,385 interactions (1,186,059 random-exposure) | Yes with attribution; ShareAlike to be safe | Yes, 9.9 GB | Viewer preference |
| Yambda-5B | 4,785,859,887 events | **Weak:** Apache-2.0 tag plus a research-only statement | Yes, 38.2 GB | Viewer preference |
| Puffer live TV telemetry | 1,904,316 streams in the 8-month trial; 2,829 daily folders to 2026-11-08 | **Not stated, ask first** | Yes, public bucket | Viewer preference, cut pacing |
| LiveRec Twitch | 124M interactions (catalog) | No licence stated | URL unconfirmed | Viewer preference |
| YTLive | 507,290 records (catalog) | None stated | Yes | Viewer preference |
| Mr. HiSum | 31,892 videos, labels from 50,000+ users each | **Academic non-commercial** | Yes | Moment value |
| CSO Ireland WMI11 | 48 rows, some blank | Yes, CC BY 4.0 | Yes | Viewer preference (league prior) |
| Wikipedia clickstream | 25M pairs for the single month the page cites | Yes, CC0 per analytics page | Yes | Viewer preference (weak) |

Only Wikimedia and KuaiRand-27K count toward the total. LiveRec and YTLive figures come from the catalog and were not re-opened today. Sports share of KuaiRand, Yambda and Puffer is not published.

### Notes per source

- **Wikimedia:** files are at `dumps.wikimedia.org/other/pageviews/YYYY/YYYY-MM/` and were live through Oct 2026
  ([index](https://dumps.wikimedia.org/other/pageviews/2026/2026-10/), opened). Licence is CC0 ([analytics](https://dumps.wikimedia.org/other/analytics/), opened). Row count is from
  [Google 2018](https://cloud.google.com/blog/products/gcp/bigquery-lazy-data-loading-sql-data-languages-ddl-and-dml-partitions-and-half-a-trillion-wikipedia-pageviews) (opened), which also gives unreconciled per-hour figures of 5M and 25M.
  Weak link: **the per-article API allows only daily or monthly**; I tested hourly and got a 400. Hourly per-article needs the dump files.
  One hour is too coarse for moments inside a game; it can rank games and teams.
  Fetch: stream each hour's `.gz`, keep sports titles only, drop the rest.
- **KuaiRand:** counts from [kuairand.com](https://kuairand.com/) (opened). The site says CC BY-SA 4.0 and the
  [Zenodo record](https://zenodo.org/records/10439422) (API opened) says CC BY 4.0; follow the stricter. It has real view time and play ratio, including random exposure, which removes
  recommender bias. It is short video in China, so it teaches keep-or-leave shape only.
- **Yambda:** [card](https://huggingface.co/datasets/yandex/yambda) (opened). Real skip and played-percent data; music, not sports. The Hugging Face total of 5.3B double counts the
  nested 50M and 500M samples; 4.79B is the true number. Nick should decide on the research-only wording before use.
- **Puffer:** the closest thing to a live TV viewing log, and the best find in this area. Stanford streamed six over-the-air channels to 63,508 users, 38.6 years of video by Feb 2020,
  with Super Bowl spikes over 20x baseline ([NSDI paper](https://www.usenix.org/system/files/nsdi20-paper-yan.pdf), opened). Rows carry `channel` and `session_id`, anonymised
  ([data description](https://puffer.stanford.edu/data-description/), opened). Bucket listing confirmed through 2026-11-08. No licence is on the page and it says a day is several GB, so fetch only NFL Sunday windows. Its purpose was
  network research, and sessions may end for reasons other than the game.
- **Mr. HiSum:** real YouTube replay curves, and its examples include goal compilations ([repo](https://github.com/MRHiSum/MR.HiSum), opened via API). The repo licence limits use to academic non-commercial. Whether personal model training qualifies is a judgement call for Nick.
- **CSO WMI11:** the only open licensed sports TV audience table found, Irish annual figures ([API](https://ws.cso.ie/public/api.restful/PxStat.Data.Cube_API.ReadDataset/WMI11/CSV/1.0/en), opened).

### Rejected

| Source | Why |
|---|---|
| Reddit (Pushshift dumps, game threads) | [Data API terms](https://www.redditinc.com/policies/data-api-terms) (opened) forbid training on user content without rightsholder permission; identifies people |
| X/Twitter sets on Kaggle (e.g. [UCL 2018 tweets](https://www.kaggle.com/datasets/xvivancos/tweets-during-r-madrid-vs-liverpool-ucl-2018), API listing only) | CC0 tag on Kaggle cannot override platform terms; contains identifiable accounts |
| Bluesky firehose dumps | Terms-of-service bound and withdrawn after user backlash [snippet] |
| HF Twitch-chat sets (e.g. 8,984,657 rows) | Provenance unknown; usernames possible |
| Google Trends | Official [API](https://developers.google.com/search/blog/2025/07/trends-api) (opened) is a limited alpha needing approval; daily at best; no terms for models stated |
| MovieLens 32M | 32M ratings ([page](https://grouplens.org/datasets/movielens/32m/), opened) but licence not on page and no sports value |
| Netflix Prize | Not opened; known re-identification history, avoid |

### What viewers do with game state (research, not datasets)

- EPL minute-by-minute audiences, close to 50,000 minute-observations: suspense, surprise and shock all move viewing
  ([Buraimo et al. 2020](https://econpapers.repec.org/RePEc:bla:ecinqu:v:58:y:2020:i:2:p:881-896), [snippet]). Data is not public.
- NBA: closeness adds modestly and mostly near the end, with diminishing returns; viewers fall away at halftime and at the end regardless
  ([Solow, Reilly, von Allmen 2024](https://iro.uiowa.edu/esploro/outputs/journalArticle/Within-game-uncertainty-of-outcome-and-the/9984963525302771), opened).
- 2026 hurling piece: minute-by-minute share rises and falls with uncertainty ([UCC](https://www.ucc.ie/en/business-school/newsandevents/2026/why-tv-viewers-crave-uncertainty-when-it-comes-to-hurling-matches.html), opened, summary only, no sample given).
- NFL playoffs: no uncertainty effect in local markets, some preference for even games outside them ([MetaTOC](https://metatoc.com/papers/9639-qualifying-the-game-uncertainty-effect-a-game-level-analysis-of-nfl-postseason-broadcast-ratings), [snippet]).
- Second screen, N = 630: not-close games drew more second-screen activity than close games [snippet].
- Excitement: the win-probability swing index sums absolute changes per play; the tension index asks how close a game stays to 50/50; they
  measure different things ([inpredictable](https://inpredictable.com/2016/06/inspired-by-last-nights-game-7-new.html), [snippet]; [Yale](https://sports.sites.yale.edu/node/566), [snippet]).
  Late NFL audience by quarter: not found, despite searching.

## Area 3: video production

### Summary table

| Dataset | True data points | License OK | Kaggle fetch OK | Brain task |
|---|---|---|---|---|
| SoccerNet-Echoes (Whisper v3, originals only) | 923,181 segments | Yes, CC BY 4.0 | Yes | Moment value |
| MovieCuts | 173,967 cuts, 10 types | Repo MIT; check | Likely; label location unconfirmed | Cut pacing |
| AutoShot / SHOT | 11,606 shot annotations | Repo MIT; check | Likely; label location unconfirmed | Cut pacing |
| Twitch-to-YouTube edit lists | 8 stream/edit pairs (kept spans not counted) | Yes, CC BY 4.0 | Yes | Cut pacing, moment value |
| SoccerNet-v2 labels | 301,883 (110,458 actions, 158,493 camera changes, 32,932 replays) | **Licence not stated** | Yes, ungated JSON | End-of-play timing, cut pacing |
| AVE | 196,176 shots, 1.5M+ tags | **No licence found** | Unconfirmed; paper links code only | Cut pacing |
| SoccerNet-Caption / MatchTime | almost 37k captions, 471 games | Weak (CC BY-SA on a re-host) | Yes | Moment value |
| InVID TV Logo v2.0 | 2,749 annotated videos, with shots | Yes, CC BY 4.0 | Yes (labels) | Graphics, score bug |
| ClipShots | Count not on page; excluded | Repo MIT; check | Unconfirmed | Cut pacing |
| VEBench | 3.9K videos, 3,080 QA pairs | No dataset licence | n/a | Cut pacing eval only |
| USL Broadcast camera selection | 13 games | Availability not stated | No | Cut pacing |

Rejected or skipped: `it-just-works/shot-boundary-detection` (3.4M clips but built partly on synthetic data, so not real data points);
SoccerReplay-1988, SN-BAS-2025 and SN-PCBAS-2026 (NDA or gated; Nick has not accepted anything); FOOTPASS, FineSports, NSVA and MultiSports
(sizes [snippet] only; not added); QVHighlights (counts and licence not confirmed on the page I could open).

### Notes per source

- **SoccerNet-v2** ([paper](https://ar5iv.labs.arxiv.org/html/2011.13367), opened): shots are 86.7% live, 10.9% replay of an action; replays average 6.8 seconds; transitions are 71.4% abrupt.
  That is the only open labelled record of how a real director moves from live play into a replay.
  Labels are public and ungated at [SN-Labels](https://huggingface.co/datasets/SoccerNet/SN-Labels) (API opened, 2,372 files). The GitHub MIT badge covers code. Video stays NDA and is not wanted.
- **SN-Echoes:** the [card](https://huggingface.co/datasets/SoccerNet/SN-echoes) reports 4,387,121 rows across three Whisper runs and their translations, which overlap.
  One real transcript run is 923,181. It is machine transcription of real commentary, tagged synthetic on the card; treat as weak labels.
- **Twitch EDLs** ([Zenodo](https://zenodo.org/records/23169164), API opened): tiny but the rare case of what a human kept from a live stream.
- **SoccerNet 2026 challenges** ([arXiv](https://arxiv.org/abs/2607.07320), opened) moved to ball-action anticipation and player-centric spotting; no sizes given and replay grounding is gone.
- **Gap:** no open action, replay or end-of-play labels exist for NFL, CFB, NCAA or NHL broadcasts. End-of-play timing must come from our own ESPN wall clocks and captures.

### Professional practice

- At Fox the director picks live shots and the producer picks replays ([Stockton blog](https://dstockton.com/behind-the-scenes-of-televised-sports/), [snippet]).
- Networks use 8 to 14 cameras; directors should not linger on huddles, should hold on emotional moments, and learn to slow down ([DGA Quarterly](https://www.dga.org/craft/dgaq/issues/1103-fall-2011/television-football), opened).
- About 11 minutes of action per telecast, about 60% of air time on huddles, line-ups and recovery ([WSJ via SVG](https://www.sportsvideo.org/2010/01/15/the-longest-11-minutes-in-sports/?print=1), opened, 2010).
- Fewer cuts lowered aesthetic ratings of soccer, N = 92, fandom moderated ([Ludwig & Bertling 2017](https://idrottsforum.org/international-journal-of-sport-communication-volume-10-2017-no-3/), [snippet]). One study, soccer only.

## Paid (none used, $0 budget, nothing signed up for)

| Vendor | Published price | Training allowed | Basis |
|---|---|---|---|
| Nielsen | None found | Unknown | Search only |
| Comscore | None; Vendr reports median about $97,896 per year | Unknown | [snippet] |
| Samba TV | None | Unknown | Search only |
| iSpot | None | Unknown | Search only |
| Sportradar | None; third-party figures conflict; 30 or 90-day trial, 1,000 calls | Unknown | [snippet] |
| Stats Perform (Opta) | None, sales only | Unknown | [snippet] |
| SportsDataIO | None public | Unknown | [snippet] |
| The Odds API | Free 500 credits; $30, $59, $119, $249 per month | Terms not read | [Opened](https://the-odds-api.com/) |

No vendor's terms were found allowing model training. Assume no until a contract says yes.

## Kaggle at $0

All "Yes" rows use public URLs and need no key and no sign-up beyond Nick's existing Kaggle account. Limits (about 12 hours, 20 GB output, internet needs a verified phone) are from memory because
[Kaggle's docs](https://www.kaggle.com/docs/notebooks) would not render. The BigQuery route needs a linked Google account, so prefer dump files.

## What to copy from how RedZone cuts

Rule #1 outranks all of these. Evidence is reporting, not measurement, unless noted.

1. **Trigger order:** score, then red-zone entry, then turnover, then a late one-possession game (within 8 points in the NFL). Other leagues use the same idea through `leverage.py`.
2. **Follow events, not a clock.** Target about 5 cuts per 10 minutes when 4+ games are live, up to 9 to 14 when busy (measured). Hard ceiling 3 per minute, RedZone's reported peak.
3. **One decider.** The ranker is the co-pilot and proposes; one switcher commits. No second rule path may fire a cut.
4. **Decide last.** Keep the pick open until the dead-ball window opens, then commit. Do not pre-commit at the end of the play before.
5. **Late narrowing.** In the last stretch, whip only among close games. Everything else drops off the list.
6. **Replay inside 60 seconds or drop it.** RedZone's 16-second setup and 30 to 60-second shift are the bar. Prepare it on the hidden stick, show it at a dead ball, and use a one-line note instead if neither fits.
7. **Budget visits.** Borrow CrunchTime's idea: limit visits per game early and always keep one for the last two minutes. Theirs is contractual; ours is anti-ping-pong.
8. **Stay for the replay.** After a score, hold about one replay (6.8 seconds is soccer's average) unless a higher-value event is live.
9. **Reorient on landing.** Hanson briefs each game before handing off; we have no host, so show one line (down, distance, score, clock) on every arrival.
10. **Expect time to wait.** About 60% of air time is dead ball, so waiting for a safe cut costs little.
11. **Live events beat the plan,** except when a team lock is on. WSH and UNC stay first as priors, not as vetoes.
12. **Last game standing:** stop whipping and stay on it.

Do not copy: on-screen ads (keep 0 commercial seconds), filler, or multi-box layouts unless the TV is shown to support them.

**Cheapest missing truth:** hand-log one hour of real RedZone (cut time, game, reason), about 60 minutes, no recording. It would be the first public-grade measurement of real dwell times.

## Sources

Opened (53): NPR via KBIA; Grantland; Boardroom; Wikipedia NFL RedZone; SI 2016; Gear Patrol; Yahoo ad change; Stacker/KEYT; Awful Announcing CrunchTime; Uproxx; NBA.com; SVG Paris; NBC release; Schwäbische; Yahoo fanbases; BVM Sports (no usable content);
Wikimedia dumps index and analytics page; Wikitech pageview_hourly; Wikimedia API test; Google 2018 blog; kuairand.com; Zenodo KuaiRand; Yambda card; Puffer data description; Puffer NSDI paper; Mr. HiSum repo and licence; CSO WMI11; Reddit data terms; Google Trends post; MovieLens 32M; Solow et al.; UCC hurling; SoccerNet-v2 paper; SN-Labels API; SN-Echoes card and size API; Zenodo Twitch EDL; arXiv SoccerNet 2026; DGA; SVG 11 minutes; The Odds API; Puffer bucket listing; Wikipedia clickstream page; Wikimedia pageviews readme; InVID TV Logo record; SoccerNet-Caption abstract; AVE paper; MovieCuts paper; VEBench; USL camera-selection abstract; AutoShot repo; ClipShots repo; QVHighlights data README.

Search snippet only (21): Barrett Media; Awful Announcing ad load; ESPN Goal Line; Konferenz press; Gold Zone feeds; Buraimo et al.; NFL playoffs MetaTOC; second-screen study; inpredictable; Yale; Stockton blog; Ludwig & Bertling; Comscore Vendr; Sportradar; Stats Perform; SportsDataIO; Bluesky; FOOTPASS; FineSports; NSVA; MultiSports. The FOS Hanson item and the NFL.com record were also snippet-only and are marked in place, but are not in the count.

Repo and catalog: `redzone-agents/data/catalog.md`; `docs/research/2026-11-08/redzone-numbers.md` and `viewer-experience.md` (not in this checkout; read from a worktree that has them).
