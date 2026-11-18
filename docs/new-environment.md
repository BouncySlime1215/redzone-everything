# Moving to the new Claude account (Ultra, unlocks Wed 5 PM)

Your new account starts empty: no project, no GitHub connection, no memory. Everything needed to
rebuild it lives in this repo. You do the sign-ins (I can't enter passwords or tokens); everything
else gets pasted in.

## You (about 10 min)

1. Sign in to claude.ai with the new account. In the Claude desktop app, switch to it
   (account menu, bottom-left).
2. **Settings → Customize → Connectors → GitHub Integration → Connect**, as **BouncySlime1215**
   (the account that owns the repo; the Claude GitHub App is already installed with access to all repos).
3. **Code → Projects → New project**, named "RedZone". In **Project settings → Environment**:
   - **Add repository:** `BouncySlime1215/redzone-everything`
   - **Let Claude use your device:** On
   - **Pre-approved folders:** `/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone`
     and `~/.cache/huggingface`
4. Open a Claude Code session in the desktop app on the repo folder above and paste the
   **Director bootstrap** below.

## Director bootstrap (paste into the new session)

```
You are the RedZone director. Read, in order: docs/handoff.md, docs/context/claude-memory/*.md
(copy them into your own memory dir), docs/status.md, docs/architecture.md, docs/archive/tonight-checklist.md.
Then:
1) Every 10 min, in ONE pass: usage guard (pause the project at 88% weekly usage), PRs + CI on
   integration/next, the Kaggle run (kaggle.com/code/nickmatta/notebooka466d7f817 and its round-2
   successor), the project threads. Send one concise directive if something's off. Update §5 of
   docs/handoff.md and push it.
2) Re-create the claude.ai Code Project coordinator with the rules in docs/handoff.md §7 (max 5
   threads, owners from docs/architecture.md, 95% gates on unseen data).
3) Only message me when I must act. ADHD-style replies.
```

## Project coordinator bootstrap (paste into the new project's chat)

```
You are the coordinator for RedZone Everything. Repo BouncySlime1215/redzone-everything, base
codex/resume-redzone, integration branch integration/next. Read docs/handoff.md,
docs/architecture.md, docs/status.md and the open PRs, then resume the queue in docs/status.md with
the owner map from docs/architecture.md. Rules: at most 5 working threads; every PR targets
integration/next; CI green; scorecards no worse; 95% gates judged only on locked/fresh unseen
nights; $0 budget; never ask the user for tokens; stop at 88% weekly usage. Keep docs/status.md
current after every merge.
```

## Every clone (about 5 seconds)

`git config rerere.enabled true`, so git replays conflict resolutions you already made
([merge runbook](memory/runbooks/merge.md)).

## What carries over automatically

- Code, branches, PRs, CI, docs: GitHub.
- Kaggle (account `nickmatta`): datasets, notebooks, GPU quota. Not tied to Claude.
- The live app and watchdog on the Mac.

## What doesn't carry over

- The old project's thread history. The repo docs replace it (handoff, status, architecture,
  integration, the `docs/memory/` knowledge base).
- Scheduled checks in this session. The new director recreates them.
