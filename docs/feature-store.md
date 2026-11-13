# Feature store (F-06)

`redzone/featurestore.py` is the one place model features are built from a feed prefix. The stage 4/5/6 notebooks
and the app's stage 6 arm both call it, so training rows and serving rows come from the same code.

## Use

```python
from redzone import featurestore as fs
P = fs.fs_parse("nba", summary)                               # once per new summary
X = fs.fs_rows("value_v3", "nba", P, times, facts=facts, lag=15.0, dtype=np.float32)
rec.update(fs.fs_registry_fields("value_v3"))                 # registry record gets "feature_set"
RD = fs.FS_STAGE4_COMPAT                                      # notebooks: replaces the stage4_reader_v3 module
```

Notebook swap (stage5_policy.py, stage6_rerank.py): wherever `RD` is the loaded `stage4_reader_v3` module and only
`RD.parse`, `RD.game_features`, `RD.FEATURES` or `RD.features_at` are used, set `RD = featurestore.FS_STAGE4_COMPAT`
from the pinned repo. Model loading and `RD.predict` stay in the reader file (the store builds features only).

## Causal contract

A feature at time `t` reads only rows stamped at or before `t - lag`, and grader facts stamped at or before it.
Features from a cut feed (`fence.cut_feed`) equal features from the stored full game at the same moment.
One quirk kept for parity with the old builders: a moment before the game's first row reads the first row.

## Feature sets and versions

The registry tag is `fs_tag(name)` = `name@version/fs-1/<sha12 of the column list>`. Change the version whenever a
column's meaning or arithmetic changes; a changed column list changes the hash on its own.

| set | version | tag (today) | leagues | columns |
|---|---|---|---|---|
| `value_v3` | value-v3.0 | `value_v3@value-v3.0/fs-1/d622ceaf9f69` | nba, cbb | 62, below |
| `state4` | state4-v1 | `state4@state4-v1/fs-1/7f577586d7e2` | nba, cbb | lev_now, since_stop, last_kind, since_to |

`value_v3` columns (origin: the notebook reader stage4_reader v3, sha256 `1120430239ef...`, ported line for line):

| group | columns |
|---|---|
| game state | league_cbb, post, period, clock_s, left_s, ot, margin, absmargin, total |
| time since | wall_elapsed, since_row, since_action, since_stop, since_to (capped at 9999 s); last_kind (0 action, 1 whistle, 2 review, 3 timeout, 4 period end, 5 halftime, 6 game end) |
| pace | rows_60w, rows_300w (rows in the last 60/300 wall seconds), gsec_60w, gsec_300w (game seconds in them) |
| scoring windows (last 2 / 5 game minutes) | lead_pts_2, trail_pts_2, lead_pts_5, trail_pts_5, tot_pts_2, tot_pts_5, d_absmargin_2, d_absmargin_5, run_pts, run_by_trailer, lead_changes_5, ties_5, lead_changes |
| play counts (last 2 / 5 game minutes) | poss_2, poss_5, shots_5, made3_5, dunk_5, tov_5, stocks_5, foul_5, ft_5 |
| fouls and timeouts | tf_lead, tf_trail (team fouls this period), to_lead, to_trail (team timeouts so far), to_period |
| pregame line | spread_abs, over_under (-999 when the summary has none) |
| win probability | wp_home, wp_close, wp_vol_2, wp_vol_5, wp_d60w, wp_pre (-999 when absent) |
| grader facts | lev_now, pv60, pv300, pv900, pv_rate, since_big, n_big, since_hl (-999 without facts) |

## Proof

- `python scripts/feature_parity.py --reader <stage4_reader_v3.py>`: 250 states from real dev nights (fixed seed);
  store vs notebook reader (value_v3, state4), store vs the arm's pre-F-06 `Stage6Rows` (read from git), cut feed vs
  full game; a 30 s shifted control must differ.
- `tests/test_featurestore.py` (CI): the same checks on fixture games and tiny built games, against a golden file the
  reader wrote (`scripts/feature_parity.py golden`).
