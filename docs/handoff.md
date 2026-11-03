# RedZone Everything: handoff

The living handoff for whoever (human or AI) picks this up. The director updates it every
10 minutes while it runs. Read this first, then `docs/status.md` (the project board) and
`docs/architecture.md` (contracts and owners).

## 1. What this is

A self-hosted Mac app (Python/FastAPI/asyncio, launchd service `com.redzone.everything`, port 8778)
that runs a Samsung TV like NFL RedZone across every sport. Two Fire TV sticks over ADB play the
games, and the app switches between them at dead balls. The owner is Nick Matta (UNC student).

## 2. What Nick wants (north star; full list in `docs/context/claude-memory/redzone-north-star.md`)

- **Switching:** instant game-to-game switches, with no Fire TV home screen and no clicking. Cut only
  at dead time (commercials, timeouts, free throws, challenges, injuries) and enter at the restart.
  **0 mid-play cuts or entries.**
- **League order:** NFL > CFB > NBA > NCAA basketball > NHL > MLB. His teams are WSH (Commanders,
  Wizards, Capitals, Nationals) and UNC; teams and bets have their own priorities. Crunch time comes
  first. NHL: power plays, goals and overtime.
- **Catch-up reel:** 5+ clips back to back in 30–60 s, played only in dead time, with timing dialed.
- **Fully automatic:** sync is automatic (no taps, no mic or camera). Never use Peacock (NBC/NBCSN
  come via YouTube TV).
- **AI, "low and insane":** about 1.5–2 GB of RAM total on an 8 GB M2, but very smart.
  - REFLEX: a local distilled student (Qwen3-1.7B, 4B if needed).
  - BRAIN: Claude auto-repair through GitHub `claude-code-action` on his subscription token.
  - A live game reasoner.
- **Release gate:** 95% on locked, unseen data, with no overfitting (time splits, locked test,
  fresh nights, per-slice gates). Anything that misses ships OFF behind a flag.
- **Constraints:** $0 budget (ask before spending). Never enter passwords or tokens. Keep the Mac
  light (8 GB). No disruptive TV tests while he watches. **Stop all Claude work at 88% weekly
  usage** (he keeps 10% for school). Keep ADHD-style replies: lead with the action, short.

## 3. Where everything lives

| Thing | Where |
|---|---|
| Repo | github.com/BouncySlime1215/redzone-everything (private). Base `codex/resume-redzone`, integration `integration/next`, release `integration/release` |
| Mac checkout | `/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone` (the service runs from here) |
| Cloud build project | claude.ai Code Projects: "Training model for redzone" (`chan_011rPfhndEhhnAPuwfcdBQK8`). A coordinator runs about 5 owner threads |
| GitHub on claude.ai | connected as **BouncySlime1215** (switched from nmatta1215-svg on Nov 3) |
| Kaggle | account `nickmatta` (nmatta1215@gmail.com), phone verified, 2x T4, 30 GPU-h/week. Datasets: `nickmatta/redzone-distill` (round-1 pack, split .bin parts), `nickmatta/redzone-code` (round-2 code, auto-extracted to `code/`). Notebook: `nickmatta/notebooka466d7f817` |
| Watchdog | launchd `com.redzone.agent` (rules only, `--no-model`); plist in `agent/` |
| Memory snapshot | `docs/context/claude-memory/` (the director's memory files) |

## 4. Hardware facts

- **Fire TV sticks:** Fire TV Stick 4K Plus at 100.110.165.150 on HDMI2, and Fire TV Stick 4K
  (2023) at .181 on HDMI1.
- **TV:** Samsung 6 Series 55" that came with the apartment (The Union Chapel Hill, WhiteSky
  Wi-Fi). MAC 8c:79:f5:8e:e2:5f. The IP .151 was never verified.
- **Why network TV control failed:** almost certainly the Tailscale 100.64/10 route overlap or a
  wrong IP. Check `.venv/bin/python -m redzone.network`.
- **Today's switch:** HOME + CEC. It shows the launcher and makes Prime reload (5–50 s). The fix is
  TV-side input switching (`scripts/tv_setup.py`), plus a YouTube TV Multiview probe.
- **The Mac travels to school** (campus network 10.23.x). Then the sticks look offline; they are not dead.

## 5. Current state (updated by the director)

<!-- STATUS:START -->
- **2026-11-03 6:45 PM ET.** Live service: this MORNING's code. `integration/next` 35ca936 is green (#39 merged). `integration/release` is still RC1 9e859b7 (failed CI). The coordinator got the RC2 directive at 6:41, due 7 PM.
- **Open PRs:** #13 #16 #18 #20 #23 #24 #26 #30 #37.
- **Kaggle round 1, V2:** running 4 h 03 m; teacher at 11,520 of 81,372 (~61 tok/s). It stops at its 7 h budget (~9:50 PM); ETA ~1 AM. The round-2 notebook fix (auto-extracted code/) is in progress on the #13 thread.
- **Usage:** 47% (on pace; up to 3 threads allowed).
- **Waiting on Nick:** add NFL RedZone + RedZone Replay to the YTTV library. Tonight: docs/tonight-checklist.md (deploy once RC2 is green). **Wed 5 PM:** new account.
<!-- STATUS:END -->

## 6. Next steps, in order

1. Cut `integration/release` (95% gate report attached, flags off for misses) → Nick deploys tonight.
2. Kaggle: when V2 finishes, download `out.zip`, grade it against the gates, then start round 2.
3. Decision core → highlight jump + auto-tune on top. Next come data sources tier A (1–11), the
   reasoning add-ons (game memory, handbook, scouting), and the auto-fix BRAIN (Nick adds
   `CLAUDE_CODE_OAUTH_TOKEN` himself).
4. Data sources tier B (12–31).

## 7. How the director works (so a new session can take over)

- **Every 10 minutes, in one pass:** usage guard → PRs/CI → Kaggle log → project threads → one
  concise directive if something's off → update §5 of this file. Only message Nick when he must act.
- **Project rules:**
  - max 5 working threads; paused threads stay paused until their dependencies land
  - every PR targets `integration/next`; merge small, CI green, scorecards no worse
  - 95% gates on held-out data
- **Kaggle uploads:** the Chrome upload tool caps files at 10 MB (split into .bin parts), and
  Kaggle auto-extracts .zip/.tar.gz.
