# Results

Design: [W2-LOCKED](../docs/agents/locked-results/2026-11-09-combined-design.md).

- Cite `summary/latest.json`. `python scripts/results_ledger.py summary` prints it.
- `ledger.jsonl`, `HEAD` and `summary/` are written only by `scripts/results_ledger.py`. A wrong row is retracted, never edited.
- `python scripts/results_ledger.py verify` proves the chain is intact (CI runs it on every PR).
- Model registry (F-02): `kind: result`, `suite: model-registry` rows hold one `registry` event (register, promote,
  demote). Write them only with `python scripts/model_registry.py`; the runtime loads a model only through
  `redzone/models_rt/registry.py`, which refuses any run that is not promoted or whose artifact sha256 changed.
- `dev/<UTC-date>-<sha7>-<suite>.json`: full detail on dev nights. Not in git.
- `locked/`: reserved, stays empty, and no Claude session may read it. Not in git.
