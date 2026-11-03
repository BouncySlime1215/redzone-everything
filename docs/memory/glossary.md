# Glossary

| Word | Meaning |
|---|---|
| Dead ball / dead time | No live play: commercial, timeout, free throws, review, injury. The only time we cut. |
| Mid-play cut / entry | Leaving or landing on a game while a play is live. Must be 0. |
| Missed on entry | A play that started before our switch landed and we never saw. |
| Dead air | Commercials or idle screens on the TV. Measured as `dead_air_pct`. |
| Crunch time | Late and close. Comes first in every decision. |
| Your team | WSH (Commanders, Wizards, Capitals, Nationals) and UNC. 100% gates. |
| Big play | Scoring play or a play the brain rates high. "Seen" = live or in a catch-up reel. |
| Reel / catch-up | 5+ highlight clips in 30–60 s, played only in dead time. |
| Trip budget | How long a visit to another game may last before we must be back. |
| Decision core | `redzone/decider/core.py`: the canonical types and the one place that decides. |
| WorldState | One snapshot of every game, stick and the screen per tick (`redzone/contracts/`). |
| Stick | A Fire TV stick driven over ADB. Two of them, on HDMI1 and HDMI2. |
| SwitchPath | One way of changing what the TV shows (HOME+CEC, TV input, Multiview). |
| Sync / delay | How far each channel's stream runs behind the score feed. Goal < 2 s error. |
| FastCast | ESPN's push score feed; watch-only check in #33. |
| Brain | (1) MOMENT/BREAK models that score games; (2) the cloud auto-fix loop (docs/auto-fix.md). Say which. |
| REFLEX | The local distilled student model (Qwen3-1.7B, ≤ 2 GB). |
| Teacher / student | Big model on Kaggle labels moments; the small one learns from it. |
| Watchdog | `agent/watchdog.py`, launchd `com.redzone.agent`: rules + optional local model, fixed actions. |
| Governor | `redzone/governor.py`: memory/CPU/disk modes (normal, eco, critical). |
| Locked set | ≥ 25% of nights never used for training or tuning; scored once per release. |
| Fresh nights | Nights after the training cutoff, reported separately each round. |
| eval-data-v1 | The pinned scorecard dataset (release asset). |
| Integration train | Merging PRs one at a time into `integration/next`, with scorecards. |
| Flag | A `flags.*` switch; a part that misses its gate ships with its flag OFF. |
| Director | Nick's Mac-side Claude session that steers the project and updates docs/handoff.md. |
| Coordinator | The claude.ai project chat Claude that runs the threads. |
