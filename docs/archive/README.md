# docs/archive/

Docs that are history, not current, moved here on Nov 11, 2026 with `git mv`: the planner cycles of Nov 4-5
(`cycles/`), the Nov 8 audits (`audits/`), the Brain v3 report (`brain3/`), the Nov 3 research queue
(`research/2026-11-03/`), superseded plans (`plans/`), old gate reports, dated checklists and reports, and the root
`RESUME.md`. Each keeps its old path under `docs/archive/` (root files sit at the top), and links to and from them
were updated in the same change. Nothing here runs or is read by code or CI; the current docs are listed in
[docs/README.md](../README.md), and archived code is in [archive/](../../archive/README.md). To bring a doc back, move it
to its old path: `git mv docs/archive/plans/2026-11-08-fix-plan.md docs/plans/2026-11-08-fix-plan.md` (for
`RESUME.md`: `git mv docs/archive/RESUME.md RESUME.md`), then fix the links that point at it and add it back to
docs/README.md.
