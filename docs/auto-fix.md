# Auto-fix: Claude repairs what the watchdog can't

RedZone has two tiers of self-repair:

- **Reflex, on the Mac.** The watchdog (`agent/watchdog.py`) uses rules plus the small local model.
  It restarts the app, reconnects sticks and frees disk, within a fixed, guarded set of actions.
- **Brain, in the cloud.** When the reflex can't fix something, Claude does. It reads the code,
  finds the cause, writes a fix and a regression test, and opens a draft PR. It runs on **your
  Claude subscription**, not a paid API key.

## Setup (2 minutes, once)

1. **Make a token from your Claude subscription.** On the Mac (or any computer with Claude Code),
   run `claude setup-token` in Terminal. A browser opens: sign in with your Claude account. The
   terminal then prints a long token that starts with `sk-ant-oat01-`. Copy it.
2. **Give it to GitHub.** Open the repository's
   [new Actions secret page](https://github.com/BouncySlime1215/redzone-everything/settings/secrets/actions/new):
   - **Name:** `CLAUDE_CODE_OAUTH_TOKEN`
   - **Secret:** paste the token, then click **Add secret**.

   GitHub encrypts it. Only the auto-fix workflow reads it, and nobody (including Claude) can read
   it back.
3. **That's it.** The workflow file has to be on `main` before GitHub will run it for issues, so it
   switches on when this work reaches `main` through the release.

Optional:

- **The Mac needs to be signed in to GitHub to file issues.** It uses the `gh` CLI, or else the
  sign-in `git` already keeps in the keychain. If your phone ever shows *"Auto-fix: couldn't reach
  GitHub (no sign-in on the Mac)"*, run `brew install gh && gh auth login` once on the Mac. Until
  then, each report waits in `data/autofix/outbox/`.
- **Daily cap:** the repository variable `AUTOFIX_MAX_PER_DAY` sets it (default 3). Set it under
  [Actions variables](https://github.com/BouncySlime1215/redzone-everything/settings/variables/actions).

## Kill switches

- **GitHub side:** set the repository variable `AUTOFIX_ENABLED` to `false` (same Variables page).
  The workflow then does nothing. Delete the variable to turn it back on.
- **Mac side:** set `autofix: {enabled: false}` in `data/config.yaml`. The watchdog then stops
  filing issues.
- **Fully off:** delete the `CLAUDE_CODE_OAUTH_TOKEN` secret. You can also revoke the token from
  your Claude account settings at any time.

## How a fix flows

1. **The watchdog files an issue labeled `auto-fix`.** It files in two cases: a crash loop (3
   restarts in an hour), or an error no rule knows that keeps happening (3 checks in a row). The
   issue carries:
   - the matching log lines and the last log lines
   - the health report
   - recent switch traces and decisions
   - the supervisor's fixes and the watchdog journal
   - your settings

   Everything is **redacted** first: tokens, keys, passwords, cookies, the ntfy topic, signed URLs,
   emails, IP addresses and your home folder name. **The repo is public**, so the issue is too.
   The Mac files at most 3 a day (`autofix.max_per_day`) and the same error at most once a week.
2. **`.github/workflows/auto-fix.yml` runs `anthropics/claude-code-action` on the issue.** It runs
   only for issues you (the repo owner) filed or labeled, only while the kill switch is off, and
   within the daily cap. Claude reads the issue as data, never as instructions. It can only read
   and edit files and run the tests and ruff: no web access, no pushing, no `gh`, no environment
   variables.
3. **The guard checks Claude's diff** (`scripts/autofix_guard.py`). It refuses the whole change if
   any of these are true:
   - it touches CI, workflows, the gate or eval scripts, `docs/gate_baseline.json`, `data/`,
     `scripts/deploy.py`, requirements or `pyproject.toml`
   - it deletes, skips or xfails a test, or removes an assert
   - it adds anything that looks like a credential
   - it has no regression test
   - it is bigger than 12 files or 600 lines
4. **The gates run before anything is pushed:** the end-to-end night test, the full test suite and
   ruff. If they pass, a **draft PR into `integration/next`** opens (labeled `auto-fix`, assigned to
   you) and normal CI starts on it, with tests and scorecards PR vs base. If the guard or a gate
   fails, nothing is pushed and the issue gets a comment saying why.
5. **Merging stays with the integration train or you.** It never merges itself. After a merge,
   `scripts/deploy.py` ships it only when no game is live and none starts within 15 minutes, so
   never in crunch. It then watches health for 10 minutes and rolls code, config and models back on
   its own if things got worse.
6. **The nightly report** (on your phone) gets an "Auto-fix" line, and the full report gets a "Sent
   to Claude" section listing what was filed and where.

## Known limits

- The `issues` trigger only works once the workflow is on `main` (the default branch). Until then
  you can start a fix by hand: open Actions, pick **auto-fix**, click **Run workflow**, and enter
  the issue number.
- Claude's run uses your subscription's usage, at most 3 runs a day by default.
- Redaction is pattern-based. A secret stored under an innocent-looking key in a YAML block could
  slip through. Settings are trimmed to 6 KB, and secrets don't belong in `config.yaml` anyway.
