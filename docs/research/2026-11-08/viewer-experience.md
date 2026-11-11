---
id: R-VIEWER
title: "Viewer experience of automated multi-game sports (research, 2026-11-08)"
date: 2026-11-08
stream: context
author: "unrecorded (2026-11-08 research agent)"
status: reviewed
supersedes: []
sources: 24
verified: 19
affects: []
gate: []
---

# Viewer experience of automated multi-game sports (research, 2026-11-08)

Status: research only. No code changed, no default or gate changed. Date: 2026-11-08.
Scope: how RedZone-style hosts pace switches, what the research says about cuts, context after a
switch, trust and control in automation, explanations, alerts, the phone as a remote, and ADHD.
Builds on `docs/research/attention-science.md` and `watch-science-playbook.md` (does not repeat
their suspense/peak-end material). Evidence labels: **Strong** (replicated/meta-analysis),
**Moderate** (solid single studies or well-designed observation), **Weak** (theory, one small
study, or our own inference). Sources marked *(older)* pre-date 2024. Every source in section 4 was
opened or checked against its DOI/abstract in this session; how each was checked is noted there.

## 0. What I could NOT find (read this first)

- **No published switch rate for NFL RedZone** (cuts per hour, dwell times). Nothing from the NFL,
  Nielsen or a paper. Any number you see on a blog is unsourced. Do not cite one.
- **No peer-reviewed study of score bugs or situation overlays** on comprehension after a cut. The
  closest items are eye-tracking studies of sponsor signage and a vendor study. So the "orientation
  beat" is justified by interruption and memory theory, not by a direct test.
- **No ADHD-specific study of watching multiple games or of resuming after a channel switch.**
  ADHD evidence below is about executive function in general, attentional set shifting and phone
  interruptions. Treat the ADHD design implications as inference.
- Nick's own pass bar (7.5 jumps/min, handoff.md, Nov 7 22:15) is far faster than anything in the
  interruption literature, which studies seconds-to-minutes tasks, not sport. The research cannot
  say 7.5/min is wrong. It says each landing must be cheap to understand. Measure it (section 3).

## 1. Ranked UX principles with evidence

Ranked by (strength of evidence x how much it changes RedZone's design).

### P1. Interrupt only at a boundary, never inside a task. (Strong, directly matches Rule #1)
- Interruptions at subtask boundaries cost less in emotional state and performance than interruptions
  mid-subtask (Adamczyk & Bailey 2004, *older*; cited by later interruption work). A dead ball is the
  sports version of a subtask boundary.
- Real RedZone treats this as craft: the host introduces a delayed play with a segue such as "while we
  were watching that..." so a time-shifted play does not feel like a cut-in (Wikipedia summary;
  Hanson also asks on air to switch games, InsideHook, *older*).
- Implication: Rule #1 is the best-supported rule in the whole project. Do not trade it for cadence.

### P2. Every cut creates a resumption cost; pay it down with cues at the moment of arrival. (Moderate)
- Suspended goals decay; resumption lag grows with time away, and cues that are attended add
  activation (Altmann & Trafton 2002, *older*, model fitted on a puzzle task, not TV).
- Cuts and edits draw involuntary attention and use limited processing capacity (Lang 2000, *older*;
  the limited-capacity model, applied to TV).
- Implication: the longer a game was away, the more context it needs on return. Short-gap returns
  need almost none; long-gap returns (a minute or more) need a catch-up. This is the logic of the
  planned drive catch-up story (`docs/research/2026-11-08/catch-up-story.md`).
- Caveat: cue timing and size are untested for sport. This is the weakest-evidence, highest-impact
  area; A/B it.

### P3. Predictable rules beat clever rules; trust is built on visible performance, process and purpose. (Strong for trust theory; Moderate for this setting)
- Appropriate trust is calibrated, not maximal, and depends on what the automation did (performance),
  how (process) and why (purpose) (Lee & See 2004, *older*).
- Automation surprises come from unseen mode changes ("how did I get into this mode?") (Sarter &
  Woods 1995, *older*). Microsoft's 18 human-AI guidelines include "limit disruptive changes" and
  "provide global controls" (Amershi et al. 2019, *older*).
- Implication: keep the policy stable within a night; always show the mode (auto, paused, locked,
  held, safe, frozen). The phone already has banners for most of these (`app.js` 70-77). Never change
  cadence silently mid-night.

### P4. Even small control raises acceptance of an imperfect automation. (Strong for the effect; Moderate transfer)
- People used an imperfect algorithm more, and were more satisfied, when allowed to modify its output
  even a little; the size of the allowed change barely mattered (Dietvorst, Simmons & Massey 2018,
  *older*, forecasting tasks).
- In video apps, autoplay and recommendations undermine sense of agency, while search and playlists
  support it; users with a specific intention want more agency (Lukoff et al. 2021, *older*;
  120-person survey plus 13 co-design sessions).
- Implication: a one-tap Lock, Skip, Pause and Go back, with obvious state, matter more than their
  rate of use. Their existence is the trust mechanism. (They all exist: `rz-now.js` dock, `rz-v2.js`
  "Stay here".) The risk is forgotten holds, not missing controls.

### P5. Explanations help when they are cheap to read; completeness matters more than polish. (Moderate)
- Completeness of an explanation improved users' mental models more than soundness did; very
  oversimplified explanations raised mental demand and cost trust (Kulesza et al. 2013, *older*).
- Explanations reduce over-reliance when they lower the cost of checking the AI's answer (Vasconcelos
  et al. 2023, five studies, N = 731, maze task, simulated AI).
- Guideline 11: "make clear why the system did what it did" (Amershi 2019).
- Implication: "why did it switch?" must be readable in about one second while the eyes are on the
  TV, with the fuller reason one tap away. A reason that needs reading is not used.

### P6. Be quiet. Useful notifications still disrupt, and notifications raise ADHD-like symptoms. (Moderate)
- Even useful notifications disrupted; presentation, task and personality changed the cost (Mehrotra
  et al. 2016, *older*, 10,372 notifications, 20 users).
- Week with phone alerts on vs off: higher self-reported inattention and hyperactivity with alerts on
  (Kushlev, Proulx & Dunn 2016, *older*, N = 221, general population; the authors say it neither shows
  phones cause ADHD nor that silencing treats it).
- Implication: the phone should add no sound or buzz per switch. Alert only for things Nick asked
  for. See section 2, alerts.

### P7. Feedback timing: acknowledge a tap in about 0.1 s, show progress by 1 s, explain by 10 s. (Strong, long-standing)
- Nielsen's three response-time limits (0.1 s feels instant, 1 s keeps flow, 10 s is the attention
  limit) (NN/g, from 1993, updated 2014, *older*).
- Implication: the existing "Switching to X..." then "On your TV" flow (`rz-v2.js` ~749) is the right
  pattern. The north star "phone press to TV p95" is the number that decides whether it feels
  instant. Because a cut waits for a dead ball, the bar must say "Waiting for a safe moment" at once
  (it does).

### P8. Pacing: fast is normal for RedZone, but it adapts to the slate, and viewers punish anything that breaks flow. (Weak to Moderate)
- RedZone shows one game, or splits to two, three, four, five, eight or ten boxes when many scores are
  developing; late in the early window it narrows to one-possession games (within 8 points); it claims
  every touchdown, though some air with a 30-60 s time shift (Wikipedia summary, secondary).
- Cuts are chosen by a human control room watching every game (LAIST 2013, *older*: "People have
  turned over their remote controls to us"). Trust in the picker is built over years: Hanson says a lot
  of trust had to develop before he changed plays (InsideHook, *older*).
- The 2025 backlash over ads shows how sensitive viewers are to broken flow: ads grew from four
  15-second spots in Week 1 to eight double-box spots in a December Sunday, and fans said these differ
  from normal breaks (Barrett Media Oct and Dec 2025). Nick's stated worst failure is sitting through
  commercials on his team's game, which matches.
- Implication: cadence should follow how many live situations exist, not a fixed clock. High cadence
  when many games are at dead balls and meaningful; slow or hold when one game is the whole story
  (the existing crunch hold does this). No source here gives a "right" cuts-per-minute.

### P9. For ADHD, reduce what must be held in working memory, reduce decisions, and never add interruptions. (Moderate evidence for the traits; inference for the design)
- Meta-analysis of 83 studies: moderate impairment across executive-function tasks in ADHD, largest
  for response inhibition, vigilance, working memory and planning (Willcutt et al. 2005, *older*).
- Adults with ADHD showed a selective impairment of attentional set shifting (Behav Brain Funct 2018,
  *older*, abstract only checked via search) and the task-switching literature is mixed for children.
- Average attention on one screen fell from about 2.5 minutes (2003) to about 47 seconds (2020); half of
  interruptions are self-inflicted; recovering from one can take much longer than the interruption
  (Mark, UC interview 2024; method details are in her papers, not the interview).
- Interrupted work finishes faster but with more stress, frustration, time pressure and effort; need for
  personal structure and openness predict the cost (Mark, Gudith & Klocke 2008, *older*).
- Implication: the phone is Nick's external working memory ("what am I watching, why, what did I
  miss"). Keep it a glance surface with at most 3-4 primary controls, big targets, and no streaks or
  guilt. Hypothesis, untested: high-stimulation cadence may suit him better than average; let his own
  per-switch votes and "worth it" ratings decide, not this literature.

## 2. Concrete changes for RedZone, mapped to files

Paths relative to the worktree. Everything is a proposal. Each needs a North Star line and a measured
delta per CLAUDE.md. Most live behind existing flags that are OFF today.

### 2.1 Switch pacing
| Change | Where | Why (principle) |
|---|---|---|
| Keep Rule #1 as the only hard pacing rule; treat cadence as a tunable that depends on the number of live "meaningful" games at dead balls, not a target | `redzone/decider/decider.py`, `redzone/default_config.yaml` (`decider.dwell`, min_s 0 = off today) | P1, P8 |
| Log and score ping-pong (A to B to A within N s) and quick re-entries as guardrail metrics; the old limits `decider.ping_pong_freeze_s`, `min_dwell_s`, `max_switches_per_5min` are retired in the Settings UI (`rz-settings.js:42`), so only the "Holding still, flip-flopping" safety banner (`app.js:75`) remains | `redzone/lab` (new metric), `redzone/demo/engine.py` (already has `switches_per_hour`) | P3, P8 |
| Cadence floor by gap length: a return after a long gap gets a catch-up (the catch-up story), a return after a short gap goes straight to live | catch-up lane, `redzone/web/phone_v2.py:446 catchup_queue` | P2 |
| Never leave a high-leverage game for cadence (already true via crunch hold: `crunch.mine_first`, `floor`) | `default_config.yaml` crunch block | P8 |

### 2.2 Overlays and orientation
Key finding: RedZone cannot draw on the TV (two Fire TV sticks, no overlay path found in the code);
the only overlay surface is the phone. A card on the phone costs Nick a look-away from the TV (a
second-screen attention cost; CHI-style second-screen studies exist, I could not verify them, so this
is inference).
| Change | Where | Why |
|---|---|---|
| At 5-10 jumps a minute a new 3 s card each cut cannot be read. Show the beat only when the gap since this game was last shown exceeds a threshold, or the situation is not self-evident on the TV's own score bug; otherwise keep a single persistent "On TV now" line | `redzone/web/static/rz-v2.js` `beat()` (3 s corner card), `redzone/web/phone_v2.py` `orientation()` and `context_line()`, config `phone.orient`, `phone.orient_beat_s` | P2, P6 |
| Make the card glanceable: score and one fact only, big type; keep the one-question context line only when late and close (already the design: `CONTEXT_LATE_FRAC`) | same | P5 |
| Test, do not assume: A/B the beat on vs off by whole night, judged by "overrides within 30 s" (`phone.orient_override_s`) and per-switch thumbs | `redzone/lab/orient.py` (grader exists) | P2 |
| Consider an optional spoken or audio cue only if Nick asks; no evidence supports it for sport | n/a | P6 |

### 2.3 "Why did it switch?" on the phone
| Change | Where | Why |
|---|---|---|
| One persistent line at the top of Now: "[Game]: [reason chip]" that updates at each switch, tap for the full reason and score at the time. Reuse `why_tag()` and the chips already built | `redzone/web/phone_v2.py` `chips()`, `why_tag()`, `redzone/web/static/rz-now.js` `plan()` ("Staying on X: reason") | P5 |
| Show "why NOT" for the loud case: when a close game elsewhere is ignored, say why ("holding: your team, 4th quarter") | `phone_v2.py` `also_close()`, `rz-v2.js` also-close strip (flag `phone.also_close`, OFF) | P3, P5 |
| Keep reasons honest and tied to logged thresholds (`phone.why_tag_min`, 70) | `phone_v2.py:632` | P3 |
| One-tap per-switch thumbs stay in the Recent switches log, not on the main screen | `rz-v2.js` `switchExtras()` | P6, P9 |

### 2.4 Controls
| Change | Where | Why |
|---|---|---|
| Timebox every hold: "Stay here" ends at the game's end or at a chosen limit, and the state is always visible ("Holding... until you tap Resume auto", `rz-now.js:108`). Forgotten holds are the main failure of control | `rz-now.js`, `rz-v2.js` commit flow (`phone.commit`, OFF), `redzone/control.py` | P4 |
| Keep the dock to the few controls Nick uses most (Skip, Pause/Resume, Go back are in the dock; Lock, Boost, Mute, Set channel, thumbs sit in the More sheet) and audit tap-target sizes against WCAG 2.2 SC 2.5.8 (at least 24 by 24 CSS px, AA) and ideally 44 px | `redzone/web/static/app.css` (`.dock-btn`, `.act-tile`) | P7, P9 |
| Keep idempotent taps (ids, dedupe): a second "Watch this" is "Go now", so a retry must not double-fire. Already built | `redzone/commands.py` | P7 |
| A visible mode pill that is always on screen: Auto, Paused, Locked, Held, Safe, Frozen | `app.js` banners (70-77) | P3 |

### 2.5 Alerts and notifications
Today the phone has one alert, the your-team crunch clash warning 30 minutes ahead
(`phone_v2.py:1052 ALERT_AHEAD_S`, `clash_alert`), and uses `vibrate()` for taps. No push or
Notification API was found.
- Alert only for things Nick opted into (his team's crunch, a failure that needs him such as a lost
  hold or safe mode). No sound or buzz per switch (P6).
- Keep taps haptic-only and tiny; never buzz on automatic events.
- Batch non-urgent items into the morning "Last night" card (`phone.last_night`, OFF).

## 3. Metrics to measure UX on a live night

All derivable from existing logs (switches, taps, votes) unless marked NEW.

| Metric | Target or use | Source |
|---|---|---|
| Overrides within 30 s of an automatic switch | Lower is better; the main "wrong or confusing" signal | `lab/orient.py`, `phone.orient_override_s` |
| Per-switch thumbs up rate, split by gap-since-last-shown bucket (under 30 s, 30-120 s, over 2 min) | Does a short gap or long gap hurt? | `/api/v2/vote`, switches |
| Ping-pong count (A to B to A within 60 s) and re-entry count | Guardrail, not a target | NEW in `lab` |
| Switches per minute per window, and thumbs by cadence band | Find Nick's comfortable cadence; do not assume 7.5 | `demo/engine.py switches_per_hour` extended |
| "What game is this?" proxy: taps on Now or game detail within 10 s of a switch | Orientation failure | NEW (tap log) |
| Phone press to TV p95 (0.1 s / 1 s / 10 s bands) | Feels instant? | North Star line |
| Held-too-long: holds or locks lasting over N minutes, forgotten resumes | Control hygiene | `control.py` log |
| Mid-play joins and leaves, commercials on screen, dead air | Existing hard gates | North Star |
| "Worth it?" end-of-night rating (1-5) | Primary success signal over minutes | `phone.worth` (ON) |
| Alerts sent per night and how many Nick opened or acted on | Quiet is good | NEW |
| Nick's one-line post-night note on "too fast / right / too slow" (one tap) | Cadence calibration | NEW, optional |

Method: compare by whole night (as CLAUDE.md requires), no mid-night parameter changes, record which
flags were on. N = one person, so treat results as personal calibration, not generalisable science.

## 4. Bibliography (24 sources; how each was verified)

"Opened" = I fetched the page or PDF and read the relevant text. "DOI checked" = title, venue and year
confirmed through the Crossref record. "Search only" = confirmed by search result summaries; abstract
not opened; claims limited accordingly.

1. Adamczyk, P. D., & Bailey, B. P. (2004). If not now, when? The effects of interruption at different moments within task execution. CHI '04, 271-278. *(older)* Opened (PDF abstract). https://hci.rwth-aachen.de/materials/conferences/CHI2004/1p271.pdf
2. Altmann, E. M., & Trafton, J. G. (2002). Memory for goals: an activation-based model. Cognitive Science, 26(1), 39-83. *(older)* Opened (PDF abstract). https://www.interruptions.net/literature/Altmann-CogSci02.pdf
3. Mark, G., Gudith, D., & Klocke, U. (2008). The cost of interrupted work: more speed and stress. CHI '08, 107-110. *(older)* Opened (PDF abstract). https://www.interruptions.net/literature/Mark-CHI08.pdf
4. Mehrotra, A., Pejovic, V., Vermeulen, J., Hendley, R., & Musolesi, M. (2016). My Phone and Me: understanding people's receptivity to mobile notifications. CHI '16, 1021-1032. *(older)* Opened (PDF abstract). https://discovery.ucl.ac.uk/1502364/1/CHI16.pdf
5. Kushlev, K., Proulx, J., & Dunn, E. W. (2016). "Silence your phones": smartphone notifications increase inattention and hyperactivity symptoms. CHI '16. DOI 10.1145/2858036.2858359. *(older)* Opened (PDF abstract). https://www.interruptions.net/literature/Kushlev-CHI16.pdf
6. Lukoff, K., Lyngs, U., Zade, H., Liao, J. V., Choi, J., Fan, K., Munson, S. A., & Hiniker, A. (2021). How the design of YouTube influences user sense of agency. CHI '21. DOI 10.1145/3411764.3445467. *(older)* Opened (arXiv PDF abstract). https://arxiv.org/pdf/2101.11778
7. Dietvorst, B. J., Simmons, J. P., & Massey, C. (2018). Overcoming algorithm aversion: people will use imperfect algorithms if they can (even slightly) modify them. Management Science, 64(3), 1155-1170. *(older)* Opened (abstract page) and DOI checked. https://econpapers.repec.org/RePEc:inm:ormnsc:v:64:y:2018:i:3:p:1155-1170
8. Amershi, S., et al. (2019). Guidelines for human-AI interaction. CHI '19. DOI 10.1145/3290605.3300233. *(older)* Opened (Microsoft Research summary of the 18 guidelines) and DOI checked. https://www.microsoft.com/en-us/research/blog/guidelines-for-human-ai-interaction-design/
9. Vasconcelos, H., Jorke, M., Grunde-McLaughlin, M., Gerstenberg, T., Bernstein, M., & Krishna, R. (2023). Explanations can reduce overreliance on AI systems during decision-making. PACM HCI, 7(CSCW1). DOI 10.1145/3579605. Opened (arXiv abstract) and DOI checked. https://arxiv.org/abs/2212.06823
10. Kulesza, T., Stumpf, S., Burnett, M., Yang, S., Kwan, I., & Wong, W.-K. (2013). Too much, too little, or just right? Ways explanations impact end users' mental models. IEEE VL/HCC, 3-10. *(older)* Opened (abstract page) and DOI checked. https://eprints.gla.ac.uk/261399
11. Lee, J. D., & See, K. A. (2004). Trust in automation: designing for appropriate reliance. Human Factors, 46(1), 50-80. *(older)* DOI checked; abstract content via search only. https://doi.org/10.1518/hfes.46.1.50_30392
12. Parasuraman, R., Sheridan, T. B., & Wickens, C. D. (2000). A model for types and levels of human interaction with automation. IEEE Trans. SMC-A, 30(3), 286-297. *(older)* DOI checked; level descriptions via search only (not quoted here). https://doi.org/10.1109/3468.844354
13. Sarter, N. B., & Woods, D. D. (1995). How in the world did we ever get into that mode? Mode error and awareness in supervisory control. Human Factors, 37(1). *(older)* Search only (library records: SWOV, TRID). https://trid.trb.org/View/426376
14. Lang, A. (2000). The limited capacity model of mediated message processing. Journal of Communication, 50(1), 46-70. *(older)* DOI checked; claims about cuts via search summaries, treated as Moderate. https://doi.org/10.1111/j.1460-2466.2000.tb02833.x
15. Willcutt, E. G., Doyle, A. E., Nigg, J. T., Faraone, S. V., & Pennington, B. F. (2005). Validity of the executive function theory of ADHD: a meta-analytic review. Biological Psychiatry, 57(11), 1336-1346. *(older)* DOI checked; findings via search abstract summaries. https://doi.org/10.1016/j.biopsych.2005.02.006
16. [Authors not verified] (2018). Selective impairment of attentional set shifting in adults with ADHD. Behavioral and Brain Functions, 14. DOI 10.1186/s12993-018-0150-y. *(older)* DOI checked (title, year); findings via search only; cite by title only. https://doi.org/10.1186/s12993-018-0150-y
17. Mark, G. (2024). UC Newsroom interview: how to sharpen your attention and meet your goals. University of California. Opened. https://www.universityofcalifornia.edu/news/how-sharpen-your-attention-and-meet-your-goals-2024
18. Nielsen, J. Response times: the 3 important limits (1993; 2014 update). Nielsen Norman Group. *(older)* Opened. https://www.nngroup.com/articles/response-times-3-important-limits/
19. W3C. Understanding SC 2.5.8: Target Size (Minimum), WCAG 2.2 (2023). Opened. https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
20. Wikipedia. NFL RedZone (accessed 2026-11-08). Secondary source; used for split-screen names, narrowing to one-possession games, time-shifted plays and segues. Opened. https://en.wikipedia.org/wiki/NFL_RedZone
21. LAIST (2013). NFL RedZone: why watch one game when you can watch... *(older)* Opened; control-room facts and quote. https://laist.com/2013-11-26/nfl-redzone-why-watch-one-game-when-you-can-watch
22. InsideHook. Good Job: Scott Hanson. *(older, undated in fetch)* Opened. https://www.insidehook.com/culture/good-job-scott-hanson/amp
23. Barrett Media (2025-09-10). How Scott Hanson is guiding RedZone through changing times with NFL Media. Opened. https://barrettmedia.com/2025/09/10/how-scott-hanson-is-guiding-redzone-through-changing-times-with-nfl-media/
24. Barrett Media (2025-10-20). NFL RedZone commercial load increases causing outrage from fans. Opened; December 2025 follow-up (eight double-box ads) seen only as a search snippet. https://barrettmedia.com/2025/10/20/nfl-redzone-commercial-load-increases-causing-outrage-from-fans/

Looked at and rejected: a podcast-style page quoting "34% fewer task-switching errors" and "40-60% higher
resumption cost" for ADHD (untraceable); cavac.at and similar mirror sites (unreliable); the
Parks Associates multiview ranking (secondary reports only, not opened).

## 5. Next action (under 2 minutes)
Pick one flag to A/B first: `phone.orient` (3 s card) vs a persistent line. It is the cheapest change,
already has a grader (`redzone/lab/orient.py`), and tests the largest unverified assumption (P2).
