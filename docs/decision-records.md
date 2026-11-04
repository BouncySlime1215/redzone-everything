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
