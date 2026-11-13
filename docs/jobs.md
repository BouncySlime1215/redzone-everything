# Jobs on Colab and Kaggle (F-03 job runner)

Training never runs on the Mac. A job is a JobSpec (`tools/jobs/spec.py`): pinned commit, entry module, config, seed, step range, manifest hashes, expected outputs, wall-clock budget. The worker (`tools/jobs/worker.py`) runs it on the VM in resumable units and prints one `RZ-RESULT` line. Nothing in the app imports `tools/jobs`.

## Run a job on Colab through Chrome (the coordinator)

1. Push the commit the job needs; the cell fetches `tools/jobs/worker.py` at that exact sha from GitHub and checks its sha256.
2. `python scripts/job.py spec --job-id NAME --module MOD --commit HEAD --seed N --steps 0 END --ckpt-every K --output F ... --out spec.json` (refused: short sha, no seed, unknown data hash).
3. `python scripts/job.py cell spec.json > cell.txt` (about 1,300 characters; it has no indented lines, so typing it is safe).
4. In Nick's Chrome, open a new Colab notebook (Runtime > Change runtime type for a GPU; CPU is the default; never buy compute units).
5. Click into the first cell, type the text of `cell.txt`, press Shift+Enter. If the editor doubled any bracket or quote, clear the cell and paste the text instead.
6. Read the output with get_page_text: `RZ-PROGRESS` lines show the step reached; wait until `RZ-RESULT` and `RZ-EXIT` appear.
7. Save the page text to a file and run `python scripts/job.py parse spec.json page.txt`: exit 0 accepted ok, 3 partial, 1 refused or failed.
8. `partial` (self-stop or SIGTERM): run the same cell again in the same runtime; it resumes from the last checkpoint. A new runtime starts over unless the work folder is on Drive (mounting Drive needs Nick to authorise it).
9. Output files sit in `/content/rz_job/NAME/work/out/` with `MANIFEST.json` and `DONE`; download them, then `parse ... --files DIR` re-hashes each one.
10. Never paste tokens, passwords or Drive links into a cell; the worker reads none and clones only plain https URLs.
11. Prove resume locally first: `python scripts/job.py local-smoke` (toy job: killed at step 50 and resumed equals uninterrupted).
12. Unverified: no job has run through this cell on Colab yet; the first one should be the toy (`--module tools.jobs.toy`, outputs `metrics.json model.bin state.json`).

## What the Kaggle path needs from Nick

A Kaggle API token on the Mac (WORKPLAN N1, his call) and the Kaggle CLI. `tools/jobs/kaggle_backend.py` builds `kernel-metadata.json` and a `run.py` (the cell with the worker embedded) for `kaggle kernels push -p DIR`; it is untested against Kaggle and unused until then. Without a token, Kaggle stays the manual Chrome route.
