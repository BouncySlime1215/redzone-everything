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
