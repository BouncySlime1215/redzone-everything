# First-pass reads on eval-data-v3 new NBA fit nights (Nov 6 2026, graded once)
Nights: v3 fit nights not in v1. That is 2025-10-21..2026-01-31 plus 2026-10-29..31: 104 nights, 102 with 2+ games. The locked block (>= 20261101) was never read. NFL: v3 adds no new unlocked nights.

| EXP | result | verdict |
|---|---|---|
| 1722 NBA big_v2 | zero-value games 59.2 -> 1.2%; recall 52.0 -> 58.8 (+6.8, CI 3.5..9.8); your-team crunch 506/506 both; mid-play 0 both | PASS (all 3) |
| 1723 beyond value, p90 lag | B1-B0 +0.6 (CI -3.0..4.6); B1-app -9.6 | FAIL |
| 1724 beyond value, 11.5 s | B0 84.9 / B1 83.5 / B2 82.6 / app 83.0 % of full | FAIL |
| 1725 reachable crunch (#362) | big live +0.0 (CI -0.5..0.5), crunch +0.07; your-team crunch 462/506 in both arms | FAIL (criterion 2; no effect) |
| ledger | app 89.8% of full hindsight (CI 81.2..96.8); value lost: your-team crunch 67%, crunch plays 14%, dead air 13%, big 7% | info |

Your-team crunch misses (hard rule, both arms): see your_team_crunch_misses.md.
1723/1724 summaries say "evidence: dev history only"; that is stale script text. These runs are the counting read on v3 new nights.

## Frozen-file candidate (EXP-1722)
`redzone/lab/oracle_v2.py` holds NBA big_v2, `OBJECTIVE_V2` and `tables(n, steps, wp, lag)`. It reads oracle.py and never changes it; `register()` adds "v2" at runtime.
On 3 read nights (20251110, 20251214, 20260115) its big table matches the read harness (`exp1722_read.py`) element for element.
Tests: `tests/test_oracle_v2.py`. It reports beside v1 as information only. v1 stays the gate until Nick decides.
