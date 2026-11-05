# EXPERIENCE (lane 5): phone v2, replay viewer, slate, multiview

This lane owns read-only, display-only views. Everything sits behind the `phone.v2` flag (default off), and nothing here
writes to the store, touches the decider, or touches lane 3's command path (`/api/action`, `engine.action`,
`rz-api sendTap`). Nick's taps are never read for training or grading (the self-taught rule). Code lives in
`redzone/web/phone_v2.py` (the `/api/v2` router), `redzone/web/ledger.py` and `redzone/web/static/rz-v2.js`.
There's a mock preview at `/static/index.html?mock&v2` (data in `rz-mock.js`). Owners and serves tags are in the
[city map](../citymap.md#experience).

| endpoint | view | reads (contract fields and files) |
|---|---|---|
| `GET /api/v2/switches`, `POST /vote`, `POST /missed`, `GET/POST /priority` | reason chips, 👍/👎 tied to one switch, "I missed that", L2 sliders | `switches` rows, `feedback` (controls only, never training), `Preferences` |
| `GET /api/v2/night?day=` | Whole night: what was on, hindsight ribbon, planner lane, crunch timeline | `switches`, `decisions`, `data/oracle/<night>.json`, `data/supervisor_moments.jsonl`, `data/crunch/<night>.json` (learned vs oracle spans), else `trace.provenance.crunch_state` |
| `GET /api/v2/provenance?key=` | Switch trace: who contributed what | `switches.detail.trace` events + `trace.provenance` {decider, watch_value, crunch_state, importance, stakes, floors, planner, playbook, guard, control} |
| `GET /api/v2/held` | the "Held + coming back" strip | `control.hidden_target/hidden_ready/held`, `decider.home_break_visit/home_return_at/queue` |
| `GET /api/v2/catchup` | Catch-up queue + multiview stub (live tile, held tile) | `control.held`, `data/buffer.jsonl` (`left_held` then `left_resumed`, `left_expired` or `left_lost`, STK-07), `decider.queue` (`Missed`: `must`, `mine`, `due_s`), `Scored.is_break`, `decider.current` |
| `GET /api/v2/watch` | Watch value strip and sheet, plus the EXP-035 "why we're here" tag (only when the game on screen is at or over config `phone.why_tag_min`, 70) | `Scored.watch_value`, `watch_why`, `factor`, `score`, `crunch_state`, `stakes_line` (#172; `None` = no stakes, then a "rough" line from `Scored.stakes`); EXP-037 `context` per row ("BUF down 3, 1:12 left, 1 timeout"), only late and within one score, from `GameState` clock, score and situation; EXP-039 `also_close` (flag `phone.also_close`): up to 2 other late, close games as text |
| `GET /api/v2/slate` | Tonight ahead: crunch windows, clashes, WV per live game | board games, `HardRules`, `Scored.watch_value`, `watch_why`, `watch_rising` (hidden unless `scoring.watch_value`); `complete` = no games left and at least one finished, which shows the EXP-036 "Slate complete" card with nothing after it |
| `GET /api/v2/status` | `enabled` (`phone.v2`) and `commit` (`phone.commit`, EXP-036) | config only. The commit sheet sends `act("commit", {mode: my_teams/stop_after/end_at, game_id, at})`; lanes 2/3 own the action and apply it only at a dead ball. Controls only, never a training signal |
| `GET/POST /api/v2/worth` | EXP-040 "Was tonight worth it?" (1-5) on the Slate complete card, plus the weekly agency/regret probes. Own flag `phone.worth`; works with `phone.v2` off | `data/worth.jsonl` (evaluation only, graded by `redzone/lab/worth.py`) |
| `GET /api/v2/orient?game=` | EXP-038 orientation beat after a switch: `score`, `clock`, one `fact`, the late/close `context`. Flag `phone.orient` (off) on top of `phone.v2` | `GameState` score, clock, situation |
| `GET /api/v2/trend?split=` | How close to perfect | `docs/northstar-history.jsonl` |
| `GET /api/v2/scorecard?n=` | Night scorecard: release-gate lines per night | `data/reports/<night>.json` or `docs/nights/<night>.json` (`mid_play_entries`/`landed_mid_play`, `high_leverage_leaves`, `plays_missed_entry`, `dead_air_pct`), `data/live_nights/<night>/summary.json`. Missing keys show as pending |
| `GET /api/v2/capture?day=`, `/capture/report` | Live night: did it become gold data | `data/live_nights/<night>/summary.json`, `trace.provenance` coverage, `control.held`, `data/buffer.jsonl`, the night report `.md` |
| `GET /api/v2/ledger` | Data + NFM | `docs/data-ledger.json` (lane 1 writes it; `scripts/data_ledger.py --manifest` folds the factory manifest) |

Rules for this lane:
- Bump the `?v=` cache keys in every PR.
- No getattr on contract types (typed `Scored` fields only).
- No timing literals: a tolerance comes from its producer's file.
- Core never imports lab: the ledger reader lives in `redzone/web`.
- `contract_check.py` must show 0 new violations.

## Pre-registered bars

### EXP-040: end-of-night "worth it?" (evaluation only)

Set on Nov 5, 2026, before any rating exists. It is graded by `python scripts/worth_line.py --live <nights> --candidate <nights>`.

- **What is recorded:** one row per night in `data/worth.jsonl` (`{night, ts, rating 1-5, agency? 1-5, regret? yes/no}`, last tap wins). Nick's answer grades a release. It is never a training label, reward or feature. `tests/test_worth.py` fails if noggin, decider or scoring code reads it.
- **Instrument works** (ship gate for `phone.worth`): the card appears only once the slate is complete. It takes one tap and has no counters, streaks or follow-ups (playbook §5). The probes come at most once per `phone.worth_probe_days` (7) nights and only after the rating. On nights with a completed slate while the flag is on, ≥ 90% of nights get a rating (checked after the first 7 such nights).
- **The line "worth it ≥ live":** each rated night is tagged with the release that drove the TV that night. The night is the unit of evidence.
  - **PASS:** at least 4 rated nights on each side, and the 95% bootstrap lower bound of mean(candidate) − mean(live) is ≥ −0.25 (non-inferior by at most a quarter point on 1-5).
  - **FAIL:** the upper bound is below −0.25.
  - **PENDING:** anything else, including too few nights. PENDING never counts as passing.
- **Warning rule** (§6): watch time up while the rating is down blocks the change (`worth.warning`).
- **Honest limit:** a shadow candidate never drives the TV, so its nights can't be rated. Until a candidate drives real nights, the line stays PENDING, and the live nights build the baseline.

### EXP-038: orientation beat (phone)

Set on Nov 5, 2026, before any beat night. It is graded by `python scripts/orient_line.py --db <db> --on <nights> --off <nights>`.

- **What it is:** for `phone.orient_beat_s` (3 s) after each switch, a corner card on the phone shows the score, the clock, one situation fact and the late/close context line. It's plain facts with no button, no sound and no hype. **Honest limit:** this rig can't draw on the TV (the sticks only switch HDMI inputs), so the beat is phone-only. A TV corner overlay would need a new display path, which is a hardware decision for Nick.
- **Measure:** an override is a user switch to another game within `phone.orient_override_s` (30 s) of an automatic switch. The rate is overrides / automatic switches per night. Whole nights alternate on/off and are never split within a night. The grader reads only the switches table.
- **PASS:** at least 6 nights per side, and the 95% bootstrap upper bound of rate(on) − rate(off) is < 0.
- **FAIL:** the lower bound is > 0.
- **PENDING:** anything else (never counts as passing).
- **Guardrail:** Right game agreement must not drop on beat nights.

### EXP-039: "Also close" line (phone)

Set on Nov 5, 2026, before any line night. It is graded by `python scripts/also_close_line.py --db <db> --on <nights> --off <nights>`.

- **What it is:** under the watch strip, up to 2 other live games that are late and within one score, shown as text ("UNC @ DUKE UNC down 2, 0:41 left, 1 timeout") in league order. It has no Watch button and no tap target. It answers "is anything else close?" without a switch.
- **Measure:** the manual-switch share per night (user switches / all switches that went through). Whole nights alternate on/off.
- **PASS:** at least 6 nights per side, and the 95% bootstrap upper bound of share(on) − share(off) is < 0.
- **FAIL:** the lower bound is > 0. That would mean the line pulls switches instead of informing.
- **PENDING:** anything else.
- **Guardrail:** "Big plays live or replayed within 3 min" must not drop on line nights.
