---
id: W1-TAMPER
title: Tamper-evident storage for test and experiment results
date: 2026-11-08
stream: locked-results
author: wave-1 locked-results agent
status: draft
supersedes: []
sources: 30
verified: 0
affects: [data/ results, .github/workflows/ci.yml]
gate: []
---

> Note (coordinator): verified counts only sources opened AND read; this report checked that links load (HTTP 200) without reading them, so treat it as weaker evidence.

# Locked, tamper-evident results (Topic B)

Date: 2026-11-08. Scope: how RedZone stores test and experiment results so agents cannot edit or
cherry-pick them, and a later agent can prove a number is untouched. $0, 8 GB Mac, public repo.
Source status: every URL below returned HTTP 200 on 2026-11-08 via curl (page existence only, not
full read) unless marked "snippet-only". Claims about macOS flags were tested locally (section 2).

## 1. Threat model (what we defend against)

| Threat | Example | Defense |
|---|---|---|
| Agent edits a past number | rewrites a scorecard line | hash chain + CI verify + immutable anchor |
| Cherry-picking | reruns until good, keeps best | pre-registration + every run logged, "claims" cite run ids |
| Deleting a bad run | drops a line from a jsonl | chain break (prev-hash) + external anchor of head |
| Agent writes results directly | `echo` into data/ | separate OS user owns the store; agents read summaries only |
| Whole-history rewrite (force-push) | rewritten git history | signed tags + rulesets blocking force-push + published head hash |

Honest limit: an attacker with root on the Mac, or with admin on the GitHub repo, can defeat any
local scheme. The goal is tamper-EVIDENT (detectable later), plus a cheap permission layer that
stops accidental and agent-level edits. A hash chain alone proves nothing if the attacker can
rewrite the whole chain, so the head hash must be anchored somewhere the agents cannot write
(Crosby and Wallach; Schneier and Kelsey; Russ Cox on transparency logs).

## 2. Options surveyed

**Hash chain / Merkle log.** Each record stores sha256 of the previous record; the head hash
commits to all history (Schneier and Kelsey 1999; Crosby and Wallach 2009 for tree-based logs).
Certificate Transparency (RFC 6962, RFC 9162) and Go's checksum DB use Merkle trees with inclusion
and consistency proofs; Trillian and Tessera are the open implementations; Rekor is the Sigstore
public instance. For us a linear chain is enough (hundreds to thousands of rows a night); Merkle
proofs only matter if third parties audit.

**Git as the log.** A signed commit (SSH or GPG) is already a hash-linked, signed record; GitHub
shows "Verified" and rulesets can require signed commits and block force-push and deletion.
`gitsign` signs with Sigstore keyless OIDC; SSH signing is simpler and $0.

**Sigstore, in-toto, SLSA, GitHub artifact attestations.** `cosign sign-blob` signs a file and
logs it in Rekor; in-toto attestations bind a predicate (e.g. "scorecard v3 for commit X") to a
subject digest; GitHub `actions/attest-build-provenance` produces these for public repos at no
cost. Best use here: attest the CI-produced results bundle, so a number is tied to a commit, a
workflow and a runner, not to an agent's say-so.

**Trusted timestamps.** OpenTimestamps anchors a hash in Bitcoin (free, no account);
RFC 3161 TSAs do the same with a CA. Gives "this hash existed before date D".

**Experiment trackers.** MLflow (local file or sqlite, free, but runs are mutable by API: delete
and set_tag exist), W&B (free tier; Artifacts are versioned with digests, but the cloud account
is a secret and the run history can be deleted by the owner), DVC (content-addressed data and
metrics, Git-tracked `dvc.lock`, experiments as Git refs). None is write-once by itself; they
add run identity and digests. At 8 GB we skip MLflow server; DVC is optional, only for big
artifacts. Recommendation: do NOT adopt a tracker now; the chain below gives the same guarantee
with stdlib Python.

**Write-once storage at $0.** S3 Object Lock is real WORM but costs money (rule: ask first;
snippet-only on pricing). GitHub Releases with release immutability, Actions artifacts (expire,
deletable by writers) and git tags are the free options. A public GitHub release or a tag push
by a CI-only identity is the cheapest external anchor.

**macOS file flags (tested 2026-11-08 on this Mac, Darwin 25.5).** `chflags uappnd FILE` makes
append succeed but truncate and `rm` fail with "Operation not permitted". BUT the owner can run
`chflags nouappnd FILE` and clear it, so an agent running as Nick can undo it (tested, rc=0).
`schg`/`sappnd` (system flags) need root and do not survive a determined root user, and at
securelevel 0 root can also clear them. Conclusion: flags stop accidents and sloppy agents; the
real boundary is a different OS user that owns the files, with agents running as Nick having read
only via group or ACL.

**Pre-registration.** COS Preregistration, and Nosek et al. ("The preregistration revolution",
PNAS 2018) argue for fixing hypotheses and analysis before seeing data; Simmons et al. (2011) show
researcher degrees of freedom inflate false positives. Our analog: a `prereg` record written
BEFORE a run (metric, threshold, dataset night ids, flag name) and chained, so the later result
must reference it.

## 3. Design for RedZone

### 3.1 Where results live

| Tier | Path | Writer | Agent access |
|---|---|---|---|
| Raw local logs | `data/` (gitignored, Mac) | live app (user `nick`) | read via summaries only |
| Sealed ledger | `/Users/Shared/rz-ledger/ledger.jsonl` | user `rzledger` via `rz_seal` | read-only (world-readable, owned by rzledger, mode 0644, dir 0755) |
| Public record | `results/ledger.jsonl` + `results/HEAD` in repo branch `results` | CI only (bot identity) | PRs cannot touch (ruleset) |
| Agent view | `docs/results-summary.md` (generated) | CI | free to read; that is the only thing agents cite |

"Hidden": raw rows with details (team names, private TV logs) stay in the local sealed ledger; the
public repo gets only per-run digests and aggregate metrics (repo is public; no secrets, per CLAUDE.md).

### 3.2 Record format (one JSON line)

```json
{"seq":42,"ts":"2026-11-08T22:10:03Z","kind":"result","prereg":"pr-0007",
 "commit":"<git sha>","metric":"entries_mid_play","value":0,"n":118,
 "inputs_sha256":"<hash of scorecard json>","prev":"<sha256 of previous line bytes>",
 "hash":"<sha256 of this line without the hash field>"}
```
kinds: `prereg`, `result`, `retract` (never delete; a wrong row is superseded by a retract row with
a reason). Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",",":"))`.

### 3.3 Who writes (one-time setup, about 10 minutes, Nick runs it)

```sh
# 1. Dedicated user (needs Nick's admin password; agents must not do this)
sudo sysadminctl -addUser rzledger -fullName "RZ ledger" -password - -home /var/empty -shell /usr/bin/false
sudo mkdir -p /Users/Shared/rz-ledger && sudo chown rzledger:staff /Users/Shared/rz-ledger
sudo chmod 755 /Users/Shared/rz-ledger
sudo touch /Users/Shared/rz-ledger/ledger.jsonl && sudo chown rzledger /Users/Shared/rz-ledger/ledger.jsonl
sudo chmod 644 /Users/Shared/rz-ledger/ledger.jsonl
sudo -u rzledger chflags uappnd /Users/Shared/rz-ledger/ledger.jsonl   # append-only extra layer
# 2. Only one sudo rule: nick may run the sealer as rzledger (no shell)
echo 'nick ALL=(rzledger) NOPASSWD: /usr/local/bin/rz_seal' | sudo tee /etc/sudoers.d/rz_seal
sudo visudo -cf /etc/sudoers.d/rz_seal
```
`rz_seal` (root-owned, 0755, not writable by nick) reads one JSON record on stdin, fills seq,
prev, hash, then `open(path,"a")` + fsync. Agents call it only through the scorecard scripts,
which pass values computed from files, never free text. Caveat: agents running as nick can still
call `sudo -u rzledger rz_seal` with fake values, so the sealer should only accept a `kind` plus a
path to a result file under `data/` and recompute the metric itself (no value from the caller).
The rzledger user is for the Mac-local tier; the CI tier below is the stronger one.

### 3.4 Where it is verified in CI

CI-produced numbers are the trusted ones (no agent runs the job; PRs from agents cannot alter the
workflow if `.github/workflows` is code-owned). Sketch of `.github/workflows/results.yml`:

```yaml
on: { push: { branches: [integration/next] } }
permissions: { contents: write, id-token: write, attestations: write }
jobs:
  seal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { ref: results, path: results, fetch-depth: 0 }
      - uses: actions/checkout@v4
        with: { path: src }
      - run: python src/scripts/results_ledger.py verify results/ledger.jsonl --head results/HEAD
      - run: python src/scripts/ci_scorecard.py --nfl-n 2 --nights 4 --json > card.json
      - run: python src/scripts/results_ledger.py append results/ledger.jsonl --prereg-check --from card.json
      - uses: actions/attest-build-provenance@v2
        with: { subject-path: results/ledger.jsonl }
      - run: cd results && git add -A && git commit -m "ledger" && git push
```
(Pin action versions at implementation time; v-numbers above are illustrative, verify them.)
Ruleset on branch `results`: block force-push, block deletion, restrict pushes to the CI app,
require signed commits. Release tag `ledger-YYYY-MM-DD` annotated and signed, containing the head
hash; optionally `ots stamp results/HEAD` for an external timestamp.

### 3.5 Verify script (exact, stdlib only; `scripts/results_ledger.py verify`)

```python
import hashlib, json, sys
def h(b): return hashlib.sha256(b).hexdigest()
prev = "0"*64
for i, line in enumerate(open(sys.argv[1], "rb")):
    rec = json.loads(line); got = rec.pop("hash")
    assert rec["seq"] == i and rec["prev"] == prev, f"chain break at {i}"
    assert h(json.dumps(rec, sort_keys=True, separators=(",",":")).encode()) == got, f"edit at {i}"
    prev = got
assert prev == open(sys.argv[3]).read().strip(), "head mismatch"
print("ok", i + 1, "records, head", prev[:12])
```

### 3.6 How a later agent proves a number was not edited

1. `git fetch origin results && git log --show-signature -1 origin/results` (signed by CI app).
2. `git show origin/results:ledger.jsonl > /tmp/l && git show origin/results:HEAD > /tmp/h`
3. `python scripts/results_ledger.py verify /tmp/l --head /tmp/h` (chain intact, head matches).
4. `gh attestation verify /tmp/l --repo <owner>/<repo>` (provenance: built by that workflow at that commit).
5. Compare the head in the signed release tag: `git tag -v ledger-2026-11-08`.
6. Optional: `ots verify results/HEAD.ots`.
If step 3 passes and the quoted number is in the file at that seq, the number is unedited since
the head was anchored. Rows after the last anchor are "unanchored"; label them so.

### 3.7 How agents read results

- Agents read `docs/results-summary.md` (generated by CI from the ledger: latest value, n, gate
  pass or fail, run id, seq). They do not get write access to the ledger or CI workflow.
- Any claim in a PR must quote `seq` and `hash` prefix; a CI check (`scripts/claims.py` already
  exists in the repo, extend it) rejects numbers with no matching ledger row.
- Pre-registration: before a new experiment, the agent opens a `prereg` row via PR (metric,
  threshold, datasets, flag). CI rejects a `result` row whose `prereg` is missing, or whose dataset
  list differs from the prereg. All reruns are rows; the summary shows count of runs per prereg, so
  best-of-N is visible. Locked test set scored once per release (CLAUDE.md gate): CI refuses a
  second `locked_eval` row for the same release tag unless a `retract` row explains it.
- Live TV test results: the Mac seals a digest-only row (metric values, no raw logs); raw files
  stay in `data/`, and their sha256 is in the row so tampering with raw files is detectable.

### 3.8 Rollout (about 2 hours total, in order)

1. Write `scripts/results_ledger.py` (append, verify, summary) + tests: 45 min.
2. Create `results` branch, ruleset, CI job: 30 min.
3. Nick runs section 3.3 for the local tier: 10 min (optional; CI tier alone gives most value).
4. Wire ci_scorecard and release_gate to emit ledger rows: 30 min.
This needs a line in docs/memory/decisions.md (CLAUDE.md rule) because it changes a gate.

## 4. Open questions for Nick (not blocking)

- Is the local rzledger user worth the sudo setup, or is CI-only anchoring enough?
- OK to publish aggregate metrics (not raw logs) on the public repo?

## 5. Bibliography (all fetched 2026-11-08, HTTP 200 unless noted; access check only, not a full read)

1. Sigstore documentation. https://docs.sigstore.dev/
2. Sigstore cosign, signing blobs. https://docs.sigstore.dev/cosign/signing/signing_with_blobs/
3. Rekor transparency log. https://rekor.sigstore.dev
4. sigstore/gitsign (Git signing with Sigstore). https://github.com/sigstore/gitsign
5. in-toto attestation framework. https://github.com/in-toto/attestation
6. SLSA specification v1.0. https://slsa.dev/spec/v1.0/
7. GitHub: artifact attestations for build provenance. https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds
8. GitHub: about commit signature verification. https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification
9. GitHub: available rules for rulesets. https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets
10. GitHub: about protected branches. https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
11. RFC 6962, Certificate Transparency. https://datatracker.ietf.org/doc/html/rfc6962
12. RFC 9162, Certificate Transparency v2. https://datatracker.ietf.org/doc/html/rfc9162
13. RFC 3161, Time-Stamp Protocol. https://www.rfc-editor.org/rfc/rfc3161
14. Crosby and Wallach, Efficient Data Structures for Tamper-Evident Logging, USENIX Security 2009. https://www.usenix.org/legacy/event/sec09/tech/full_papers/crosby.pdf
15. Russ Cox, Transparent Logs for Skeptical Clients. https://research.swtch.com/tlog
16. Trillian. https://github.com/google/trillian
17. Tessera (transparency-dev). https://github.com/transparency-dev/tessera
18. OpenTimestamps. https://opentimestamps.org/
19. Gwern, Easy Cryptographic Timestamping of Files. https://www.gwern.net/Timestamping
20. DVC experiment management. https://dvc.org/doc/user-guide/experiment-management
21. DVC data versioning. https://dvc.org/doc/start/data-management/data-versioning
22. MLflow docs. https://mlflow.org/docs/latest/
23. Weights and Biases Artifacts docs. https://docs.wandb.ai/guides/artifacts/
24. Center for Open Science, Preregistration. https://www.cos.io/initiatives/prereg
25. Munafo et al., A manifesto for reproducible science, Nature Human Behaviour 2017. https://www.nature.com/articles/s41562-016-0021
26. Nosek et al., The preregistration revolution, PNAS 2018. https://www.pnas.org/doi/10.1073/pnas.1708274114 (returned 403 to curl: snippet-only; cited from memory of the paper, verify before quoting)
27. ss64, chflags (macOS). https://ss64.com/mac/chflags.html
28. git-notes and git-verify-commit manuals. https://git-scm.com/docs/git-verify-commit
29. AWS S3 Object Lock (WORM, paid; pricing not verified). https://aws.amazon.com/s3/features/object-lock/
30. GitHub Actions: storing and sharing workflow data (artifact retention and deletion). https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/storing-and-sharing-data-from-a-workflow

Not fetched, cited from memory, verify before quoting: Schneier and Kelsey 1999, "Secure Audit
Logs to Support Computer Forensics" (ACM TISSEC); Simmons, Nelson, Simonsohn 2011, "False-Positive
Psychology" (Psychological Science).
