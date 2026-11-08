# Review brief, 2026-11-08 (shared by all reviewers)

Goal of the product (North Star, CLAUDE.md + docs/context/claude-memory/redzone-north-star.md):
RedZone for every sport on Nick's Samsung TV via two Fire TV sticks, switching only at dead balls.
League order NFL > CFB > NBA > NCAAB > NHL > MLB; his teams (WSH, UNC) first; crunch time first.
Rule #1: never JOIN mid-play (0 mid-play entries, hard). Leave mid-play only at low leverage; never leave
a high-leverage moment (close and late, or overtime). Replays only on the hidden stick. No commercials.
Real autopilot (no manual forces). $0 budget, never Peacock, Mac is 8 GB, release gate 95% on locked nights.

Method (Agentless-style, docs/research/agent-coordination-and-rationale.md):
1. Localize: `python scripts/citymap.py --focus <file>` and `--impact <file>` to see how your area connects.
2. Read the code. 3. Verify each finding against the code (quote the lines) before reporting.

Report only real, verified problems:
- ERROR: a bug (wrong behavior, crash, race, lost state, wrong unit, dead code path that should run).
- CONFLICT: two rules/gates/configs that disagree with each other or with the North Star
  (e.g. a gate that allows a mid-play join, a priority that ignores league order, a default that contradicts docs).
- GAP: a North Star requirement nothing implements or nothing tests.
Each finding: id, type, severity (S1 breaks Rule #1 or freezes the TV / S2 wrong choice or lost time / S3 cleanup),
file:line, evidence (quoted code), why it matters vs the North Star, suggested fix, how to test it.
Known already (don't re-report, but extend if you find more): lab/leverage.py CLOSE_LATE has no CFB/CBB/MLB keys.
