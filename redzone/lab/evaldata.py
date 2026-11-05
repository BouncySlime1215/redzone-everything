"""One pinned eval dataset, so every thread, CI and the Mac grade on byte-identical nights.

The scorecards (scripts/eval.py, scripts/eval_sports.py, ``python -m redzone.demo``) read
``data/archive/<league>/``, ``data/eval_config.yaml`` and ``data/demo/<day>/``. None of that is in
git and the live service keeps adding nights, so two sessions that "re-download the archive" can
grade different data. ``eval-data.lock.json`` (in git, at the repo root) pins one snapshot:

    {"tag": "eval-data-v1", "asset": "eval-archive.tgz", "sha256": "<tarball>",
     "window": {"nfl": {"start": "...", "end": "..."}, ...}, "demo_days": ["20261101"],
     "files": 1234, "tree_sha256": "<digest of every file's path + sha256>"}

The tarball holds the same files plus ``data/eval_pin.json`` (the lock + every file's sha256), so a
data folder can be checked against the pin without the network. ``scripts/fetch_eval_data.py``
downloads and verifies it; ``scripts/build_eval_data.py`` makes a new one (bump the tag).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
# RZ_EVAL_LOCK=eval-data-v2.lock.json grades on another pin (a repo-root file name) without
# switching the default: v2 lands next to v1 so every lane keeps comparable deltas (cycle 01 plan, task 4).
LOCK = ROOT / os.environ.get("RZ_EVAL_LOCK", "eval-data.lock.json")
PIN_NAME = "eval_pin.json"          # inside data/, shipped in the tarball
REPO = "BouncySlime1215/redzone-everything"
LEAGUES = ("nfl", "nba", "nhl")
_DAY = re.compile(r"^\d{8}$")


def load_lock(path: Path = LOCK) -> dict | None:
    """The pin committed to git, or None when this checkout has none."""
    if not path.exists():
        return None
    lock = json.loads(path.read_text())
    for k in ("tag", "asset", "sha256", "window"):
        if k not in lock:
            raise ValueError(f"{path.name}: missing {k!r}")
    for lg, w in lock["window"].items():
        if not (_DAY.match(str(w.get("start", ""))) and _DAY.match(str(w.get("end", "")))):
            raise ValueError(f"{path.name}: window.{lg} needs start/end as YYYYMMDD")
        if str(w["start"]) > str(w["end"]):
            raise ValueError(f"{path.name}: window.{lg} starts after it ends")
    return lock


def window_end(league: str, default: str | None = None) -> str | None:
    """The last graded night for a league: the pinned window's end, else `default`."""
    try:
        lock = load_lock()
    except (ValueError, json.JSONDecodeError):
        return default
    w = (lock or {}).get("window", {}).get(league)
    return str(w["end"]) if w else default


def asset_url(lock: dict, name: str | None = None) -> str:
    return f"https://github.com/{lock.get('repo', REPO)}/releases/download/{lock['tag']}/{name or lock['asset']}"


def _day(p: Path) -> str:
    return p.stem.split("_")[-1]


def select(data: Path, window: dict, demo_days: list[str]) -> list[str]:
    """Every file the scorecards read for this window, as paths relative to the repo root
    ("data/..."), sorted. Scoreboards dated inside the window and the summaries they name."""
    out: set[str] = set()
    for lg, w in window.items():
        arch = data / "archive" / lg
        for sb in sorted(arch.glob("scoreboard_*.json")):
            d = _day(sb)
            if not (_DAY.match(d) and str(w["start"]) <= d <= str(w["end"])):
                continue
            out.add(f"data/archive/{lg}/{sb.name}")
            for e in json.loads(sb.read_text()).get("events", []):
                f = arch / f"summary_{e['id']}.json"
                if f.exists():
                    out.add(f"data/archive/{lg}/{f.name}")
    for day in demo_days:
        dd = data / "demo" / day
        if not dd.is_dir():
            raise FileNotFoundError(f"{dd} is missing: the demo day can't be pinned")
        # _work/ is the demo engine's own scratch (rewritten every run): not data.
        out.update(f"data/demo/{day}/{p.relative_to(dd).as_posix()}" for p in dd.rglob("*")
                   if p.is_file() and not p.relative_to(dd).parts[0].startswith("_"))
    for extra in ("eval_config.yaml", "_eval_channels.json"):
        if (data / extra).exists():
            out.add(f"data/{extra}")
    return sorted(out)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(hashes: dict[str, str]) -> str:
    h = hashlib.sha256()
    for path in sorted(hashes):
        h.update(f"{path}\t{hashes[path]}\n".encode())
    return h.hexdigest()


def verify(root: Path = ROOT) -> tuple[bool, list[str]]:
    """Check root/data against the pin shipped in data/eval_pin.json (and that pin against the
    lock in git). Returns (ok, problems). Extra files outside the pin are fine: the scorecards
    only grade inside the window."""
    pin_path = root / "data" / PIN_NAME
    if not pin_path.exists():
        return False, [f"no data/{PIN_NAME}: run scripts/fetch_eval_data.py"]
    pin = json.loads(pin_path.read_text())
    problems = []
    lock = load_lock(root / LOCK.name)
    if lock and (lock["tag"] != pin.get("tag") or lock.get("tree_sha256") != pin.get("tree_sha256")):
        problems.append(f"data is {pin.get('tag')}, the lock in git says {lock['tag']}: refetch")
    hashes = pin.get("hashes", {})
    if tree_digest(hashes) != pin.get("tree_sha256"):
        problems.append(f"data/{PIN_NAME} is internally inconsistent")
    for rel, want in hashes.items():
        p = root / rel
        if not p.exists():
            problems.append(f"missing {rel}")
        elif sha256_file(p) != want:
            problems.append(f"changed {rel}")
        if len(problems) > 20:
            problems.append("...")
            break
    return not problems, problems


def stamp(root: Path = ROOT) -> str:
    """One short label for scorecard output: which data this run graded."""
    try:
        ok, problems = verify(root)
    except (ValueError, json.JSONDecodeError, OSError) as e:
        return f"unpinned ({e})"
    if ok:
        return json.loads((root / "data" / PIN_NAME).read_text())["tag"]
    return f"UNPINNED: {problems[0]}" + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")


def use_shipped_switch_costs(cfg) -> dict:
    """[switch costs] Scorecards price switches from the one source of truth (default_config.yaml,
    contracts/switching.ConfigSwitchPaths), not from whatever the pinned eval_config.yaml snapshot
    carried. Returns the costs now in cfg."""
    from redzone.config import Config
    from redzone.contracts.switching import ConfigSwitchPaths
    shipped = Config.__new__(Config)
    shipped.data = cfg.defaults
    costs = ConfigSwitchPaths(shipped).costs()
    cfg.data.setdefault("decider", {})["switch_cost"] = dict(costs)
    return costs


def flatten_league_overrides(cfg, league: str) -> dict:
    """[per-league promotion, grader v7] Apply `league_overrides[<league>]` (dotted key -> value) onto the run's
    config, so a single-league scorecard grades exactly what the app runs for that league. Applied AFTER --set, so a
    --set of league_overrides itself is honored. Returns the flattened keys (for the report). Serves: release gate."""
    over = ((cfg.data.get("league_overrides") or {}).get(league) or {})
    for dotted, value in over.items():
        node = cfg.data
        *path, last = str(dotted).split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[last] = value
    return dict(over)


def config_behind_live(cfg, league: str) -> float | None:
    """[grader v8] The time-shift buffer the app would run for `league` from its own config
    (timing.behind_live_s: enabled, by_league, default), or None when it's off. Scorecards use it when no
    --behind-live is given, so a tree whose defaults turn the buffer on is graded with it. Serves: release gate."""
    tb = ((cfg.data.get("timing") or {}).get("behind_live_s") or {})
    if not tb.get("enabled"):
        return None
    by = tb.get("by_league") or {}
    v = by.get(league, tb.get("default"))
    return float(v) if v is not None else None
