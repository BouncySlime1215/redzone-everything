# Cycle 03 plan (Start Planner), Wed Nov 4, ~12:15 to 15:30 UTC

**From:** [cycle 02 review](../02/review.md) (integration/next 6fb6e94). **Usage brake** (Nick, 5:25 AM ET):
only lanes ①-③ run until Nick's 8 AM ET audit (13:00 UTC); lanes ④-⑤ get tasks below **only if he reopens them**.
No outside research. Lean doc.

**Where we are (candidate, dev / locked):** mid_play 0 / 0 PASS. Big live NFL 53, NBA 77.5 / 69, NHL 45.
% of oracle (worst line) NFL 59 / 55, NBA 83 / 69, NHL 53 / 55, and **big live is the worst line in every league**.
Dead-air ceiling NFL 10.6, NBA 11.2, NHL 18.9%. press_to_tv sim p95 8.75 s FAIL. Coverage 32/79.

**The one idea:** big live is the gap, and the lever is measured: GAME-04 is blocked on 91% of its
attempts by on-screen delay doubt σ > 8 s (the what-if: σ ≤ 3 s ≈ +10 NFL, +4 NBA, +4 NHL). So this cycle
makes **σ honest and small** before the probe data arrives Thursday. Lane ① puts Pick in shadow, and
lane ③ fixes the one control line that FAILs.

## ① Noggin training: PICK v1 in the decider, shadow first (already started)
- **Looking for:** a `noggin.pick.enabled` hook (OFF) with the forest exported to numpy (p99 < 5 ms,
  RAM line), shadow logging beside the app, and a switch-rate cap. Then the full NBA sim on dev and
  locked. **Bar:** mid_play 0, your-team crunch 100%, and beating the candidate on big live NBA and dead
  air. Report % of oracle before and after; if it falls short, say by how much.
- **Next:** NHL factory nights on a GH runner, then NHL PICK.
- **Files:** `redzone/noggin/pick*.py`, `models/`, decider hook (flag only), tests.

## ② Noggin logic/sync: make σ honest and small in the sims
- **Looking for:** the break anchors (#78, logged) **feed the Kalman filter in the sims** (`syncsim`, flag
  as in #78), so σ shrinks after each on-screen break the way it will live. Proof that σ is honest:
  the sim knows the true delay, so report **coverage, meaning the true error falls within 2σ in ≥95% of
  samples**, plus the share of on-screen time with σ ≤ 8 s and ≤ 3 s. Then GAME-04 numbers with the
  honest σ. No cap tricks.
- **Also:** grade **both** candidate variants every run (`crunch_now=wait` and `rewind`) until Nick
  decides, with the wait variant as the default candidate (rule #1). Then GAME-05/06 continue.
- **North Star (candidate, dev):** big live NFL 53 → **≥60**, NHL 45 → **≥50**, NBA ≥ 77.5 (wait) ;
  mid_play 0; σ coverage ≥95%. Dev + locked in the PR.
- **Files:** `redzone/decider/sync.py`, `redzone/lab/syncsim.py`, `redzone/noggin/profile.py`,
  `scripts/northstar.py` (variant column only), tests.

## ③ Performance + controls: press_to_tv ≤ 3 s in the drill
- **Looking for:** the drill's 8.75 s comes down to **p95 ≤ 3 s** by having the hidden stick already
  tuned to the pressed game, using pre-tune-on-open (on in the candidate, not live) plus idea #17 (the
  lookahead target as a high-class standby option, from #100). Presses for a game that isn't pre-tuned
  are reported separately (honest split: pre-tuned vs cold).
- **North Star:** `press_to_tv_p95` (sim) ≤ 3 s; `flip_playing_3s` holds; tick p99 and RSS unchanged.
- **Files:** standby planner, `redzone/control.py`, profile flag, drill, tests.

## ④ App + integration hardening (only if reopened at 8 AM)
Rebase #88/#91 (UI conflict), prep the v3 locked switch for Fri Nov 6, coverage 32 → ≥45.

## ⑤ Features (only if reopened)
Decision-record export for the Teacher (backlog item 2). One small PR.

## Integrator
Merge ② before ③ (③ reads the lookahead target). The review grades σ coverage and press_to_tv per the
"looking for" above, not only the deltas.
