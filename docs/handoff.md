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
- 2026-11-03 ~4:15 PM ET. Live service: this MORNING's code (not deployed yet).
- **integration/next:** green. Merged so far:
  - trip budget (#1), NHL (#3/#22/#27), NFL drive priority (#2), sync (#8/#25), CI (#7)
  - pinned eval data (#14), traces (#10), network awareness (#12), platform (#15/#20/#23/#24)
  - instant switching (#5, off until TV setup), deploy (#9/#29), governor (#6)
  - Brain v3 (#26, acting parts off), reasoner plumbing (#17, off), e2e night (#19/#28)
  - tonight checklist (#31), multiview path (#32, off), fastcast check (#33)
  - Decision core #21 merging.
- **Kaggle round 1, Version 2:** running since ~2:47 PM. The teacher (Qwen3-32B-AWQ) was at 4,352 of
  81,372 labels (~45–65/min), then the students, grading, and `out.zip`. ETA ~1 AM.
  - Version 1 failed: `run_teacher.py` read the `.gz` as text. Fixed in the notebook and in the repo.
- **Kaggle round 2:** ready on branch `kaggle-inputs` (millions of moments built from ESPN inside
  Kaggle). It starts after V2 finishes and needs the notebook fix for the auto-extracted `code/`.
- **Usage:** 44% weekly (Nov 3, 4 PM). The cap pauses the project at 88%.
- **Waiting on Nick tonight (about 30 min):** `docs/tonight-checklist.md`: wall power for the
  sticks, quit Tailscale, TV settings (Anynet+, IP Remote), the deploy command, `tv_setup.py`
  (press Allow), the Multiview probe.
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
