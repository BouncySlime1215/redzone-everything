#!/usr/bin/env python3
"""Hash-chained, append-only ledger of test results, plus the verdict-only public view of locked gates.

Why: process gate G-08 (W2-LOCKED in docs/agents/locked-results/2026-11-09-combined-design.md): a number nobody can
prove unedited is worthless, and per-night locked scores on public CI are the tuning feedback the no-overfitting rule forbids.

    python scripts/results_ledger.py init                    # once: writes the genesis row
    python scripts/results_ledger.py append --kind live --suite hdmi1-nfl --verdict PASS --metric entries_mid_play=0 --raw data/x.json
    python scripts/results_ledger.py verify [--base origin/integration/next]   # exit 1 on any edit, delete or rewrite
    python scripts/results_ledger.py summary                 # what agents read (also results/summary/latest.json)
    python scripts/results_ledger.py public-gate locked.json --baseline docs/gate_baseline.json --md gate-public.md
    python scripts/results_ledger.py scrub head.json base.json   # drops per-night detail before an artifact upload

Rows: `seq`, `ts`, `kind`, `suite`, `commit`, `prev` (hash of the row before), `hash` (sha256 of this row's canonical JSON
without `hash`), and optional `verdict`, `metrics`, `raw_sha256`, `prereg`, `release`, `retracts`, `note`.
A wrong row is never removed: a `retract` row names it and says why. A `locked_eval` row holds a verdict only (no
metrics, no night ids) and there is one per release unless the earlier one was retracted.

Limits: this proves a row was not changed after the anchor (the base branch, later a signed ref). It cannot prove the
number was honest when written, and whoever can rewrite the base branch can rewrite the chain. Stdlib only.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_DIR = ROOT / "results"
LEDGER_NAME = "ledger.jsonl"
HEAD_NAME = "HEAD"
SUMMARY_NAME = "summary/latest.json"
GENESIS_PREV = "0" * 64

KINDS = ("genesis", "prereg", "result", "live", "locked_eval", "retract")
VERDICTS = ("PASS", "FAIL", "PENDING")
NIGHT_ID = re.compile(r"(?<!\d)(?:19|20)\d{6}(?!\d)")
# Keys that hold row-per-night or row-per-game detail in a scorecard. Only lists and dicts go; a scalar count stays.
DETAIL_KEYS = {"per_night", "per_game", "per_play", "nights", "games", "timeline", "replays"}
DETAIL_ROW_KEYS = {"night", "gid", "game", "game_id", "date"}


class LedgerError(Exception):
    """The ledger is broken, or a requested row is not allowed."""


def canon(rec: dict) -> bytes:
    try:
        return json.dumps(rec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except ValueError as exc:
        raise LedgerError(f"not canonical JSON: {exc}") from exc


def digest(rec: dict) -> str:
    return hashlib.sha256(canon({k: v for k, v in rec.items() if k != "hash"})).hexdigest()


def parse_chain(data: bytes) -> list[dict]:
    """Every row, checked for seq, prev link, own hash and exact canonical bytes. Raises at the first break."""
    if data and not data.endswith(b"\n"):
        raise LedgerError("last row has no newline (a partial write or an edit)")
    recs: list[dict] = []
    prev = GENESIS_PREV
    for i, line in enumerate(data.splitlines()):
        try:
            rec = json.loads(line)
        except ValueError as exc:
            raise LedgerError(f"row {i}: not JSON ({exc})") from exc
        if not isinstance(rec, dict) or "hash" not in rec:
            raise LedgerError(f"row {i}: not a ledger row")
        if rec.get("seq") != i:
            raise LedgerError(f"row {i}: seq is {rec.get('seq')!r} (a row was removed or moved)")
        if rec.get("prev") != prev:
            raise LedgerError(f"row {i}: prev does not match the row before (chain break)")
        if digest(rec) != rec["hash"]:
            raise LedgerError(f"row {i}: hash does not match its content (the row was edited)")
        if canon(rec) != line:
            raise LedgerError(f"row {i}: not in canonical form (the row was edited)")
        prev = rec["hash"]
        recs.append(rec)
    check_rules(recs)
    return recs


def check_rules(recs: list[dict]) -> None:
    """The rules a row must meet given the rows before it. Runs on append and on verify, so a hand-built chain fails too."""
    preregs: set[str] = set()
    by_seq: dict[int, dict] = {}
    retracted: set[int] = set()
    used: dict[str, int] = {}          # release tag -> seq of its live locked_eval row
    for rec in recs:
        seq, kind = rec["seq"], rec.get("kind")
        if kind not in KINDS:
            raise LedgerError(f"row {seq}: unknown kind {kind!r}")
        if (kind == "genesis") != (seq == 0):
            raise LedgerError(f"row {seq}: the genesis row is row 0 and only row 0")
        if rec.get("verdict") is not None and rec["verdict"] not in VERDICTS:
            raise LedgerError(f"row {seq}: verdict {rec['verdict']!r} not in {VERDICTS}")
        if kind == "prereg":
            if not rec.get("prereg"):
                raise LedgerError(f"row {seq}: a prereg row needs an id")
            if rec["prereg"] in preregs:
                raise LedgerError(f"row {seq}: prereg {rec['prereg']} already exists")
            preregs.add(rec["prereg"])
        elif rec.get("prereg") and rec["prereg"] not in preregs:
            raise LedgerError(f"row {seq}: prereg {rec['prereg']} has no earlier prereg row")
        if kind == "retract":
            target = rec.get("retracts")
            if not rec.get("note"):
                raise LedgerError(f"row {seq}: a retract row must say why")
            if target not in by_seq or by_seq[target]["kind"] in ("genesis", "retract"):
                raise LedgerError(f"row {seq}: retracts {target!r}, which is not an earlier result row")
            if target in retracted:
                raise LedgerError(f"row {seq}: row {target} is already retracted")
            retracted.add(target)
            if by_seq[target]["kind"] == "locked_eval":
                used.pop(by_seq[target].get("release"), None)
        if kind == "locked_eval":
            if rec.get("metrics"):
                raise LedgerError(f"row {seq}: a locked_eval row holds a verdict only, no metrics")
            if not rec.get("verdict") or not rec.get("release"):
                raise LedgerError(f"row {seq}: a locked_eval row needs --verdict and --release")
            for field in ("suite", "note", "release"):
                if NIGHT_ID.search(str(rec.get(field) or "")):
                    raise LedgerError(f"row {seq}: {field} names a night; locked rows carry no per-night detail")
            if rec["release"] in used:
                raise LedgerError(f"row {seq}: release {rec['release']} already used its locked run "
                                  f"(row {used[rec['release']]}); retract it with a reason first")
            used[rec["release"]] = seq
        by_seq[seq] = rec


def build_summary(recs: list[dict]) -> dict:
    """The agent-facing view: latest row per kind/suite and how many runs each had, so best-of-N shows."""
    retracted = {r["retracts"] for r in recs if r["kind"] == "retract"}
    latest: dict[str, dict] = {}
    runs: dict[str, int] = {}
    for r in recs:
        if r["kind"] in ("genesis", "retract", "prereg"):
            continue
        key = f"{r['kind']}/{r.get('suite', '')}"
        runs[key] = runs.get(key, 0) + 1
        if r["seq"] in retracted:
            continue
        latest[key] = {k: r[k] for k in ("seq", "ts", "commit", "verdict", "metrics", "release", "prereg") if k in r}
    return {"records": len(recs), "head": recs[-1]["hash"] if recs else GENESIS_PREV,
            "latest": dict(sorted(latest.items())), "runs": dict(sorted(runs.items())), "retracted": sorted(retracted)}


def _head_text(recs: list[dict]) -> str:
    return f"{len(recs) - 1} {recs[-1]['hash']}\n"


def _summary_text(recs: list[dict]) -> str:
    return json.dumps(build_summary(recs), indent=1, sort_keys=True) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _refresh(base: Path, recs: list[dict]) -> None:
    _write_atomic(base / HEAD_NAME, _head_text(recs))
    _write_atomic(base / SUMMARY_NAME, _summary_text(recs))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_row(f, recs: list[dict], fields: dict) -> dict:
    rec = {"seq": len(recs), "ts": _now(), "prev": recs[-1]["hash"] if recs else GENESIS_PREV, **fields}
    rec["hash"] = digest(rec)
    check_rules([*recs, rec])
    f.seek(0, os.SEEK_END)
    f.write(canon(rec) + b"\n")
    f.flush()
    os.fsync(f.fileno())
    return rec


def init(base: Path, note: str) -> dict:
    path = base / LEDGER_NAME
    if path.exists():
        raise LedgerError(f"{path} already exists; a ledger is started once")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        rec = _write_row(f, [], {"kind": "genesis", "suite": "", "commit": _git_commit(), "note": note})
    _refresh(base, [rec])
    return rec


def append(base: Path, fields: dict) -> dict:
    path = base / LEDGER_NAME
    if not path.exists():
        raise LedgerError(f"{path} is missing. Run init only for a new ledger; a missing one may have been deleted")
    with path.open("r+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)      # parallel agents queue here; the row before stays the one we chain to
        recs = parse_chain(f.read())
        rec = _write_row(f, recs, fields)
    recs.append(rec)
    _refresh(base, recs)
    return rec


def _base_ledger(ref: str, rel: str) -> bytes | None:
    """The ledger on a git ref; None when the ref predates the ledger. An unknown ref is an error, never a pass."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
        raise LedgerError(f"base ref {ref} not found (fetch it; the append-only check cannot run without it)")
    if git("cat-file", "-e", f"{ref}:{rel}").returncode != 0:
        return None
    return git("show", f"{ref}:{rel}").stdout


def verify(base: Path, ref: str | None = None) -> list[dict]:
    path = base / LEDGER_NAME
    if not path.exists():
        raise LedgerError(f"{path} is missing")
    data = path.read_bytes()
    recs = parse_chain(data)
    if not recs:
        raise LedgerError(f"{path} is empty (it starts with a genesis row)")
    head = base / HEAD_NAME
    if not head.exists() or head.read_text() != _head_text(recs):
        raise LedgerError(f"{head} does not match the last row (rows added or removed by hand)")
    summary = base / SUMMARY_NAME
    if not summary.exists() or summary.read_text() != _summary_text(recs):
        raise LedgerError(f"{summary} does not match the ledger (the view was edited)")
    if ref:
        try:
            rel = path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError as exc:
            raise LedgerError("--base needs a ledger inside this checkout") from exc
        old = _base_ledger(ref, rel)
        if old is not None and not data.startswith(old):
            raise LedgerError(f"this ledger does not extend the one on {ref}: history was rewritten, not appended to")
    return recs


def public_rows(cards: dict, baseline: dict | None) -> list[tuple[str, str, str]]:
    """(gate, scope, status) from the release gate: the same verdicts with the value column left out."""
    sys.path.insert(0, str(HERE))
    import release_gate as rg
    return [(gate, scope, status) for gate, scope, status, _detail in rg.gates(cards, baseline)]


def public_markdown(rows: list[tuple[str, str, str]]) -> str:
    out = ["| gate | scope | status |", "|---|---|---|"]
    out += [f"| {g} | {s} | {st} |" for g, s, st in rows]
    n = {k: sum(r[2] == k for r in rows) for k in VERDICTS}
    out.append(f"\n{n['PASS']} pass, {n['FAIL']} fail, {n['PENDING']} pending (pending never counts as passing). "
               "Verdicts only: locked values and per-night detail are withheld (W2-LOCKED).")
    return "\n".join(out)


def scrub(value):
    """A copy with per-night, per-game rows removed at any depth. Scalars and aggregate dicts stay."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if not (NIGHT_ID.fullmatch(str(k)) or (k in DETAIL_KEYS and isinstance(v, (list, dict))))}
    if isinstance(value, list):
        return [scrub(v) for v in value if not (isinstance(v, dict) and DETAIL_ROW_KEYS & set(v))]
    return value


def _metric(text: str) -> tuple[str, object]:
    key, sep, raw = text.partition("=")
    if not sep or not key:
        raise LedgerError(f"--metric wants name=value, got {text!r}")
    try:
        return key, json.loads(raw)
    except ValueError:
        return key, raw


def _fields(a) -> dict:
    fields = {"kind": a.kind, "suite": a.suite, "commit": a.commit or _git_commit()}
    if a.verdict:
        fields["verdict"] = a.verdict
    if a.metric:
        fields["metrics"] = dict(_metric(m) for m in a.metric)
    if a.raw:
        fields["raw_sha256"] = _sha256_file(a.raw)
    for key, val in (("prereg", a.prereg), ("release", a.release), ("retracts", a.retracts), ("note", a.note)):
        if val is not None:
            fields[key] = val
    return fields


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="folder holding ledger.jsonl (default results/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="start a new ledger")
    p.add_argument("--note", default="ledger started")
    p = sub.add_parser("append", help="add one row")
    p.add_argument("--kind", required=True, choices=[k for k in KINDS if k != "genesis"])
    p.add_argument("--suite", default="")
    p.add_argument("--verdict", choices=VERDICTS)
    p.add_argument("--metric", action="append", default=[], help="name=value, repeatable")
    p.add_argument("--raw", type=Path, help="raw result file: only its sha256 goes in the row")
    p.add_argument("--prereg")
    p.add_argument("--release", help="release tag, for a locked_eval row")
    p.add_argument("--retracts", type=int)
    p.add_argument("--note")
    p.add_argument("--commit", help="default: this checkout's HEAD")
    p = sub.add_parser("verify", help="exit 1 on any edit, delete or rewrite")
    p.add_argument("--base", help="git ref whose ledger this one must extend")
    sub.add_parser("summary", help="print the agent-facing view")
    p = sub.add_parser("public-gate", help="release-gate verdicts with every value withheld; exit 1 on any FAIL")
    p.add_argument("scorecards", type=Path)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--md", type=Path, required=True)
    p = sub.add_parser("scrub", help="drop per-night rows from scorecard JSON files, in place")
    p.add_argument("files", type=Path, nargs="+")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        if a.cmd == "init":
            rec = init(a.dir, a.note)
            print(f"started ledger, head {rec['hash'][:12]}")
        elif a.cmd == "append":
            rec = append(a.dir, _fields(a))
            print(f"row {rec['seq']} written, head {rec['hash'][:12]}")
        elif a.cmd == "verify":
            recs = verify(a.dir, a.base)
            print(f"ok: {len(recs)} rows, head {recs[-1]['hash'][:12]}" + (f", extends {a.base}" if a.base else ""))
        elif a.cmd == "summary":
            print(_summary_text(verify(a.dir)), end="")
        elif a.cmd == "public-gate":
            cards = json.loads(a.scorecards.read_text()).get("cards", {})
            base = json.loads(a.baseline.read_text()) if a.baseline and a.baseline.exists() else None
            rows = public_rows(cards, base)
            a.md.write_text(public_markdown(rows) + "\n")
            print(f"{sum(r[2] == 'PASS' for r in rows)} pass, {sum(r[2] == 'FAIL' for r in rows)} fail, "
                  f"{sum(r[2] == 'PENDING' for r in rows)} pending")
            return 1 if any(r[2] == "FAIL" for r in rows) else 0
        elif a.cmd == "scrub":
            for path in a.files:
                if not path.exists():
                    print(f"{path}: not there, skipped")
                    continue
                _write_atomic(path, json.dumps(scrub(json.loads(path.read_text())), indent=1))
                print(f"{path}: per-night detail removed")
    except (LedgerError, OSError, ValueError) as exc:
        print(f"results_ledger: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, (OSError, ValueError)) and a.cmd in ("public-gate", "scrub") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
