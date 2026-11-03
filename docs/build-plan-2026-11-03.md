# Build plan: Tue Nov 3, 2026 (no TV testing today)

Rule for every merge: the full test suite passes, the scorecards show 0 mid-play cuts, and nothing
important gets worse. Anything that needs the TV is built today and tested when you're home.

## What I found while planning

| Fact | Number | Where |
|---|---|---|
| A Prime switch takes | **32 s median** (19 to 61 s, 15 real switches) | `data/switch_times.json` |
| A YouTube TV tune takes | 4.5 s | same |
| A flip to a stick that's already playing the game | **~3 s** | same |
| Prime switches that were already preloaded on the hidden stick | **3 of 13** | same |
| Priority by league (NFL over MLB, and so on) | **doesn't exist**: every league competes on equal terms | `redzone/scoring/engine.py` |
| College football and basketball | scored with the NFL/NBA rules, no break lengths, no scorecard | `default_config.yaml`, `scripts/` |
| ESPN's game data includes **highlight clips as direct MP4 links** | 12 clips in Monday night's game | `data/archive/nfl/summary_*.json` → `videos` |
| Break detection | NFL/NBA/NHL timeouts, TV timeouts, reviews, quarter ends. **No free throws, no challenges as their own type** | `redzone/scoring/breaks.py` |

The reason Prime takes 30 s isn't the stick. It's that the app opens Prime cold: a deep link, then the
detail page, then "Watch", then "Watch Live". The fix is to make the slow part happen
**off screen, before it's needed**.

---

## Build order (today ≈ 10 hours; finished items are kept)

### 1. Instant Prime switches (~2 hr)

Goal: a Prime switch goes from 32 s to ~3 s whenever the HDMI1 stick is up.

1. **Cost-aware preload** (`redzone/standby.py`): the hidden stick prefers the game that's slowest
   to reach. A preloaded Prime game saves ~29 s, while a preloaded YouTube TV game saves ~2 s. Today it
   ranks by "most likely next" only. The new rank is likely-next × seconds saved.
2. **Prime parking**: when tonight's plan has Prime games, the hidden stick keeps the best Prime game
   playing between other preloads, unless a YouTube TV preload is needed within its 4.5 s budget.
3. **Never leave Prime cold on the visible stick**: when switching away from a Prime game, keep it
   playing on the stick that goes hidden (already ~2.5 s to bring back). It's re-tuned only for a
   clearly better Prime game.
4. Scorecard: add "Prime switch seconds" and "preload hit rate by app" to the demo replay (Nov 1 has
   Prime NBA). Target: Prime hit rate from 23% to above 70%.
5. **When you're home** (a 15-minute test): run `rz_prime_pause.py` to check whether a paused Prime game
   resumes where it stopped. If it does, a Prime switch can also land exactly on a dead ball (this
   also fixes NBA mid-play entries).

On one stick, Prime can't be instant (it has to load on screen). **Plugging HDMI1 into wall power is
the real fix.**

### 2. Your league order, teams and bets (~1 hr)

New setting `score.league_rank` (`default_config.yaml` + `engine.py`), applied to neutral games only:

| League | Bonus |
|---|---|
| NFL | +10 |
| CFB | +8 |
| NBA | +6 |
| NCAA basketball (men's) | +4 |
| NHL | +2 |
| MLB, women's college basketball | 0 |

- **Your teams in the same order**: Commanders > UNC football > Wizards > UNC basketball > Capitals >
  Nationals (per-team boost, replacing today's flat +25).
- **Bets as their own tier**: the boost scales with stake/odds, separate from teams. I'll start at bet = your
  #3 team level; you can change it from the phone.
- Garbage time still wins: a blowout loses its bonus, even yours.
- Scorecard: a league-mix check on the Nov 1 replay (NFL + NBA + NHL same night). NFL screen time
  should go up, with 0 crunch plays lost in other leagues.

### 3. Break detection, locked in (~3 hr)

One detector (`redzone/scoring/breaks.py`) that tags every stoppage with type, expected length and
confidence, for all six leagues.

| Stoppage | Source today | Adding |
|---|---|---|
| Commercials / TV timeouts | play-by-play | **College media timeouts** (CBB: first dead ball under 16/12/8/4 min, a fixed schedule), CFB TV timeouts, NHL TV timeouts at 6/10/14 min |
| Team timeouts | play-by-play | NBA mandatory timeouts (under 7:00 and 3:00) |
| **Free throws** | none | NBA/CBB "free throw 1 of 2": ~25 to 40 s of dead ball, **a clean entry point**, not a visit |
| **Challenges / reviews** | lumped as "review" | NFL challenge, NBA coach's challenge, NHL coach's challenge: ~60 to 120 s |
| Injuries | NFL only | all leagues |
| Score bug gone (a commercial on screen) | `screen/scorebug.py` exists | use it as a second signal on the game you're watching |

- Break lengths for college (missing today), measured from the archive.
- **Detection scorecard (new)**: for every real stoppage in 14 archived nights, did we see it, how many
  seconds late, and how far off was the length. That gives a number to improve, not a feeling.

### 4. Switch more, at the right moments (~1.5 hr; needs 3)

- Every detected stoppage of 30 s or more is a chance to visit the best live play elsewhere. Every free
  throw / dead ball is a chance to **enter** a game cleanly.
- Tune `switch_margin` (10), `two_stick.hold_s` (20) and `min_gain` (8) with the scorecard instead of by
  hand, for both quicker switches and more of them.
- New scorecard lines: switches per hour, seconds of dull play watched while a better game was live,
  and seconds from decision to picture.
- It merges only if mid-play cuts and entries stay at 0 (or drop) and NFL big plays seen live goes up.

### 5. Catch-up mode: a 5-game highlight reel (~3 hr)

You get ~60 to 90 s of back-to-back clips from up to 5 games, played when the TV would otherwise sit on
dead time (halftime, long commercials, or everything on a break at once).

- **Finding clips**: ESPN posts in-game highlights (MP4 links) in the same game data the app already
  polls every few seconds. No new account, $0. I'll measure from the archive how fast clips appear after
  the play.
- **Playing them**: the Mac serves a reel page (`/reel`) on the app's own server: a full-screen video
  player that plays the clips one after another. The hidden stick opens it in Fire TV's Silk browser,
  then the TV flips to it. Fallback (the clip isn't posted yet): YouTube TV's rewind replay, as today.
- **Order**: your teams first, then by league order, then by how big the play was. A play you already
  saw live is skipped.
- **Rules**: never during crunch time anywhere. Cut the reel the moment a live drive gets hot or your
  game comes back. Max 90 s (the old version's mistake was long reels keeping you 18% behind live).
- **When you're home** (10-minute test): Silk autoplay and full-screen on the stick. If Silk blocks
  autoplay, the fallback is VLC from the Amazon store (free; I'll ask before installing anything).

### 6. NHL: power plays, goals, overtime (~1 hr)

- Power play: always a candidate (not only when the game is close), but it never beats crunch time
  elsewhere or your team late.
- Overtime: never left except for crunch time elsewhere.
- Goals: a goal not seen live goes in the next catch-up reel (5).
- **Merge NHL entry timing** (mid-play entries from 165 to 12) first, since you said yes to items 1 to 5.

### 7. Smarter model, NFL first (~3 hr, may spill to tomorrow)

- **Hype v2 retrain**: it already lifts NFL big plays seen live from 29.9% to 39.5%. Its NBA rankings are
  what blocked it. Retrain only the NBA part, keep NFL as is, then re-run all three scorecards.
- **RedZone drive priority, reworked**: an NFL drive inside the 20 beats other sports' non-crunch
  play. Small rework from the `redzone-mode` branch, minus the long reels (5 replaces them).
- **"When to go" awareness**: feed the stoppage detector (3) into the model, so it knows a 2-minute
  review is coming before the score bug disappears.

### 8. Trip budget fix (~1 hr)

Skip a break visit when the round trip doesn't fit in the break, but keep catch-up replays (the one
Sunday it broke, CLE @ CHI). Merge only if NFL big plays seen stays at 95.4% or higher.

---

## Today's order and what you'll see

| When | Item | You'll see |
|---|---|---|
| First | 6 (merge NHL timing) + 2 (league order) | Small, safe, and everything after uses the new priorities |
| Then | 1 (Prime) | Prime hit rate on the replay, before/after |
| Then | 3 then 4 (detection, switch more) | Detection scorecard and switches/hour |
| Then | 5 (catch-up reel) | Reel page you can open on your phone's browser to watch it play |
| Last | 7, 8 | Scorecards |

**Not today (needs you home):** Prime pause test (15 min), Silk reel test (10 min), plugging HDMI1 into
wall power (2 min). Nothing is deployed to the live service while you're out unless the scorecards are
a clear win, and the service is restarted only when no games are on.
