# Your-team crunch misses on eval-data-v3 new NBA nights (WV loop, Nov 6). personal-use, not for commercial release.
App as shipped (integration/next 1cd94c1, eval_config pinned, 90 s buffer, feed lag 70 s), 102 new fit nights (2025-10-21..2026-01-31, 2026-10-29..31), locked never read.
your_team_crunch_live = 462/506 (91.3%); on v1 nights it was 31/31. Three nights:
- 20251110 WSH @ DET (401810054): 82/105. About 14 min into WSH's Q4, the app switched away on "moment's over, back to: LAL up 12 · 5:54 left in Q4" (the catch-up return target was not your team's game). It then went to an ORL crunch (0:22) and returned to WSH only at "WSH up 3 · 0:01 left in Q4" (OT game).
- 20251212 CLE @ WSH (401836753): 12/31. The app left WSH for "CHI up 5 · 0:39 left" crunch. WSH came back from 11 down into its own crunch while the app was elsewhere. It returned only at "CLE up 2 · 0:24 left".
- 20251231: 28/30.
Ledger value (full oracle, combined objective): your-team crunch = 4200 of 6300 value lost (67%) on these nights. Big plays 141, crunch 883, dead air 13%.
Repro: scripts/eval_sports.py nba --split fit --behind-live 90 --feed-lag 70 on the v3 archive (lock dev_before 20261001).
