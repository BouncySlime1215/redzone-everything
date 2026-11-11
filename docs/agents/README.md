# Agent reports

Layout and rules: [docs/STANDARD.md](../STANDARD.md). Generated index of every report: [INDEX.md](INDEX.md).

- `<stream>/<YYYY-MM-DD>-<slug>.md`: one report, with front matter (id, status, sources/verified, gate items).
- `<stream>/log.md`: a standing stream's append-only log (process, training).
- `data/catalog.md`: the dataset database (append-only table).

Standing streams write first to the permanent folder `/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone-agents/`
(same layout, outside git, so scheduled runs never collide with branches); the coordinator copies reviewed
content here and cites finding ids in docs/PLAN.md.
