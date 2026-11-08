#!/usr/bin/env python3
"""PreCompact: tell the summarizer what must survive (research: one /compact kept 53% of rules, five kept 10%).
Stdout becomes compaction instructions. Facts and a keep-list only; stdlib; errors print nothing."""
print("""When summarizing, keep verbatim:
- Rule #1: join a game only at a dead ball; never leave a high-leverage moment (close and late, or overtime).
- The current task card or goal, its next action, files claimed (docs/claims/), and anything marked unverified.
- Decisions Nick made in this conversation, with his words, and any BLOCKED reasons.
- Which branch and worktree the work is on, what is committed and what is not.
Drop tool output and file dumps; keep file paths and line numbers.""")
