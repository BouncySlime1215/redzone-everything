---
name: redzone-north-star
description: Full RedZone Everything requirements, league order, merge bar, and how Nick wants work run (cloud project coordinator)
metadata:
  type: project
---

What Nick wants from RedZone Everything (as of 2026-11-03):

**Product**
1. **Feel like NFL RedZone.** Switch game to game instantly: no Fire TV home screen, no clicking around. The biggest pain right now is the 30-50 s HOME/HDMI switch.
2. **Cut at the right moments.** Leave only at dead time (commercials, timeouts, free throws, challenges, injuries) and enter at the restart. 0 mid-play cuts or entries. Switch MORE often, but always at good points, never randomly.
3. **Catch every key moment, crunch time first.**
4. **League order:** NFL > CFB > NBA > NCAA basketball > NHL > MLB. Teams: Commanders, UNC, Wizards, Capitals, Nationals. Teams and bets have their own priorities.
5. **NHL:** power plays, goals and overtimes.
6. **Catch-up mode:** 5+ clips back to back in 30-60 s, only what he needs to see. Timing is super important; play it when a game lingers in dead time.
7. **Sync is automatic.** No taps, no mic or camera. Nick reaffirmed on Nov 4: "no manual work, no extra setup, I'm not changing anything; pure code + AI". The Noggin must match the TV output from digital signals only: on-screen ad-break anchors read over ADB, media-session state, the set offset, and learned per-channel delay priors. No tap-sync or calibration chores.
8. **Never Peacock** ([[redzone-no-peacock]]).
9. **Fully autonomous.** A watchdog fixes problems by itself.

**AI**
- "Low and insane": low RAM (about 1.5 GB total for all local models on an 8 GB M2) but very smart.
- A watchdog model that can fix any problem.
- A separate live game-reasoning model that reads every game live and locks in.
- Train on servers, never heavily on the Mac ([[mac-load-limits]]).

**Hardware facts:** TV is a Samsung 6 Series that came with the apartment (The Union Chapel Hill, WhiteSky Wi-Fi). Two Fire TV sticks. See [[redzone-home-network]]. "Mac as the HDMI source" was REJECTED; keep the sticks and TV.

**How Nick runs the work now**
- Builds happen in the claude.ai Code Project "Training model for redzone" (coordinator + threads, repo BouncySlime1215/redzone-everything, base branch codex/resume-redzone, draft PRs into it).
- My job: lock in context, steer the coordinator and its threads, and check for bugs and build faults. Make sure everything integrates as ONE system, not a sum of parts.
- USAGE RULES (updated Nov 4, 12:30 AM; this replaces the Nov 3 "90% by Wed 5 PM" plan):
  - Nick has TWO Claude Ultra accounts: this one (upgraded Nov 4, weekly reset Mon 8:00 PM ET) and the new one from Wed Nov 4, 5 PM.
  - Spread work across both over the whole week. "Don't burn a week of usage in a day."
  - Pace each account in a straight line from its reset to 90% at the next reset (about 13-15%/day). Check it hourly on claude.ai/settings/usage.
  - Keep the last 10% of each account for school. At >=89%, pause the project and stop the director crons on that account.
  - More than 5 points ahead → 1 thread. Behind → up to 5 threads.
  - Research agents are expensive: on Nov 3 the 9 Fable max-effort agents plus 5 threads took 48% → 63% in 50 minutes. Use them sparingly and prefer cheaper models.
- He chose to keep ALL project threads running in parallel even though usage was on pace to hit the weekly limit early (26% used on Tue Nov 3). Once those finish: at most 5 threads at once, the rest queued. Don't re-ask; mention usage only when it nears the limit.
- He worries the project is discombobulated. Coordination is the coordinator's #1 job: architecture contract (docs/architecture.md), one owner per area, shared WorldState / clock / config, contract tests, golden nights, status board (docs/status.md), and one release PR that works as a whole.
- "95% good minimum" release gate on held-out nights: 0 mid-play cuts; >=95% dead-ball landings; >=95% big plays seen per league; your-team 100%; reels >=95% in dead time; reasoner >=95% vs gold with unsafe leaves <=0.5%; watchdog >=95%; sync <2 s on >=95% of samples; >=95% preloaded switches under 3 s. A part that misses its gate ships OFF behind a flag.
- No overfitting. Split by time (whole nights/games). Keep a locked final test set of 25%+ of nights that is scored only at release. Evaluate on fresh post-cutoff nights every round, run forward-chaining CV, report the train/test gap, run leakage checks in CI, and require per-slice gates. 95% gates are judged only on unseen data.
- AI = two tiers. REFLEX: a local ~2 GB student distilled on Kaggle (2x T4, account nmatta1215 / user nickmatta, private dataset redzone-distill; round 1 started Nov 3 2:33 PM; round 2 = millions of ESPN moments built inside Kaggle). BRAIN: Claude auto-repair via a GitHub issue → claude-code-action PR → gates → safe deploy. It must use his subscription OAuth token, which he adds himself.
- HANDOFF: keep docs/handoff.md (STATUS block) updated every 10 min and push it. Nick moves to a NEW Claude account (Ultra) on Wed Nov 4 at 5 PM; setup steps and bootstrap prompts are in docs/new-environment.md. The memory snapshot lives in docs/context/claude-memory/. A "Memory systems" project thread builds CLAUDE.md, docs/memory/ and scripts/context_pack.py.
- Merge bar: full tests pass, scorecards show a clear win, 0 mid-play cuts, nothing important worse.
- $0 budget; ask before spending. Never enter passwords. No disruptive TV tests while he's watching. He says when to push.

**Why:** These were stated across the Nov 2-3 sessions; this is the consolidated spec.

**How to apply:** Check every coordinator thread and PR against this list. Keep ADHD-style replies.
