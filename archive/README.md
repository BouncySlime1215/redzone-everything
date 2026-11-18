# archive/

Code that nothing in the app, CI, the grader or the eval scripts imports any more, moved here on Nov 11, 2026 with
`git mv` so its history stays intact. Each file keeps its old path under `archive/`: `archive/redzone/...` holds the
lab and tuner modules (the "Also close" and orientation graders, Brain v3 training, YouTube TV multiview),
`archive/tests/` their tests, and `archive/scripts/` the scripts that only drove them. One-off scripts with no
user live in [`scripts/archive/`](../scripts/archive/) and old docs in [`docs/archive/`](../docs/archive/README.md).
Nothing here runs: pytest only collects `tests/`, the app and `citymap.py` do not look here, and the imports inside
these files point at paths that no longer exist. To bring a file back, move it to its old path, together with its
test and script: `git mv archive/redzone/lab/orient.py redzone/lab/orient.py` (likewise `archive/tests/...` to
`tests/...` and `archive/scripts/...` to `scripts/...`, or `scripts/archive/x.py` to `scripts/x.py`), then run
`python scripts/citymap.py` and the tests.
