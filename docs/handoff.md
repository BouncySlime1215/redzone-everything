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
  light (8 GB). No disruptive TV tests while he watches. Keep ADHD-style replies: lead with the
  action, short.
- **USAGE RULES (Nick, Nov 4, 12:30 AM):**
  - Nick has **two Claude Ultra accounts**: this one, upgraded Nov 4 (resets **Mon 8:00 PM ET**), and
    the new one from Wed Nov 4 at 5 PM (its own weekly reset).
  - **Spread work across both over the whole week. Never burn a week of usage in a day.**
  - Pace each account in a straight line from its weekly reset to **90%** at the next reset, about
    **13-15% per day**. Check it hourly.
  - **Keep the last 10% of each account for Nick's school work.** At **≥89%**, pause the project and
    stop the director jobs on that account.
  - More than 5 points ahead of the line → cut to 1 thread. Behind the line → up to 5 threads.
  - Research agents are expensive (a max-effort Fable agent is about 250-400k tokens), so use them
    sparingly and prefer cheaper models.

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
- **2026-11-03 10:24 PM ET.** The Mac still runs this MORNING's code. **The RC3 deploy failed** (two_stick_recovers on the Mac). Root cause, found by the director: the night test's fake sticks at 10.0.0.x versus the Mac's real LAN make the real network monitor say AWAY, so the supervisor skips adb reconnects. Test isolation only; the real devices read HOME. The integration train is pinning the network monitor in the night test; then RC4, then the deploy.
- **TV:** SmartThings OAuth works: **10/10 switches confirmed, median 1.98 s**. Its token, OAuth file and device id are in the director's rc2 worktree data/. After the deploy, run `scratchpad/apply_tv.py <rc2wt> <repo>`: it copies them into the live data/, sets input_method=smartthings and fixes the port map (.150 = HDMI1, .181 = HDMI2), then restarts the app. Today's code can't use OAuth (PAT in .env only).
- **Kaggle V3** (students-only, reusing V2's 22,272 teacher answers) is running, started 10:13 PM, ETA ~1 AM; GPU quota 7.3 / 30 h used before it. redzone-code dataset v2 = the bf16 fix.
- **Open PRs:** #13 #16 #18 #20 #23 #24 #26 #37 #40. **Usage:** 48%.
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
