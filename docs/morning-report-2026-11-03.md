# Morning report — Tue Nov 3, 2026

All merged work is on branch `codex/resume-redzone`. Nothing is pushed to GitHub. The live service was
restarted at 3:36 AM onto the merged code, with no games on: no errors, the supervisor is running, and both
sticks are connected.

Rule for every merge tonight: the full test suite must pass, and the scorecards must show it's a clear win,
with 0 mid-play cuts and nothing important getting worse. Anything mixed stays on its branch.

## Live now

| Change | Proof |
|---|---|
| **Sports brain**: per-league models trained on about 6M real plays. They predict a break or timeout in the next minute, a key moment soon, and game flow (0-100) | Beats the old rules at predicting breaks in every league on games it never trained on; key moments better except NHL (a tie); 0 mid-play cuts |
| Game-flow graph per game on the phone (like ESPN's) | Shown in the game sheet |
| **Self-healing supervisor**: fixes home screens, Prime pages, stuck switches, idle autopilot with games on, dead sources, stale feeds and stick drops; writes a nightly report | Review's 3 blockers fixed and tested (restores can be cancelled, Wi-Fi timeouts don't mute an app for the night, black copy-protected frames are never read as "frozen") |
| **Cut timing (NFL)**: leave at dead time, land on the dead ball before the snap (short DVR rewind) | NFL big plays seen live 24.5% → 29.9%, big plays seen 94 → 95%, crunch live 68.7 → 70%, your team's big plays 96.6 → 100%, 0 mid-play cuts, 0 mid-play entries |
| **Prime fixes from the hardware soak test**: presses through Prime's page after a flip (about 5 s instead of about 50 s), a missing standby read is re-asked, a screenshot timeout doesn't crash | Tests |
| Brain only logs a broken model once; it waits about 40 plays before predicting after a restart | Tests |
| Safe mode isn't triggered by "TV input unknown" refusals; crunch-time games stay preloaded through their timeouts | Tests |
| **Your team late and close is never abandoned**: in its final period and not garbage time, your team (or bet) only gives up the TV to crunch time elsewhere | The Oct 26 Wizards finish went from **0 of 13 crunch plays to 13 of 13**. NBA your-team crunch live 58% → **100%**, crunch live 95.7 → 96.3%. Cost: 3 neutral NBA big plays (95.4 → 92.0% seen). NFL and NHL identical |
| **No chained break visits**: while visiting another game during your game's break, that game's own break sends you home instead of on to a third game (the Wizards case) | NFL big plays seen 95.0 → 95.4, everything else identical |

## Built but not merged (and why)

- **Cut timing for NBA/NHL**: it cost NBA big plays seen (95.4 → 90.8) and NHL crunch live (87.5 → 82.7), so it's switched on for NFL only.
- **RedZone mode** (NFL drive priority and catch-up highlight reels): on top of cut timing, NFL big plays seen 95.0 → 93.4 and your team's 100 → 93.1. The reels kept it behind live 18% of the time. Needs a rework: shorter reels, and only during real dead time.
- **Most of miss-fixes**: compared on the same settings, it's neutral on NBA and slightly worse on NFL (big plays seen 94.4 → 92.8, crunch live 70.0 → 67.9) and NHL (crunch 87.5 → 86.8). I first reported an NBA "your team's crunch 58 → 100%" gain, but that came from the settings drift described below, not the code.
- **Break-visit trip budget** (skip a visit when the trip there and back doesn't fit inside the break): NFL crunch live 70.0 → 71.4%, but big plays seen 95.4 → 93.9% and your team's big plays 100 → 93.1%. Nearly all of the loss is one Sunday (CLE @ CHI), where today's code shows 14 minutes of catch-up replays and the branch stays on a live drive instead. Mixed, so it stays on branch `fv-review`.
- **NBA/NHL entry timing with a wait cap** (branch `entry-wait-cap`): 0, 8, 15 and 25 s caps all score the same. Turning NBA timing on changes only one of 14 nights (Oct 26): +5.7 points of NBA big plays seen, but it leaves the Wizards at 0:22, down 7, and they cut it back to 5. One night isn't enough evidence to merge.
- **Hype v2** (learned key-moment score): NFL big plays seen **live** 29.9% → **39.5%**, which is big, but your team's NFL big plays fell 100 → 93.1, NBA big plays seen 95.4 → 90.8, and NHL crunch 87.5 → 83.8. Capping non-crunch scores didn't fix it. Not merged. Its chained-visit fix *was* merged on its own.

## A scorecard pitfall, fixed

The live service saves newly measured switch times into `data/config.yaml`, and the scorecards read the same
file. At 3:39 AM that moved the baseline by itself (NFL your-team big plays 100 → 93.1 with no code change).
Scorecards now read a pinned `data/eval_config.yaml` when it exists, so before/after comparisons stay fair.

## Found and fixed tonight

- **Today's code enters NBA games mid-play 50 times and NHL 163 times** across the test nights (the new `entries_mid_play` scorecard). This is the "cuts in mid-play" you saw, and it's the top item for the next round.
  - With cut timing on everywhere, mid-play entries dropped to **0**, but it held switches for about 69 s of dead time per entry, and that cost crunch plays elsewhere.
  - Most NBA games are on **Prime (League Pass), which can't rewind live**, so it can't back up to the free throw the way YouTube TV can. Short rewinds only (20 s or less, never waiting) never found a clean landing.
  - A test for the Prime pause idea (a, below) is written and ready to run tonight with a live Prime game on the off-screen stick. It checks whether pause works on live Prime, whether play resumes from the paused point, and how fast.
  - Next ideas: (a) on Prime, pause the loaded game on the off-screen stick at the dead ball, then press play when switching to it (needs a hardware test with live games); (b) time a preloaded 3–5 s flip into free throws and timeouts once the delay is measured tightly.
- A cut-timing check could have blocked NBA/NHL switches live. It now runs for the NFL only.

## NBCSN on YouTube TV, so Peacock games skip Peacock (NEW, works)

- **Tune-by-search built and proven on the HDMI2 stick:** with no watch code, the app searches YouTube TV for "NBC Sports Network", goes to the network page, then **Live**, then the live tile, then OK. The stick confirmed "Liverpool vs. Arsenal, NBC Sports Network" in **19.6 s**, starting from TNT.
- Peacock games (NBCSN nights) now route to YouTube TV NBCSN first. Peacock is only a last resort.
- Along the way, fixed two bugs: a word a hair off the screen edge made the text reader reject the whole screen, and a 1–2 pixel shift of the focus ring between screenshots aborted selections.
- **Local NBC (WRAL) works too:** searching "NBC" opens the NBC network page, and its Live row is your local station ("Early Today, WRAL", 20.0 s).
- **NHL Network isn't in your YouTube TV plan.** Searching it only shows clips from NHL Network's YouTube channel, with no live channel (it's part of the Sports Plus add-on). Games that are only on NHL Network can't be shown.

## Still on you

- **The HDMI1 stick (Fire TV Stick 4K 2023, .181) went offline at about 3:41 AM.** It doesn't answer ping and isn't on the network. Most likely it lost power: is it powered from the TV's USB port? If so, plug it into the wall adapter so the TV turning off can't kill it. The app runs single-stick until it's back, and reconnects by itself.

- Sign in to Peacock on the second stick (HDMI2), once, with the remote.
- Plug in the Mac.
