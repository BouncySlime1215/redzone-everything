# Idea bank (Nick, Nov 4 3:35 AM): "100x better"

Owned by the Start Planner. Each cycle's plan pulls the next items from here into lanes ①-④, highest
leverage first, and marks them `planned (cycle NN)` → `done (#PR)` or `dropped (reason)`.

**Hard constraint:** one 8 GB M2 Mac runs everything. Heavy work (hindsight optimization, simulation,
search, training) runs offline on Kaggle (Nick launches it) or GitHub Actions / the cloud container.
The Mac only runs lookups and small models: **~2.5 GB RSS total for AI**, background priority, stays
cool. Every item ships flag OFF, is graded in the candidate column on full dev + locked nights, and
its PR reports a RAM/CPU line.

Lanes: ① Noggin training, ② Noggin logic/sync, ③ performance + controls, ④ app + integration hardening.

## Top 3

| # | idea | lane | North Star metric (target) | Mac budget | offline work | slot |
|---|---|---|---|---|---|---|
| 1 | **Clairvoyance:** with the 60-90 s buffer the feed already shows the next 30-60 s before it airs, so pre-position on the game whose big play is about to air ("never miss a TD") | ① model (Predict reading the buffered-ahead feed), ② decider pre-position in the candidate | `big_live_{nfl,nba,nhl}` ≥95% (cycle 02 goal: NFL candidate +20 pts dev), `your_team_big` 100%, `mid_play` unchanged | lookup over the feed already in memory, <1 ms/tick, +0 MB model | none beyond Predict v1 | cycle 02 |
| 2 | **Hindsight-perfect nights:** DP over all games per archived night with full hindsight gives the optimal switch schedule; train Reflex PICK to imitate it across thousands of nights | ① | new line `oracle_gap` (the candidate's score as a % of the oracle on the same nights, target ≥90%); then `big_live_*`, `crunch_*` | PICK model ≤50 MB, <5 ms/tick | DP + imitation training on GH Actions / cloud | cycle 02 (oracle + gap line), 03 (PICK) |
| 3 | **Lookahead planning:** plan the next ~5 min across all games (MPC over a precomputed value table), not one switch at a time | ② | `big_live_*`, `dead_air`, `oracle_gap`; `mid_play` 0 hard | value table ≤100 MB mmap, plan ≤20 ms every 5 s | value table built offline from #2's oracle | cycle 03 |

## Next

| # | idea | lane | metric (target) | Mac budget | offline work | slot |
|---|---|---|---|---|---|---|
| 4 | Millions of simulated nights from 20 yrs of pbp for offline self-improvement / RL | ① | `oracle_gap`, every candidate line on locked | 0 (offline only; the result ships as a #2 PICK update) | GH Actions / Kaggle | cycle 04+ |
| 5 | Sync truth from the sticks (in progress: sync stack, break anchors) | ② | `delay_err_p95` ≤2 s, `flip_playing_3s` ≥99% | ≤20 MB, <1 ms/tick | none | in flight |
| 6 | Live-odds swings as a crowd "big moment coming" signal (free sources only, no paid feeds) | ① feature, ④ source | `big_live_*` (+ in candidate), Predict PR-AUC | one poll/30 s, ≤5 MB | backtest on archived odds if a free archive exists | cycle 03 research, 04 build |
| 7 | Learn Nick's taste from overrides, lock-ins and 👍/👎 | ① (needs lane ⑤ records + lane ③ commands) | `right_game` ≥95% | ≤5 MB table | nightly cloud fit | cycle 04+ (after records exist) |
| 8 | Model ensemble + abstain: disagreement means hold | ① | `mid_play` 0, `landings_dead` ≥95% | ≤3 small models, <5 ms total | train offline | cycle 03 |

## Mac efficiency

| # | idea | lane | metric (target) | Mac budget | slot |
|---|---|---|---|---|---|
| 9 | Pre-game night plan computed in the cloud and pulled to the Mac | ④ (with ②) | `big_live_*`, startup time | download ≤10 MB, 0 CPU at runtime | cycle 03 |
| 10 | History lookup tables instead of live inference where they're as good | ③ | tick p99, RSS (and no candidate line worse) | tables mmap ≤200 MB | cycle 03 |
| 11 | One batched pass over all games per tick | ③ | tick p99 (target ≤20 ms for 8 games) | CPU down | cycle 03 |
| 12 | Attention budget: idle on blowouts, focus on close/late games | ③ | CPU % per tick, `crunch_*` unchanged | CPU down | cycle 04 |
| 13 | Pre-flip check that the hidden stick is playing smoothly, else skip the switch | ③ | `flip_playing_3s` ≥99%, `dead_air` | <1 ms | cycle 02 |
| 14 | Fast nightly warm-up of each stream's lag | ② | `delay_err_p95` ≤2 s in the first 10 min | ≤1 min of probe at startup | cycle 03 |
| 15 | The Mind (3-4B) pauses when the Mac is hot, busy or on battery | ③ | tick p99 under load, thermal state, no stalls | Mind 0 MB while paused | cycle 03 |
| 16 | Nightly cloud review pushes a small update; never training on the Mac | ④ | every candidate line on fresh nights | download ≤50 MB | cycle 04 |
| 17 | Keep the most-likely-next game pre-tuned on the hidden stick | ③ (uses ① Predict) | `press_to_tv_p95` ≤3 s, `switch_p95`, `big_live_*` | <1 ms decision | cycle 02 |
| 18 | Safe mode on bad feeds or Wi-Fi: never cut mid-play | ② (rule), ④ (feed health) | `mid_play` 0 under the robustness slices (feed 10 s stale, sync ±3 s) | <1 ms | cycle 02 |

## Nick's last orders (3:45 AM)

| # | idea | lane | metric (target) | Mac budget | offline work | slot |
|---|---|---|---|---|---|---|
| 19 | **Chess planning with scenarios** (extends #3): the lookahead keeps a TREE of contingency branches ("if A breaks while B is in the red zone → B now, back to A at the restart"; "TD in A while C has a PP → C next"; "your-team crunch → lock in"). The cloud precomputes the scenario policy as compact tables keyed by a discretized slate state; the Mac looks up the branch that matches the live state (instant) and re-plans when a branch is invalidated | ② (Mac lookup + invalidation), ① (cloud solver over the #2 oracle) | `oracle_gap` ≥90%, `big_live_*`, `crunch_*`; `mid_play` 0 hard | table ≤100 MB mmap, lookup <1 ms, re-plan ≤20 ms | build tables on GH Actions / Kaggle from archived nights | cycle 02 (hand-written GAME-* branches + table format), 03 (cloud-built tree) |
| 20 | **Playbooks everywhere:** hundreds of named trigger → action → fallback entries across game situations, feeds, Wi-Fi, sticks, SmartThings, Mac sleep/heat and button presses, each with a night-lab test. Ordered list: [docs/playbooks.md](../playbooks.md) | all lanes (owner per entry) | per entry; overall `recovery` ≥95%, `mid_play` 0 | each check <1 ms, pure lookup | night-lab seeds in CI / GH Actions | every cycle, P0 → P1 → P2 |

Per-lane ranked backlogs: [backlog.md](backlog.md).

## New North Star lines these need
- `oracle_gap` (#2): the candidate's big-plays-seen + crunch score as a % of the hindsight oracle's on
  the same nights. Built offline, graded on dev and locked.
- `press_to_tv_p95` (lane ③, cycle 01) and `flip_playing_3s` (exists, PENDING) cover #13 and #17.
- A RAM/CPU line per PR: peak RSS of the AI side (≤2.5 GB) and tick p99.
