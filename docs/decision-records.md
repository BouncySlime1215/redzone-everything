# Decision records (Teacher nightly review)

One JSONL file per night (6 AM to 6 AM ET), written by
`python scripts/export_decision_records.py --night YYYY-MM-DD` from the Mac's `data/redzone.db` (opened
read-only) and the supervisor's `data/supervisor_cuts.jsonl` and `data/supervisor_moments.jsonl`. The code
and the full schema are in [redzone/lab/decision_records.py](../redzone/lab/decision_records.py).

**Self-taught rule:** the export never reads the `feedback` table. Nick's 👍/👎, "I missed that" and
overrides don't appear in any record (decisions.md, Nov 4 10:55 AM). The header lists them under `never`,
and `tests/test_decision_records.py` fails if one leaks in.

| line `type` | what it holds | source |
|---|---|---|
| `night` (first line) | schema version, window, counts (decisions, switches, big plays, big seen, landings graded, mid-play entries/leaves), sources | all |
| `decision` | action, game, kind, state, gate, reason, `playbooks` (`playbook=<id>` in the reason), game on screen, `candidates` (last per-game priority snapshot ≤ 3 min old, best first), `outcome` (big plays in the next 5 min: total, on the target, seen, missed + which) | `decisions`, `switches`, `snapshots`, moments |
| `switch` | from/to, kind, reason, playbooks, ok, dry_run, `timing` (decide→picture, dead air, path), `landing` (leave/enter: `play` or `dead` + why, judged from the feed) | `switches` + its trace, cut grades |
| `moment` | one big play: game, league, kind, `seen` live, game on screen | moments |

The Teacher joins records to archived pbp by `(game, ts)` and grades them against the hindsight oracle
([redzone/lab/oracle.py](../redzone/lab/oracle.py)). The files hold game ids and reason strings only, but
they're night logs: keep them out of the public repo, as with the rest of `data/`. There's no upload
code: moving a night to Kaggle is the overseer's step.

## Oracle overlay in the replay viewer

The phone's "Whole night" sheet (flag `phone.v2`) draws a second ribbon below what was on, labelled "Best
possible (hindsight)", when `data/oracle/<night>.json` exists:

```json
{"objective": "combined", "switches": [{"t": 1730761200.0, "to": "<game id>"}, ...]}
```

`t` is unix seconds and the switches are in time order. It's written offline from the archived night by
`redzone/lab/oracle.solve`. "We matched N%" is the share of our on-screen time that showed the oracle's game.
Every big play from `data/supervisor_moments.jsonl` gets a tick on our ribbon, and the ones not seen live
are drawn in red. A missing or bad file just hides the oracle ribbon.

## "I missed that" reel requests (record only)

With phone v2 on, "I missed that" also adds a line to `data/reel_requests.jsonl`:
`{ts, game_id, matchup, play_id, kind, text, play_ago_s, due_by, state}`. `due_by` is `ts` + 180 s (the
3-minute rule). `state` is `recorded_reels_off` while `reel.play` is false (the default) and `queued` once
reels can play. It's a control request, like a button press: the decision-record export, the Teacher and
every training or grading path ignore it (self-taught rule). Nothing plays it yet.

## Crunch timeline in the replay viewer (learned vs hindsight)

The "Whole night" sheet draws one pair of tracks per game: the learned crunch state (#166) on top, the
hindsight oracle's crunch below, with each game's entry and exit delta. Misses come first, and the header
says how many exits were right. It reads `data/crunch/<night>.json`, written offline by lane 1's grader
with the same rule as `scripts/train_watch_value.crunch_card` (first-on / last-on within `tol_s`):

```json
{"tol_s": 60, "games": {"<game id>": {"learned": [[t0, t1], ...], "oracle": [[t0, t1], ...]}}}
```

When there's no file, the learned side is sampled from each switch's `trace.provenance.crunch_state`
(#173, `{"to": state, "from": state}` or a bare state) and the hindsight side shows as pending.

## Live night: the morning gold-data check

`GET /api/v2/capture?day=` (the "Live night" sheet) answers one question: did last night become gold data?
- **Auto-log:** `data/live_nights/<night>/summary.json` from the supervisor's `collect_live_night`.
- **Provenance:** how many switches carry `trace.provenance`, and which modules spoke.
- **Held buffer:** `control.held` right now (tonight only), and `data/buffer.jsonl` kinds for the night.
- **Night report:** `data/reports/<night>.md` or `docs/nights/<night>.md`, readable in the sheet through
  `/api/v2/capture/report`.

It's read only and makes no writes.

## EXP-035: "why we're here" alerts, graded

The phone labels the game on screen "Why we're here: <why>" only when its value is at or over a fixed
threshold (config `phone.why_tag_min`, default 70, on the 0-100 scale: the learned watch value when there is one,
otherwise today's score). Each switch record carries `alert: {value, threshold, fired, nothing_happened}`.
The value is the one the decision saw, from `trace.provenance` (`watch_value.to`, otherwise `importance.to`).
A fired alert "did nothing" when its game had no big play in the outcome window (`OUTCOME_WINDOW_S`).
Dry runs never alert. The night header counts `alerts`, `alerts_nothing_happened` and `alerts_nothing_pct`.
That last number is the honesty check: a label that often promises more than happens should get stricter.
