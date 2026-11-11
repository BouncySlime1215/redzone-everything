---
id: D-ADDICT
title: "The compelling show: what makes live viewing hard to leave, and how RedZone should engineer it"
date: 2026-11-09
stream: context
author: "Claude Opus 5.5 (research agent)"
status: draft
supersedes: []
sources: 75
verified: 56
affects: []
gate: []
---

# The compelling show

**Do this first (about 2 minutes):** answer the three questions in section 9. Everything else can wait for them.

Status: research only. No code, default or gate changed. Web text was treated as data. Nothing is quoted from a source.

Source tags on every claim:

| Tag | Meaning |
|---|---|
| [O] | I opened the page or PDF and read the content |
| [A] | I opened it, but only the abstract was available |
| [P] | I opened a press release or secondary summary, not the study itself |
| [S] | Search snippet or citation only. **Not opened.** Treat as a lead |

Evidence labels: **Strong** (replicated or meta-analysed), **Moderate** (good single studies or consistent observation), **Weak** (one small study, theory, or my own inference from another field).

On wording: this repo is public and `docs/research/attention-science.md` section 5.3 warns that internal wording becomes evidence. I use "compelling" in the body. "D-ADDICT" is only the id you asked for.

## 0. Bottom line

1. **Today's product paces cuts. It does not tell a story.** The research points at the missing layer: an open question you care about, resolving soon, that you can follow. A cut is good when it closes one question or opens a better one.
2. **Build one new data structure: a thread ledger.** Every game holds its current open question, its stake, when it should resolve and whether you saw the answer. Stay-or-cut, teases, callbacks, previews, catch-up and the end of the night all read from it.
3. **The TV can only change which game shows and when.** There is no overlay path on this rig. So words go on the phone, and the TV's craft is timing: hold through the answer, enter a rewind before the snap, cut on resolution.
4. **Live sport already is a variable-reward schedule, and an honest one.** Adding artificial randomness would add harm and little else. The edge is finding real moments early and arriving before them.
5. **The evidence is thin exactly where we need it.** Nobody has published a test of switching between live games. Most mechanics below are inferences from TV, film, games and lab work. So each one ships off, behind a flag, with a test on one viewer.
6. **Your taps can stay evaluation-only and you still get a taste model.** A declared taste card needs no taps. A separate opt-in teach button is the only path that would train anything, and only if you say so.
7. **The guard is a natural ending, not a nag.** Self-set limits work in the best experiment on this. In-app time prompts mostly do not.

## 1. What this design has to live with

| Limit | Source | Consequence |
|---|---|---|
| Never join mid-play; leave mid-play only at low leverage | CLAUDE.md, `redzone/lab/leverage.py` | Every mechanic below only chooses among landings that already pass |
| No overlay on the TV; sticks only switch inputs | decisions.md, EXP-038 | On-TV words are not available live. Words go on the phone |
| Reels are built but do not play on the TV | `reel.play: false`, default_config.yaml | Anything reel-based has this blocker first |
| TV volume can be stepped by remote key | `volume_duck` in `redzone/tv.py` | Coarse audio control exists. It uses the TV remote connection, so check it cannot delay a flip |
| Rewinds are unreliable today | D-OWN: 20 `scrub_failed` vs 7 `scrubbed` in `buffer.jsonl` | Build-up rewinds depend on this being fixed |
| Binding line, Nov 4 | decisions.md | Objective is "worth it", not watch time. No betting, artificial suspense, autoplay or pull-back nudges, streaks or viewer sensors |
| Taps are evaluation-only | decisions.md, Nov 4 10:55 AM and 6:10 PM | Section 5 |
| Data is tiny | D-OWN: about 90 real user switch rows, 64 graded cuts, 0 votes | No model can learn from this yet. Section 6 sizes the tests |
| Basics not proven | Major considerations note: 0 full NFL Sundays run; 24 of 371 key moments seen Nov 7 | Phone-only mechanics can start now. TV mechanics follow the reliability work |

This report does not repeat the 22 mechanics in `watch-science-playbook.md`. It extends them. Where one builds on an item there, it says so as "playbook #N".

## 2. What the evidence says

### 2.1 Suspense and surprise

- **Definitions that can be computed.** Suspense is the expected size of the next belief change. Surprise is how far belief just moved. The paper notes that fast-forwarding through a match would likely remove the appeal, because viewers need time to relish it [1, O]. That is theory, but it is the base for everything here.
- **Audiences follow both, and more late.** Wimbledon, 8,563 minutes across 80 matches: both raise TV audience, surprise more than suspense, and both grow later in the match [38, A]. EPL, close to 50,000 minutes: suspense, surprise and pre-match-upset "shock" all matter [37, A].
- **The studies disagree on which matters more.** On 790 Premier League matches, suspense alone is positive, but with both in the model only surprise stays significant [8, O]. On Twitch CS:GO, one standard deviation more suspense cuts the chance of leaving by 0.27 points, while surprise has little effect and neither changes joining [36, A]. **My read:** suspense is the thing you can act on ahead of time; surprise can only be delivered after it happens. Rank on suspense. Replay surprise. That fits playbook #1 and #11.
- **Effects are real but small.** NBA closeness adds a modest audience, mostly near the end, with diminishing returns, and viewers leave at halftime and the end regardless [39, A]. Hurling shows the same up-and-down shape, in a summary with no sample given [31, P]. So suspense is not magic. A good ranking gives an edge, not a transformation.
- **Pre-game closeness is only a moderate guide.** Correlation between the pre-match win-probability gap and realised suspense is about -0.39 [8, O]. Rank in-game, not on the schedule.
- **Caring is a multiplier, and bad feelings count.** 113 students logged suspense at each of 24 breaks of Ohio State-Michigan 2006. Rooting mattered, habitual fan commitment did not, and fans of the winning side who had nearly given up rated it most thrilling [26, P]. One game and one sample.
- **Commentary as drama.** The classic experiments on dramatic commentary exist, but I could not read them, so no finding is used here [64, S].
- **What RedZone actually does.** Staff prepare notes all week and hold milestone graphics ready before the moment arrives. A co-pilot proposes the next game [9, O]. Storylines get an 8 a.m. meeting, a late-scored touchdown is rolled back about 16 seconds, and the day ends with a roughly 10-minute touchdown montage [10, O]. Some scores air 30 to 60 seconds late [11, O]. So the show's story comes from preparation, not improvisation.

### 2.2 Anticipation and prediction error

- Dopamine neurons in humans, monkeys and rodents signal received minus predicted reward. Fully predicted rewards leave them at baseline [53, A]. **Strong** for the neural signal.
- Anticipating a gain engages the striatum [73, S]; earlier repo reviews cover this.
- **Momentary happiness follows recent expectations and prediction errors, not accumulated winnings.** Replicated in 18,420 people [22, O]. A later paper argues that the learning-relevant part matters more, and reward size less, so treat the detail as open [61, S]. The practical idea: the feeling lives in the gap between what you expected and what happened. A viewer told what is at stake feels the outcome more than one who lands cold.
- **Wanting is not liking.** Wanting runs on large, robust systems and can grow without liking [52, A]. This is the line between compelling and harmful, and the reason "worth it" stays the objective.
- **Uncertainty first, then surprise.** In pop music, pleasure was highest for a surprise after a confident expectation, or for a confirmation after an uncertain one [35, P]. Lab chords, so only an analogy. But it matches sport: a fourth-and-goal is the setup, the snap is the release.
- **Teasing works and people do not ask for it.** Creating and then resolving a brief uncertainty, about 12 seconds in one study, raised enjoyment, yet most people picked the immediate answer when offered [23, P; 75, S]. Trivia only. No sports test exists. Curiosity theory points the same way [67, 68, 69, S].

### 2.3 How other products pace and cut

| Product | What it does | Evidence | Take for RedZone |
|---|---|---|---|
| **TikTok** | About 30-50% of the first 1,000 videos exploit known interests; the rest explores. Following and liking drive personalisation most; early skip rate was not significant [6, O] | Good data donation study of 220 users | Explicit signals beat passive ones, which supports section 5. Keep real exploration |
| **TikTok** | Generally avoids two in a row with the same creator or sound; adds off-interest items deliberately [13, O] | Company statement | A dispersal rule. We have one: no return inside 60 s without a new Tier 1 |
| **TikTok** | Internal test reportedly cut teen use from 108.5 to 107 minutes with a time prompt. Habit reportedly forms by 260 videos [14, O] | **Allegations in a state filing**; TikTok disputes them | Bolted-on prompts are weak |
| **Short-form harm** | TikTok condition alone hurt remembering a planned action, N = 60 [49, A] | One small study | Fast unrelated switching can cost something afterwards. Measure it |
| **Twitch** | Chat bursts can help predict highlights [56, A]. Hype Train is a meter driven by real money in a 5-minute window [66, S] | Mixed | Crowd energy is real but needs a crowd. We have one viewer. Do not fake it, and no money |
| **Quibi** | Five to 10-minute chapters, mobile only. Founders blamed product-market fit and analysts blamed missing must-have shows, not the length alone [15, O; 70, S] | Journalism | **Short format does not create demand.** Pacing cannot rescue content nobody needs. Live sport already has the need |
| **Konferenz (Germany)** | Quick cuts between set pieces, a corner to another corner, plus a mix of live goals and the famous goal shouts inserted afterward [12, O] | Journalism | A matched-action cut is cheap for us: cut from a dead ball to a similar-stakes moment. The goal shout is the "we missed it, here it comes" move |

### 2.4 What viewers experience when switched and interrupted

- **Interruptions can improve enjoyment.** Six studies: breaks made shows more enjoyable by resetting adaptation, even with poor ads, while people predicted the opposite [2, O]. Forecasters wrongly expected harm, so stated preference is a poor guide here.
- **Boundaries of that result matter.** It vanished for content that does not adapt, and it reversed with age: in one online panel, younger viewers (35 and under) liked a clip more with a break and older viewers less, marginal on liking and significant on willingness to pay [2, O]. Those were single programs and 30-second ads. Applying it to cutting between games is my inference. **Weak** for us.
- **Edit rate.** Faster edits inside a scene raised arousal and memory with no clear overload [50, A]. Fast pacing with arousing content lowered recall [63, S]. Related versus unrelated cuts is the classic question and I could only confirm the citation [62, S]. Every whip-around cut is both arousing and unrelated, which is the untested corner.
- **Rate of consumption.** Fast consumption favours variety. Slow it down and people prefer repeating the best thing, which they fail to predict [51, A]. This supports burst-then-hold pacing. Lab stimuli.
- **Repo finding to carry forward.** Interrupting at a boundary is the best-supported rule, and a dead ball is a boundary (R-VIEWER P1). Rule #1 is also the attention rule.

### 2.5 Variable-ratio schedules

- Slot machines are more accurately random-ratio than variable-ratio [74, S]. Posting behaviour fits reward learning across a million posts [54, A]. Near-win dressing raises arousal: outcomes that lose money but celebrate produced more arousal than plain losses [29, P].
- **For us:** a whip-around is a natural random-reward feed. That is the appeal. The danger is only in faking it: invented hype on routine moments is the sports version of a loss dressed up as a win.

### 2.6 Momentum, comebacks, rivalries and stakes

- **Runs are mostly what chance produces.** NBA scoring across 6,087 games looks close to memoryless [40, A]. Timeouts show no causal effect once regression to the mean is handled [41, A], though other studies disagree in size and sign (seen in snippets). A subtle bias reversed the classic no-hot-hand result, so small real streak effects may exist [42, A].
- **So "12-0 run" is a true story and a weak forecast.** Say it as a fact. Never say it predicts. Never let it rank a game beyond what win probability already sees.
- **Rivalry:** seeing a rival fail activated reward circuits, which correlated with self-reported aggression, under 20 fans scanned [4, O]. Real but small, and a warning. Rivalry belongs in the care weight. It does not belong in taunting content, which the playbook already bans.
- **Stakes:** I found no clean study isolating rivalry or playoff stakes on TV ratings. **Gap.** Stakes are justified by the care multiplier, not by direct data.
- **Comebacks:** the clearest support is the nearly-gave-up finding in 2.1 [26, P], plus surprise effects above.

### 2.7 Second screen and social energy

- Not-close games drew more second-screen activity than close ones, N = 630 [58, S]. Sports-site traffic rose 5-15% in commercial breaks and fell back at resumption [18, O]. **The phone is a break-time surface.** Do not pull his eyes during live action.
- Survey work links team identification to second-screen engagement [65, S]. World Cup work found moderate use [20, O]. No controlled experiment of phone use during a live game turned up. **Gap.**
- **Simultaneous group attention intensified emotion** in five studies [25, P]; a later EEG paper reports mixed results (seen in snippet only). It is a reason for a real friends mode, later. Fake crowd signals are banned.

### 2.8 Audio, music and transitions

- **Added crowd noise raised perceived excitement, most in dull stretches** [28, P]. That is exactly why not to use it: it inflates dull play. It would break "no artificial suspense". Fans also say no: 40% less enjoyable, 16% more; background music 36% less, 22% more, N = 1,492 [19, O].
- **Replay.** Replay changes perceived intensity, with opposite effects on dull and exciting play [59, S].
- **J-cuts** let the next scene's sound lead its picture [34, O]. Our HDMI flip takes about 2 s and is a hard cut. Audio cannot lead picture, because the hidden stick's sound is not reachable.
- **Alerts.** More false alerts lowered compliance in student pilots, though that paper also found miss-prone alerting costly [5, O]; nurses exposed to many false alarms responded more slowly [72, S]; one air-traffic field study found no such effect [71, S]. Prolonged exposure to warning sounds can produce some habituation, as cited in [21, O]. **Keep sounds few and only on real events.**
- **Loudness.** Two HDMI inputs and several apps differ in loudness and nobody has measured it (major considerations note). A level jump at each cut is unpaid-for friction.

## 3. The show model

### 3.1 The thread ledger

One record per live game, updated on every feed tick. Local, derived only from public feeds.

| Field | Meaning | Example |
|---|---|---|
| `question` | What the next stretch answers | Can BUF punch it in on 3rd and goal? |
| `kind` | Goal-to-go, power play, final possession, run, comeback | `goal_to_go` |
| `opens_at`, `eta_s` | When it opened; expected time to resolve | Next snap, about 25 s |
| `stake` | Care times win-probability swing if resolved | From `S`, team config, standings |
| `closes_on` | Machine-checkable end | Score, turnover, first down, PP expiry |
| `last_seen_s` | Seconds since this game was on screen | 95 |
| `seen_how` | Live, rewind, reel, or not seen | `none` |

Starting definitions, all to be tuned in the sim:

| League | Thread opens | Thread closes |
|---|---|---|
| NFL, CFB | Goal-to-go, 3rd or 4th down in opponent half, final 2 min one-score | Score, turnover, first down, end of half |
| NBA, CBB | Close-late window from `leverage.py`, then each possession | Made or missed final shot, timeout, game end |
| NHL | Power play; close-late; empty net | PP expiry, goal, whistle at end |
| MLB | Runners in scoring position, late and close | Out that ends it, run, inning end |

### 3.2 Stay or cut, as one rule

This sits inside the existing priority ladder. Tier 1 elsewhere still wins. It replaces the "best game by suspense" rung with a version that respects closure.

```
stay_value = care(g) * S(g, h) + closure_bonus(open thread resolves within h)
alt_value  = max over a [ care(a) * S(a, h) * P(safe landing on a) ] - cut_cost(gap since a was seen)
cut only if alt_value - stay_value >= margin, at a landing that passes Rule #1
```

`S` is the existing suspense measure, E[(ΔWP)²] summed over expected plays in a horizon `h`. `cut_cost` rises with gap, because a long-absent game needs more explaining (R-VIEWER P2). `buffer_shadow.jsonl` already logs `value_stay`, `value_best_alt` and `value_gap`, so this can run in shadow first.

Three behaviours fall out:

1. **Hold through the answer.** You saw the question, so see the answer.
2. **Cut on resolution, not on a timer.** The moment the question closes, the game has less to offer.
3. **Do not open a question you cannot close.** If you must leave an open thread, mark it. Return at its next dead ball (playbook #9).

Nick's speed ask (13-15 cuts per 10 minutes) stays a guide. Closure rate decides whether a cut was good; the count does not.

## 4. Mechanics, in detail

Words on the phone appear only at dead balls on the TV, so the phone never competes with live action. Every template is computed from play-by-play and re-validated at display time.

### M1. Closure-first hold and cut
Section 3. Builds on playbook #2, #8, #9. Needs the ledger.

### M2. Build-up-first rewinds, with a spoiler guard
When a Tier 1 play was missed, the hidden stick scrubs to before the snap, not to the result. Lead in by 8 to 12 s for NFL, the start of the possession for NBA, the faceoff for NHL (starting values). You watch it with the tension intact.

The spoiler guard is the cheap half: while a play is queued for rewind inside 60 s, the phone holds back that game's score change and any line that reveals it. A phone tile that shows the score first destroys the replay.

Honesty: a small "replay" tag on the phone, and the replay honesty fault in `reactgrade` still applies. RedZone presents such plays as live [11, O]; we label them, because Nick should always know.

Blocked by scrub reliability (section 1). Spoiler guard is not blocked.

### M3. Callback cards
One line on the phone on arrival, chosen from the ledger:

| Type | Fires when (starting values) | Example shape |
|---|---|---|
| Run | NBA 10-0 or better; NHL 2 unanswered in a period; NFL/CFB 14 unanswered; **and** ΔWP at least 0.10 | "12-0 run since the timeout" |
| Comeback | Max deficit erased by half or more, and ΔWP at least 0.15 | "Was down 18, now down 3" |
| Stakes | Clinch, elimination, seed swing for a followed team | "A win here locks WSH's seed" |
| Milestone | From the morning story cards | "One TD from the franchise record" |
| Left-open | Thread you saw open, away for over 60 s | "Still 3rd and goal from before" |

Caps: at most one line per arrival, each type at most once per 20 minutes, never the same fact twice in a game. Rarity keeps it meaningful (habituation in HeartSteps, [32, O]).

Prepared cards come from the planned 4 AM job, as staff notes do at RedZone [9, O]. No live LLM during games, per the 8 GB Mac limit.

Never: claim a run predicts anything, taunt a rival, or dress a routine play as big. EXP-038 and playbook #12-14 are the starting code.

### M4. Honest tease
On the phone, at a dead ball, right before a flip: the question, not a promised result. "Next: UNC ball, down 3, 1:12 left." Only when:

- the flip is already chosen and landing safe,
- the answer should arrive on screen within about 30 s,
- the question gets answered on screen, whatever the answer.

"Paid" means the question was resolved on screen, not that something exciting happened. Target at least 90% paid; the feature turns itself off below 80% over a rolling 50 teases. This stays inside the "no teases that fail to pay off" line.

Also a matched-action cut (Konferenz [12, O]): when two games are at similar-stakes dead balls, prefer the like-for-like cut as a tiebreaker.

### M5. Peak map
Phone: "Coming up": the next 2 to 3 expected peaks with an honest ETA ("WSH enters final 5 in about 6 min"). TV: the hidden stick pre-tunes the top one. Shown at session start and during TV breaks. Forecasting has real room in NBA, little in NHL (the-mind note), so per-league ETAs are labelled accordingly.

This does double work. It feeds anticipation, and it tells Nick honestly how much night is left, which the guard needs.

### M6. Stakes and scoreboard care
Add to the care weight from declared config only: rivals, standings impact. "Scoreboard watching": a game that moves WSH or UNC's standing gets care even with neither team in it. Pre-game priors and in-game standings math. No names in code, per the personal-config decision.

### M7. The Close (also the main guard)
Section 7. Montage plus an ending.

### M8. Burst-then-hold rhythm
After 3 or more cuts in 90 s, the next scene holds at least one full possession unless a Tier 1 fires. After a big resolved moment, hold about 7 s for the first replay and reaction (soccer replays average 6.8 s; no US data, D-PRIORITY) unless a Tier 1 is live elsewhere. Directors are told to hold on emotion (DGA, via D-PRIORITY). Builds on playbook #18.

### M9. Audio pass
1. Per-input volume offsets, set by ear once (about 5 minutes), applied by remote key. No mic.
2. Phone chime only for real top-tier events: his team's Tier 1, overtime starting. At most 2 distinct sounds. Off by default.
3. No stings on routine switches (playbook #16), no added crowd, no music bed.
4. Test a flip duck later: a quick volume dip around the HDMI flip. Check first that it cannot delay the flip, since it uses the TV remote connection.

### M10. Taste card
Section 5, rung 0.

### Also worth building, outside the top 10

| Mechanic | Why it waited |
|---|---|
| Crunch primer reel: 20 s of how this game got here, before landing in a game you have not seen | Care-building is plausible but evidence is weak, and `reel.play` is off |
| Explore visits: in lulls, an occasional game he would not pick, only within a tie margin | TikTok-style exploration [6, O]; doubles as randomised exposure for evaluation. Useful once the tie rule exists |
| Rewind vs one-line for surprise: which is worth a dead ball | Needs reliable rewinds |
| Friends mode: real people, one-tap share of a moment | Group attention [25, P] is contested and needs other people |
| Boxed layouts on Prime multiview | Gated behind its own test |

## 5. A taste model that keeps taps evaluation-only

**The rule today.** His thumbs, overrides, lock-ins and taps feed no training, labelling or suggestion path. Ratings are evaluation-only.

**The tempting shortcut is a leak.** If a bandit picks the winning setting automatically based on overrides, the taps have trained the system, whatever it is called. Same for "just eval" numbers that a script auto-applies.

**Overrides are also poor training data anyway:**

- They mix locks, bugs, boredom and real preference. On Nov 7, 101 of 104 `user` rows re-asserted a lock (D-OWN).
- They only exist for what the system chose to show, so they are selection-biased.
- About 90 real rows exist in 4 nights.
- Engagement behaviour and what people say they want can differ [46, A; 47, A]. In TikTok, explicit follows and likes drove personalisation much more than skip behaviour [6, O].

**Three rungs. Rung 0 and 1 need no new permission. Rung 2 needs Nick to say so.**

| Rung | What | Reads taps? | Trains? |
|---|---|---|---|
| **0. Taste card** | A declared card on the phone: leagues, teams, rivals, players followed, chaos versus closure, cut speed (`prefs.switch_frequency`), blowout tolerance. It sets `care` directly | No | No. It is a control |
| **1. Profile lab** | 6 to 8 fixed taste profiles, written from public data and the hindsight oracle only. A frozen grader scores them against overrides once a month. **A report goes to Nick; a person changes the config.** | Yes, in a grader only | No. No automated path from result to setting |
| **2. Teach button (opt-in)** | A separate "more like this / less like this" control, used only when he means to teach. It writes to its own file | No. It reads only that file | Yes, after a logged decision |

Rung 1 caveat: Nick choosing from a report is still information flowing from taps to config. It is his explicit decision, which is what "unless he says otherwise" means, but say so plainly in decisions.md. Hold the report until there are at least 300 clean rows, and always show the interval.

Rung 2 limits, if he opts in:

1. Under 10 features, simple update, inspectable. Nightly job, not during games. Whether arithmetic this small counts as "no training on the Mac" is his call.
2. It may only re-rank **inside a tie margin**: at most 10% of the value gap. It can never affect Rule #1, crunch floors or WSH and UNC first.
3. Learned numbers visible on the phone in plain words, one-tap reset, decays with about a 30-night half-life.
4. Extend the `tests/test_worth.py` scan so that override, lock and worth data cannot reach this path. Only the teach file can.
5. It never reads session length, time of night or anything that would predict he is tired (playbook line against targeting vulnerable states).

## 6. Measuring with one viewer and no public data

### 6.1 The design

Whole-night A/B cannot carry this alone. Rough sizes, 80% power and 5% two-sided:

| Unit | Comparison | Needed per arm | In real time |
|---|---|---|---|
| Night, 1-5 "worth it", assuming SD 1 | Detect a full point | 16 nights | About 32 nights |
| Night, same | Detect half a point | 63 nights | About 126 nights |
| Arrival, override within 30 s | 10% vs 5% | 432 arrivals, more once within-night correlation is allowed for (say ×1.5) | About 10 to 15 nights at 90 eligible arrivals |

The SD and base rates are placeholders until there is real data. The repo's current bar (4 rated nights a side, non-inferior by 0.25) can only ever be provisional, as its own note says.

So use **micro-randomisation** [3, O; 55, A]: re-randomise the mechanic at each eligible arrival, compare the next 30 to 60 s. Each decision point is one trial. HeartSteps got its result from 44 enrolled people this way, and saw effects fade over weeks [32, O], which is a warning to retest later.

Rules:

- **Rule #1 is untouched.** Randomise only among landings that already pass, and only when values are within a tie margin.
- **Log the probability.** Without it, off-policy analysis later is guessing.
- **Carryover.** Treat arrivals within 60 s as one block. N-of-1 designs use repeated crossover, often with washout, and frequentist significance may mislead at this scale, so report posteriors [33, O].
- **Blinding is impossible for phone features.** It is possible for timing features. Say which is which.
- **Whole-night alternation** remains for things that change the feel of a night: the Close, rhythm.
- **Evaluation only.** Overrides are the outcome in a frozen grader outside `noggin`, `decider` and `scoring`, matching `lab/orient.py`. A person flips the flag. No auto-adopt.
- **Filter `dry_run=1`** before any count (D-OWN item 1).

### 6.2 Logs to add

One local file, `data/show.jsonl`. No addresses or account data; aggregate before anything is committed.

| Event | Fields |
|---|---|
| `thread_open` | thread_id, game, kind, question_tpl, eta_s, stake, wp |
| `thread_close` | thread_id, closes_on, delta_wp, on_screen, seen_how, away_s |
| `arrival` | switch_id, game, gap_s, line_type, line_id, landed_dead |
| `tease` | tease_id, switch_id, eta_s, deadline, paid, paid_ts |
| `alloc` | decision_id, mechanic, arm, p, eligible, league, night |
| `peak_eta` | game, predicted_ts, actual_ts |
| `spoiler_hold` | game, held_s, leaked |
| `rewind_lead` | switch_id, lead_s, landed_before_snap |
| `phone_fg` | ts, visible, tv_state. Needs Nick's OK (section 9) |
| `night_close` | intended_end, last_peak_ts, close_cue_ts, choice, tv_off_ts, minutes_after_close |

Also from D-OWN: the one-row-per-switch join and `bug_clock_running`. The one-hour hand log of real RedZone is still the cheapest truth we can get. Add one extra reason code, `SET`, for "host set up the next game before the cut."

### 6.3 Metrics

| Layer | Metric | Role |
|---|---|---|
| Hard floors | `entries_mid_play`, high-leverage leaves, `plays_missed_entry`, `dead_air_pct` | Never traded |
| Primary, nightly | "Worth it" 1-5 | Liking. Sets the verdict over time |
| Primary, per cut | Override within 30 s by arm | The sensitive test |
| Story | % threads closed on screen; % of Tier 1 with build-up seen | New North Star candidates. Not gates |
| Trust | Tease paid %, peak ETA error, spoiler leaks | Honesty |
| Guardrails | Minutes after the Close; regret probe; agency probe; hours past intended end | Compulsion |
| Diagnostic only | Watch time | Never a target |

Block rule from the playbook: hours up while "worth it" is flat or down means wanting is outrunning liking, and the change does not ship.

## 7. Compulsive-use risk and guards

He asked for this, and he is an adult watching his own TV. So guards here are not there to overrule him. They keep the choice his, and they guard what he has said matters: nights that were worth it.

### 7.1 Where the risk is

| Risk | Which mechanics | Evidence | Guard |
|---|---|---|---|
| Late nights: pre-sleep arousal and later bedtimes | All, worst for West Coast slates | Binge viewing tied to poorer sleep through pre-sleep arousal, N = 423, cross-sectional [44, A]; going to bed later than intended is linked to weaker self-regulation, N = 177 [45, A] | The Close; intended end |
| Wanting without liking | Teases, previews | [52, A; 46, A] | Worth-it is primary; block rule |
| Loss of agency from endless autoplay | RedZone itself | Autoplay and recommendations reduce agency, survey of 120 [48, A]. Turning autoplay off cut about 21 min a day and 18 min per session, N = 76 [7, O] | Natural end, one-tap controls, `phone.commit` |
| Alert blindness or dependence | Chime, tease | [5, O; 72, S] | Reserved sounds; self-disabling tease |
| Phone habit during games | Cards, map | [49, A] | Dead-ball only; no notifications when the session is off |
| Plain sports look dull afterwards | Whole product | **Hypothesis. No evidence found** | Watch whether solo-game nights fall in "worth it" |
| Stress peaks | His team late | Heart events reported up on Germany match days in one study [60, S], contested | None in product. Noted for honesty |

Bad fits for a guard, based on evidence: repeated time-limit prompts. TikTok's reported result was tiny [14, O, allegation], and Netflix's prompt exists for data and place-keeping, with no effect data [17, O]. Instagram's "caught up" marker is a nice idea with no measured effect [16, O].

What works: limits that people set for themselves substantially reduced use in a randomised experiment, which also attributes 31% of social media use to self-control problems [43, A]. And in the autoplay trial about a third planned to keep autoplay off afterward, for the moment of decision, while about half planned to turn it back on [7, O]. So give the moment, then respect the choice.

### 7.2 The Close

A natural ending that also happens to be the best part of the show, since peaks and endings dominate memory and length barely counts (meta-analysis, r about .57 for peak-end, duration near zero [57, S], consistent with attention-science.md).

1. **Say when, once.** At session start, an optional "done by?" or "stop after this game." Defaults to what he picked last time. Uses `phone.commit`. Skipping is fine.
2. **Honest last-peak line.** From the peak map: "Last game that matters tonight, about 20 min." If there is another real peak at 1:30 AM, it says so.
3. **Cue at a real boundary.** At the end of the last high-leverage window or his chosen time, at a dead ball. One phone card. Never mid-crunch. Never over his team's final possession.
4. **The ending is the default.** Montage of the night, 60 to 90 s, ordered with the best moment last, then the slate-complete state. One tap keeps going, no guilt language, no minutes counter, no streak.
5. **After the chosen end, the show shrinks.** Autopilot may continue if he says so. Teases, chimes and callbacks stop. Nothing intensifies when he is past his own line. This is the opposite of targeting tiredness.
6. **Private weekly line.** If "watched longer than I meant" is yes on 2 of the last 4 probes, or the block rule fires, one sentence in the Monday digest. Nothing on the TV.
7. **His call.** The Close is a setting. He can turn it off. If he does, log it in decisions.md and leave the Monday line on, unless he turns that off too.

## 8. Do not build

These cross the Nov 4 line or the evidence argues against them.

| Thing | Why |
|---|---|
| Added crowd noise, risers, music beds on routine play | Inflates dull moments [28, P]; fans dislike it [19, O] |
| Random "mystery" cuts, artificial delay to build tension | Manufactured prediction error |
| Holding back news he would normally have, outside the 60 s spoiler window | Feels like manipulation; makes the phone untrustworthy |
| Notifications when he has stopped watching | Banned pull-back nudge |
| Streaks, hours watched, badges | Rewards time |
| Ranking on session length or time of night | Targets vulnerable states |
| Run or momentum used as a predictor | Weak evidence [40, 41, A] |
| Taunts about rivals | [4, O] |
| Any betting tie-in | Standing rule |

## 9. Three questions only Nick can answer

1. **Does "the most invested part" change the Nov 4 line?** I assumed no. Every mechanic above stays inside it. Changing that line is your call, and it needs a logged decision.
2. **Teach button, yes or no?** Rung 2 is the only thing here that makes something learn from you. Rung 0 and 1 are fine without it.
3. **Is phone-app foreground logging OK?** `phone_fg` records only whether the RedZone page is visible. It is not a camera or microphone, but "viewer sensors" are banned, and the call is yours.

## 10. Sources

Web, opened and read [O]:
1. Ely, Frankel, Kamenica, Suspense and Surprise, JPE 2015. https://www.wiwi.uni-bonn.de/kraehmer/Lehre/TopicsSS14/ElyEtAl-SurpriseSuspense.pdf
2. Nelson, Meyvis, Galak, commercial interruptions, JCR 2009. https://interruptions.net/literature/Nelson-JConsumRes09.pdf
3. Klasnja et al., micro-randomized trials, Health Psychol 2015. https://archive.md2k.org/images/papers/jitai/nihms732638_Klasnja.pdf
4. Cikara, Botvinick, Fiske, Us versus them, Psych Science 2011. https://static1.squarespace.com/static/54238bf2e4b068090a9b54bb/t/54342558e4b08c886778d404/1412703576008/Cikara+et+al.%252C+2011+-+PsychSci.pdf
5. Wickens and Colcombe, imperfect alerting, Human Factors 2007. https://interruptions.net/literature/Wickens-HF07.pdf
6. Vombatkere et al., TikTok and the Art of Personalization, WWW 2024. https://arxiv.org/html/2403.12410v1
7. Experimental study of Netflix autoplay, 2024 preprint. https://arxiv.org/html/2412.16040v1
8. Flepp, Pawlowski, Richardson, Suspense and Surprise in European Football, 2025 preprint. https://arxiv.org/html/2506.21253v1
9. Grantland on the RedZone studio. https://grantland.com/features/watching-day-nfl-games-redzone-studio/
10. NPR via KBIA, seven hours behind RedZone, 2014. https://www.kbia.org/sports/2014-12-15/seven-chaotic-hours-behind-the-scenes-at-nfl-redzone
11. Wikipedia, NFL RedZone (secondary). https://en.wikipedia.org/wiki/NFL_RedZone
12. echo24 on DAZN and the Konferenz. https://www.echo24.de/sport/fussball/dazn-uebernimmt-bundesliga-konferenz-das-muessen-fans-jetzt-wissen-zr-93888405.html
13. TikTok newsroom, how recommendations work, 2020. https://newsroom.tiktok.com/how-tiktok-recommends-videos-for-you/?lang=en
14. WKMS/Kentucky Public Radio on the Kentucky filing, 2024. https://www.wkms.org/2024-10-11/listen-kentuckys-tiktok-lawsuit-revealed-executives-knew-about-apps-effect-on-teens
15. NBC News, why Quibi failed. https://www.nbcnews.com/business/business-news/look-why-quibi-failed-so-soon-after-launching-n1244312
16. NBC News, Instagram "all caught up". https://www.nbcnews.com/tech/tech-news/instagram-s-caught-joins-movement-set-limit-screentime-n890771
17. Netflix help, "Are you still watching". https://help.netflix.com/node/114059
18. Chartbeat, Super Bowl second-screen traffic. https://chartbeat.com/?p=2845
19. Morning Consult fan poll, 2020. https://intel.morningconsult.com/mc-content/articles/sports-poll-fans-prefer-natural-sound-in-telecasts
20. World Cup second-screening in Israel, Frontiers 2024. https://www.frontiersin.org/articles/10.3389/fspor.2024.1329364/full
21. Earcons and spearcons study, Frontiers 2022. https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.780657/full
22. Rutledge et al., momentary well-being, PNAS 2014. https://digitalwellbeing.org/wp-content/uploads/2014/08/PNAS-2014-Rutledge-1407535111.pdf

Web, opened, press or secondary [P]:

23. Chicago Booth Review on the teasing effect. https://www.chicagobooth.edu/review/how-hook-customers-first-tease-them
24. APS on spoilers (Leavitt and Christenfeld); stories only, no sports test, so not used for a mechanic. https://www.psychologicalscience.org/news/releases/spoiler-alert-stories-are-not-spoiled-byspoilers.html
25. Kellogg on group attention (Shteynberg et al. 2014). https://www.kellogg.northwestern.edu/academics-research/research/detail/2014/feeling-more-together-group-attention-intensifies-emotion/
26. Ohio State on sports suspense (Knobloch-Westerwick et al. 2009). https://news.osu.edu/ohio-state-study-dont-be-happy-be-worried-sports-fans-need-dose-of-negative/
27. Phys.org on live TV indeterminacy (Vosgerau et al. 2006); supports why live beats replay, thin summary. https://phys.org/news/2006-01-television-appeal.html
28. TechXplore on Cummins' crowd-noise studies. https://techxplore.com/news/2020-09-professor-artificial-crowd-noise-applicable.amp
29. Psychology of Games on Dixon et al. 2010. https://www.psychologyofgames.com/2022/08/how-to-disguise-lousy-luck-as-an-absolute-win/
30. UMD on Moe and Schweidel's Hulu study; finale-driven engagement drop, background only. https://rhsmith.umd.edu/news/why-binge-watching-bad-hulu
31. UCC on hurling audiences, 2026. https://www.ucc.ie/en/business-school/newsandevents/2026/why-tv-viewers-crave-uncertainty-when-it-comes-to-hurling-matches.html
32. UK government guidance on MRTs, HeartSteps. https://gov.uk/guidance/micro-randomised-trial
33. Wikipedia, N of 1 trial. https://en.wikipedia.org/wiki/N_of_1_trial
34. Wikipedia, J cut. https://en.wikipedia.org/wiki/J_cut
35. University of Cambridge summary of Cheung et al. 2019. https://cms.mus.cam.ac.uk/publications/cheung-uncertainty-surprise/

Web, opened, abstract only [A]:

36. Simonov, Ursu, Zheng, suspense on Twitch, 2022/2023. https://ideas.repec.org/p/cpr/ceprdp/17353.html
37. Buraimo et al., EPL, 2020. https://econpapers.repec.org/RePEc:bla:ecinqu:v:58:y:2020:i:2:p:881-896
38. Bizzozero, Flepp, Franck, Wimbledon, 2016. https://ideas.repec.org/a/eee/jeborg/v130y2016icp47-63.html
39. Solow, Reilly, von Allmen, NBA, 2024. https://iro.uiowa.edu/esploro/outputs/journalArticle/Within-game-uncertainty-of-outcome-and-the/9984963525302771
40. Gabel and Redner, NBA scoring. https://arxiv.org/abs/1109.2825
41. Assis et al., timeout effects, 2020 preprint. https://arxiv.org/abs/2009.06750
42. Miller and Sanjurjo, hot hand. https://arxiv.org/abs/1902.01265
43. Allcott, Gentzkow, Song, Digital Addiction. https://www.nber.org/papers/w28936
44. Exelmans and Van den Bulck 2017, abstract through Europe PMC. https://pmc.ncbi.nlm.nih.gov/articles/PMC5529125
45. Kroese et al. 2014, bedtime procrastination. https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2014.00611/text
46. Kleinberg, Mullainathan, Raghavan. https://arxiv.org/abs/2202.11776
47. Milli et al., engagement vs stated preference. https://arxiv.org/abs/2305.16941
48. Lukoff et al., YouTube agency, CHI 2021. https://arxiv.org/abs/2101.11778
49. Chiossi et al., CHI 2023. https://arxiv.org/abs/2302.03714
50. Lang et al. 2000, edits. https://scholars.cityu.edu.hk/en/publications/the-effects-of-edits-on-arousal-attention-and-memory-for-televisi/
51. Galak, Kruger, Loewenstein 2011. https://econpapers.repec.org/RePEc:cup:judgdm:v:6:y:2011:i:3:p:230-238_5
52. Berridge and Robinson 2016, through Europe PMC. https://pmc.ncbi.nlm.nih.gov/articles/PMC5171207/
53. Schultz 2016, through Europe PMC. PMCID PMC4826767
54. Lindstrom et al. 2021. https://ideas.repec.org/a/nat/natcom/v12y2021i1d10.1038_s41467-020-19607-x.html
55. Liao et al., MRT sample size. https://arxiv.org/abs/1504.00238
56. Fu et al., chat and highlights. https://arxiv.org/abs/1707.08559

Search snippet or citation only, **not opened** [S]:

57. Alaybek et al. 2022, peak-end meta-analysis; publisher and index pages would not open. Listed in OBHDP
58. Lewis, Gantz, Wenner 2021, N = 630, Int J Sport Communication (record would not load)
59. Cummins and Hahn 2013, replay. https://vufind.lboro.ac.uk/PrimoRecord/cdi_proquest_miscellaneous_1667953777 (bot check)
60. Wilbert-Lampen et al. 2008, NEJM; secondary coverage in search results only
61. Blain and Rutledge 2020, eLife. https://elifesciences.org/articles/57977v1
62. Lang, Geiger, Strickwerda, Sumner 1993, citation only
63. Lang et al. 1999, via repo note `major-considerations.md` [b17]
64. Bryant, Comisky, Zillmann 1977, citation only. https://eprints.chi.ac.uk/id/eprint/4202/
65. Cunningham and Eastin 2017. https://metatoc.com/papers/56811-second-screen-and-sports-a-structural-investigation-into-team-identification-and-efficacy (timed out)
66. Streamlabs on Twitch Hype Train. https://streamlabs.com/content-hub/post/what-are-twitch-hype-trains
67. Hsee and Ruan 2016, Pandora effect. https://journals.sagepub.com/doi/abs/10.1177/0956797616631733
68. Gruber et al. 2014, via a later review snippet
69. Loewenstein 1994, via a snippet
70. NPR on Quibi. https://www.npr.org/transcripts/926809338 (timed out)
71. Wickens et al., ATC false alerts. https://portfolio.erau.edu/en/publications/false-alerts-in-the-atc-conflict-alert-system-is-there-a-cry-wolf/
72. CHOP on alarm fatigue. https://research.chop.edu/cornerstone-blog/researchers-honored-using-video-better-understand-alarm-fatigue
73. Knutson et al. 2001. https://www.jneurosci.org/content/21/16/RC159 (403)
74. Haw 2008, random-ratio. https://cdspress.ca/?p=8112 (metadata only)
75. Ruan, Hsee, Lu 2018, teasing effect, JMR, primary paper not read. See [23]

Internal, read in this run and not counted above: `docs/agents/data/2026-11-09-redzone-viewers-production.md` (D-PRIORITY), `docs/agents/data/2026-11-09-own-data-inventory.md` (D-OWN), `docs/research/attention-science.md`, `docs/research/watch-science-playbook.md`, `docs/research/2026-11-08/viewer-experience.md` (R-VIEWER), `docs/research/2026-11-08/catch-up-story.md` (headers), planner notes `the-mind`, `product-per-league`, `major-considerations`, `docs/memory/decisions.md`, `redzone/default_config.yaml`, `redzone/tv.py`, `redzone/reel.py`, `redzone/lab/leverage.py`.

Searched and not found: a controlled test of switching among live games; a study of phone use during a live game; rivalry or playoff stakes isolated in TV ratings; viewer data on score-run graphics; how RedZone's host hands off between games in any measured form.

## 11. Top 10 mechanics to build, ranked

Ranked by expected effect on the night, times evidence, divided by cost. Cost is agent work, excluding Nick's time. All ship OFF behind a flag and pass the hard floors. "Phone-only" means it can start before the switching work is proven.

| # | Mechanic | What it does on the TV | Why it should work | Evidence | Cost | How to measure |
|---|---|---|---|---|---|---|
| 1 | **Closure-first hold and cut** (M1, thread ledger) | Holds on a game until the open question is answered, then cuts right on the answer. Leaves an open question only for something bigger | Audiences follow suspense and surprise, more late [37, 38, A]; viewers stay when suspense is high [36, A]; boundaries are the cheapest place to interrupt (R-VIEWER P1) | **Moderate** for the ingredients. **Weak** for this exact rule: no test of it exists | About 4 days, including the ledger and shadow run on `buffer_shadow` | Shadow, then micro-randomise inside tie margin. `thread_close.on_screen` %, override within 30 s, floors |
| 2 | **Build-up-first rewinds plus spoiler guard** (M2) | A missed big play starts before the snap, so the tension is intact. Phone cannot give it away first | Anticipation is part of reward [53, A; 73, S]; live feeling matters [27, P]; RedZone's own 16 s rollback [10, O] | **Moderate** for mechanism, practice-based for the format | Spoiler guard about half a day (phone-only). Rewind about 3 days, **blocked by scrub failures** | `rewind_lead.landed_before_snap`, `spoiler_hold.leaked` = 0, rewind vs no-lead by arrival, `reactgrade` honesty faults = 0 |
| 3 | **Callback cards** (M3) | Nothing on the TV. Phone gives one true line on each arrival: run, comeback, stakes | RedZone's value is prepared story [9, O]; caring and knowing the stakes raise felt outcome [22, O; 26, P]. Runs stated as fact only [40, A] | **Moderate** for context. **Weak** for run lines specifically | About 2 days phone-only, on EXP-038; plus 4 AM cards | Randomise line vs no line per arrival. Override within 30 s, `arrival.line_type`, accuracy audit = 100% |
| 4 | **Honest tease** (M4) | Cut lands on exactly the question the phone just asked | Teasing raises enjoyment and people do not choose it for themselves [23, P]; matches the 12 s scale of a dead ball | **Weak**: trivia tests only | About 1 day phone-only, after the ledger | `tease.paid` at least 90% with auto-off below 80%. Randomise tease vs none per flip |
| 5 | **Peak map** (M5) | Hidden stick is already tuned to the next peak. Phone shows the next 2 or 3 with an ETA | Anticipation; NBA crunch is forecastable (the-mind note); also feeds the Close | **Moderate** for anticipation, **Moderate** for NBA forecast, **Weak** elsewhere | About 2 days, shares Planner 3's preload rung | `peak_eta` error, preload hit rate, "also close" override rate |
| 6 | **Stakes and scoreboard care** (M6) | Early in a slate, the TV favours games with real meaning for WSH and UNC, even when they are not playing | Care multiplies suspense [26, P; 4, O]; pre-game closeness is only a moderate guide [8, O] | **Moderate** for care, **Gap** for stakes on ratings | About 2 to 3 days; needs your rival list and standings math | Sim against oracle. Override within 30 s on affected picks, inside tie margin |
| 7 | **The Close** (M7) | At the last real peak: ends with a 60 to 90 s best-of montage, best moment last, then stops. Your chosen end time is honoured | Peaks and endings drive memory, not length [57, S]; self-set limits work [43, A]; a decision moment matters [7, O] | **Strong** for peak-end (not opened), **Moderate** for self-set limits | Cue and controls about 1 day (`phone.commit`). Montage about 2 days, **blocked by `reel.play`** | Alternate whole nights. `night_close` minutes after cue, worth-it, regret, agency |
| 8 | **Burst-then-hold rhythm** (M8) | After a run of quick cuts, one held scene. After a big moment, about 7 s of reaction | Breaks reset adaptation [2, O]; slower consumption changes what people want [51, A]; fast plus arousing plus unrelated is the risky corner [63, S] | **Weak** for cutting between live games. Sign may differ by person [2, O] | About 1 to 2 days, mostly config and sim | Alternate nights for overall feel; randomise the 7 s hold per score. Must not raise dead air or misses |
| 9 | **Audio pass** (M9) | Inputs match in level. No loudness jump at the cut. Phone chime only on real top-tier events | Level jumps are avoidable friction; false alerts erode response [5, O] | **Weak** for the level match (common sense, unmeasured). **Moderate** for reserved alerts | About half a day | Hand-rated level match before and after; chime false-alert count = 0; alternate nights |
| 10 | **Taste card** (M10, rung 0) | The TV's early-game picks reflect what you declared | Explicit signals drove personalisation [6, O]; stated preferences beat engagement on satisfaction [47, A]; keeps the tap rule intact | **Moderate** by analogy | About 1 day phone-only | Scored only on the frozen grader. Profile lab report at 300+ clean rows. A person changes the config |
