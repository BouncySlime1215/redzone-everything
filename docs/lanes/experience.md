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
| `GET /api/v2/watch` | Watch value strip and sheet, plus the EXP-035 "why we're here" tag (only when the game on screen is at or over config `phone.why_tag_min`, 70) | `Scored.watch_value`, `watch_why`, `factor`, `score`, `crunch_state`, `stakes_line` (#172; `None` = no stakes, then a "rough" line from `Scored.stakes`) |
| `GET /api/v2/slate` | Tonight ahead: crunch windows, clashes, WV per live game | board games, `HardRules`, `Scored.watch_value`, `watch_why`, `watch_rising` (hidden unless `scoring.watch_value`) |
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
