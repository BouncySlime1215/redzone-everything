# Cycle 04 plan (Start Planner), Wed Nov 4, ~15:30 to 19:00 UTC (before tonight's live test)

**From:** [cycle 03 review](../03/review.md) (integration/next da1744c) and Nick's three orders today:
**possession-start crunch entry** (10:45 AM), the **self-taught rule** (10:55 AM, merged in #111), and the
**scale order** (11:05 AM). Lean doc. Lanes ④-⑤ get work only if Nick has reopened them.

**Where we are (candidate):** mid_play 0 PASS on dev and locked. % of oracle 64.8 dev / 59.1 locked
(live 49.6 / 43.3). Sync σ is honest (98-100% coverage) but ~3x too wide; NoiseCal NFL big live 57.6 / 60.4.
press_to_tv 8.75 s FAIL (pre-tuned 3.0-3.7 s, cold 8.75 s). Pick with its flag on ties NBA big live and
loses locked crunch, so it stays off.

**The one idea:** **real-world truth is the bottleneck** (Nick). Tonight is the first live night with
the probe, so everything that turns tonight into data must be in place by evening. In parallel, the
data factory scales to billions of examples on Kaggle. **No signal from Nick in any of it.**

## ① Noggin training: the crunch policy, and the factory at scale
1. **Crunch possession policy (top priority):** per possession, jump / stay / replay-later, trained by
   imitating the hindsight oracle on NBA nights (#89/#104 labels), with crunch-ahead and Mind-stakes
   features. Forward CV by whole nights, full set, locked untouched. **Bar:** mid_play 0; NBA big live
   and crunch live better than the rule-based possession default (lane ②) on dev and locked. Report
   % of oracle. Flag off and shadow first.
2. **Factory at billions (scale order):** a sharded, resumable Kaggle CPU pipeline (parquet shards
   published as Kaggle datasets; not GH Actions) covering all 6 leagues, 2002+ wherever wall clock
   exists, every possession and stoppage as a labeled moment, plus oracle/self-play synthetic nights.
   Lane ① writes the notebook and shard layout, and **Nick launches it** (he owns Kaggle). Every PR reports
   rows per league. The Reflex stays < 5 ms; the scale lives in the data.
3. Mind V7 (Kaggle, Nick): support only, no Nick-tap data anywhere in its training set.

## ② Noggin logic/sync: possession-start entry, faster feeds
1. **Possession-start entry** (in flight) as the candidate default, replacing wait and rewind. The hidden
   stick cues to the target's last dead ball before the flip; collisions put one game live and queue
   the other. It's a rule-based default with a hook for ①'s policy. New rows `possession_start_entry_pct`
   (≥95%) and NBA big live / crunch live with the policy, on locked too. Target: mid_play 0, NBA big
   live ≥81.7 dev.
2. **FastCast has never logged a row on the Mac:** find out why and fix it (it may need the Mac folder or a
   Remote Control session; ask the coordinator for the access card rather than waiting).
3. **Fastest free, legal NFL/CFB source:** one focused research pass, measured the way #58 measured NHL
   (p50 lag vs ESPN), shadow only. ToS-clean sources only.
4. After Thursday's probe: refit the anchor prior, NoiseCal into the candidate, GAME-04 with the honest σ.

## ③ Performance + controls: tonight becomes data
1. **Live nights auto-log (scale order (c), urgent for tonight):** every live night writes real landings
   (offset vs the dead ball), per-stick sync error at each anchor, switch latency and press-to-TV, with
   no Nick input, into the store that `collect_live_test.py` (#80) uploads. `northstar.py` grows a
   **"Live nights"** section (real numbers, PENDING until a night exists). This is the evaluation and
   training truth for ① and ②.
2. The drill's candidate column runs with the candidate profile (the review's harness gap).
3. Model the TV local-key path in the night lab, with timing taken from the Wed probe step, so press_to_tv
   can reach ≤3 s honestly once the probe confirms it.
- **Bar:** tonight's run produces non-empty live rows; tick p99 and RSS unchanged.

### Addendum: first real-hardware findings (live lab, Nick, 10:45 AM; docs/livelab-2026-11-04.md on the Mac)
- **② (raises priority):** (a) the real SmartThings flip distribution (64/64 confirmed, p50 1.89 s, p95 2.18 s,
  max 2.58 s, ack 0.31 s) replaces the 10-sample one in the sims and the planner's trip cost. (b) **YTTV reports a
  real playback position (ms) on the 4K Plus while playing**: use it as a direct delay measurement in the
  per-stick filter. It's the most likely way to the small σ that GAME-04 needs, so it comes right after the NBA
  data-gap fix. YTTV (Cobalt) has no UI text nodes, so its anchors come from logcat/position, not the UI tree.
- **③:** (a) **STK-05 paused hidden stick:** with HDMI-CEC on, every flip paused the stick we left, and it
  drifted silently behind live. CEC is now off on both sticks, but the guard detects a PAUSED hidden stick
  (media_session state + speed) and resumes or re-syncs it before any flip, with a marked night-lab test.
  This comes right after the live-night logging. (b) A playing stick requires `speed > 0` **and** `active`
  (a stale state=3 with active=false lingers after backgrounding).

## ④ App + integration hardening (only if reopened)
Switch locked to v3 on Fri Nov 6; rebase #88/#91.

## ⑤ Features (only if reopened)
Decision-record export for the Teacher (objective outcomes only, no taps).

## Integrator
③ item 1 merges first: it has to be live on the Mac before tonight's test (it ships with the next
deploy; Nick cuts releases). Then ②, then ①. The review checks the self-taught rule on every PR.
