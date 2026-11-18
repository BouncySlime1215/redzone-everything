"""Split the Linux unit suite into N CI shards by test file, and prove the shards cover every file exactly once.

Why: CI-FIX (Nov 10): the suite grew from ~3,600 to ~7,000 tests (298 to 455 files) and the one-job test leg
ran past its 45-minute limit on every run of PR #423 (pytest at 17-37% when cancelled, the e2e nights 17-23
min before it). Not a hang: it is size. So each Python version runs the unit suite in N parallel jobs.

    python scripts/ci_shard.py run --shard 2/4 --report shard.json -- -q -rs -p no:cacheprovider ...
    python scripts/ci_shard.py check --count 4 results/shard-*.json     # exit 1 unless the shards partition the suite
    python scripts/ci_shard.py weights results/unit-*.xml > scripts/ci_shard_weights.json   # refresh from CI timings

`run` is `python -m pytest <args>` plus one plugin: after collection (and after tests/conftest.py's mac_only
skips) it keeps only this shard's files and deselects the rest. Every shard collects the same suite, so the
split needs no file list kept by hand: a new test file lands in exactly one shard. Files are spread by
longest-first greedy packing on scripts/ci_shard_weights.json (seconds per file, measured on CI); a file not
in the table weighs DEFAULT_PER_TEST seconds per collected test. The weights only balance the shards; they
never decide whether a file runs. `check` reads every shard's report and fails if any file is missing,
doubled, or if the shards disagree on what the suite is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = ROOT / "scripts" / "ci_shard_weights.json"
DEFAULT_PER_TEST = 0.35   # s per test on a GitHub runner (Nov 10 runs, the measured part without the replay file)


def assign(counts: dict[str, int], n: int, weights: dict[str, float] | None = None) -> list[list[str]]:
    """Partition the files into n shards (index 0..n-1), heaviest first onto the lightest shard. Deterministic."""
    if n < 1:
        raise ValueError("shard count must be at least 1")
    weights = weights or {}
    cost = {f: float(weights.get(f, c * DEFAULT_PER_TEST)) for f, c in counts.items()}
    shards: list[list[str]] = [[] for _ in range(n)]
    load = [0.0] * n
    for f in sorted(cost, key=lambda f: (-cost[f], f)):
        i = min(range(n), key=lambda k: (load[k], k))
        shards[i].append(f)
        load[i] += cost[f]
    return [sorted(s) for s in shards]


def load_weights(path: Path = WEIGHTS) -> dict[str, float]:
    try:
        return {k: float(v) for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}


def parse_shard(text: str) -> tuple[int, int]:
    """'2/4' -> (2, 4), 1-based."""
    i, _, n = text.partition("/")
    i, n = int(i), int(n)
    if not 1 <= i <= n:
        raise ValueError(f"bad shard {text!r}: want i/n with 1 <= i <= n")
    return i, n


class ShardPlugin:
    def __init__(self, index: int, count: int, report: str | None, weights: dict[str, float]):
        self.index, self.count, self.report, self.weights = index, count, report, weights

    def pytest_collection_modifyitems(self, config, items):
        files = Counter(item.nodeid.split("::")[0] for item in items)
        mine = set(assign(dict(files), self.count, self.weights)[self.index - 1])
        keep = [it for it in items if it.nodeid.split("::")[0] in mine]
        dropped = [it for it in items if it.nodeid.split("::")[0] not in mine]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
        items[:] = keep
        if self.report:
            Path(self.report).write_text(json.dumps({
                "shard": self.index, "count": self.count, "all": sorted(files), "mine": sorted(mine),
                "tests": len(keep)}, indent=1) + "\n")
        print(f"\nci_shard {self.index}/{self.count}: {len(mine)} of {len(files)} files, {len(keep)} of {len(items) + len(dropped)} tests")


def check(reports: list[dict], count: int) -> list[str]:
    """Problems with a set of shard reports; empty means the shards partition the suite exactly."""
    errs = []
    seen = sorted(r.get("shard") for r in reports)
    if seen != list(range(1, count + 1)):
        errs.append(f"expected one report per shard 1..{count}, got {seen}")
    if any(r.get("count") != count for r in reports):
        errs.append("a shard ran with a different shard count")
    suites = {tuple(r.get("all", [])) for r in reports}
    if len(suites) > 1:
        errs.append("the shards collected different suites (same commit and Python should collect the same files)")
    every = set().union(*(set(r.get("all", [])) for r in reports)) if reports else set()
    owners = Counter(f for r in reports for f in r.get("mine", []))
    missing = sorted(every - set(owners))
    doubled = sorted(f for f, c in owners.items() if c > 1)
    extra = sorted(set(owners) - every)
    if missing:
        errs.append(f"{len(missing)} file(s) ran in no shard: {', '.join(missing[:10])}")
    if doubled:
        errs.append(f"{len(doubled)} file(s) ran in more than one shard: {', '.join(doubled[:10])}")
    if extra:
        errs.append(f"{len(extra)} file(s) ran but are not in the collected suite: {', '.join(extra[:10])}")
    return errs


def junit_weights(paths: list[str], root: Path = ROOT) -> dict[str, float]:
    """Seconds per test file from pytest junit reports (classname 'tests.test_x[.Class]' -> 'tests/test_x.py')."""
    out: dict[str, float] = {}
    for p in paths:
        for tc in ET.parse(p).getroot().iter("testcase"):
            parts = (tc.get("classname") or "").split(".")
            for k in range(len(parts), 0, -1):
                f = "/".join(parts[:k]) + ".py"
                if (root / f).exists():
                    out[f] = out.get(f, 0.0) + float(tc.get("time") or 0)
                    break
    return {f: round(s, 1) for f, s in sorted(out.items())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run pytest on one shard; arguments after -- go to pytest")
    r.add_argument("--shard", required=True, help="i/n, 1-based")
    r.add_argument("--report", help="write this shard's file list here (JSON) for `check`")
    r.add_argument("pytest_args", nargs=argparse.REMAINDER)
    c = sub.add_parser("check", help="fail unless the shard reports partition the suite")
    c.add_argument("--count", type=int, required=True)
    c.add_argument("reports", nargs="+")
    w = sub.add_parser("weights", help="print seconds per test file from junit reports, as JSON")
    w.add_argument("junit", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "check":
        errs = check([json.loads(Path(p).read_text()) for p in a.reports], a.count)
        for e in errs:
            print(f"::error::ci_shard: {e}")
        if not errs:
            print(f"ci_shard: {a.count} shards cover the suite, each file exactly once")
        return 1 if errs else 0
    if a.cmd == "weights":
        print(json.dumps(junit_weights(a.junit), indent=1))
        return 0

    index, count = parse_shard(a.shard)
    args = a.pytest_args[1:] if a.pytest_args[:1] == ["--"] else a.pytest_args
    # Same import path as `python -m pytest`: the working directory first, not scripts/.
    sys.path[0] = os.getcwd()
    import pytest
    return int(pytest.main(args, plugins=[ShardPlugin(index, count, a.report, load_weights())]))


if __name__ == "__main__":
    sys.exit(main())
