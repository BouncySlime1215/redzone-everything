# Debugging

Order matters: reproduce, shrink, map, bisect, name the cause, then fix. Source:
[W1-RULES](../../agents/rules/2026-11-08-debug-validate-merge.md) gap 3. A fresh read-only `debugger` subagent can
run steps 1-5 and return the note in step 5.

Never run adb, a deploy or training to debug (G3, G4, G5). One pytest at a time on the Mac.

1. **Reproduce.** Get the exact command that shows the bug and its output, before editing anything. Paste both into
   the PR's `## Repro`. A live-only bug: find the moment first with `python scripts/why.py 20:42` (state moves, jumps,
   vetoes, each row with the `sha` that made the call), then replay that night.
2. **Write the minimal failing test.** The smallest assertion that is red because of the bug, not a print probe. Red
   for the right reason: read the failure line. It becomes the regression test (`scripts/fix_needs_test.py` requires one).
3. **Map symptom to code.** `python scripts/why.py <file or config key>` for the decisions and lessons behind it.
   `python scripts/citymap.py --focus <file>` for what it routes to, `--impact <file>` for what relies on it.
   [docs/system-map.md](../../system-map.md) for the flow. Check [lessons.md](../lessons.md) for its log words.
4. **Bisect when it used to work.** Pick a commit where the test passes (the `sha` from `why.py`, or a release tag):

       cp tests/test_new.py /tmp/rz_test_new.py    # the test does not exist in old commits
       PYTHON=.venv/bin/python scripts/bisect_test.sh --good <sha> /tmp/rz_test_new.py::test_it [night]

   It prints `Bisected-to: <sha> <subject>`. Add `--needs <path>` so commits are skipped, not blamed, when data is missing.
   Everything fails at once? Use a known-good oracle: swap one module back to its base version, rerun the recorded
   night, and repeat until the failure moves.
5. **Name the root cause in one line, `file:line`.** If you can't, you aren't done; go back to step 3. The note:

       Repro: <command> -> <output line>
       Bisected-to: <sha subject, or "not bisected: reason">
       Root cause: <file:line, one sentence>

6. **Fix the cause, not the symptom.** Test goes from red to green; the tests for its callers (`--impact`) stay green.
   Never edit a test or a gate to get there: write `BLOCKED: <reason>` instead.
7. **Write it down.** Add a lesson to [lessons.md](../lessons.md) with `Repro:`, `Bisected-to:` and literal `Signals:`.

**Keep a "Tried and failed" list** in the PR or card as you go, one line per approach, so the next session skips them.

**Stop after 3 loops on the same error** ([coordination](../../coordination.md) section 2) and write `BLOCKED: <reason>`,
with the list above. Failed runs get longer and more erratic, not closer.
