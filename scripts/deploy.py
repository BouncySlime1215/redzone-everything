"""Safe deploy for the Mac: test a new commit off to the side, switch only when no game is live,
watch health for 10 minutes, and roll code + config + models back on its own if things got worse.

    .venv/bin/python scripts/deploy.py status            # what is running, last good, snapshots
    .venv/bin/python scripts/deploy.py deploy --dry-run  # fetch + test + live check; changes nothing
    .venv/bin/python scripts/deploy.py deploy            # the real thing (exit codes below)
    .venv/bin/python scripts/deploy.py rollback          # back to the last good commit + its config/models
    .venv/bin/python scripts/deploy.py snapshot          # save config/models now (no code change)
    .venv/bin/python scripts/deploy.py restore <id>      # put back one saved config/models snapshot

Deploy steps, in order. Nothing on the running checkout changes before step 5.
 1. Lock (one deploy at a time) and refuse if a previous deploy never finished its health watch.
 2. `git fetch origin integration/release`; nothing to do if HEAD is already there.
 3. Refuse if tracked files are edited, or requirements.txt changed (install deps by hand first).
    Untracked files (data/, agent/model/, .venv-agent/, local docs) never block a deploy.
 4. Run the test suite on the new commit in a separate worktree (data/deploy/stage), at background
    priority (`nice -n 10`; `test_priority: background` uses `taskpolicy -b` on the Mac, which is about
    2x slower: it timed out at 60 min where nice 10 took 30 min, Nov 5). Fail -> stop, nothing touched.
 5. Wait until no game is live and none starts inside the watch window (or give up, exit 2).
 6. Save config + models (content-addressed, deduplicated), check out the new commit (detached),
    restart the launchd service, then the watchdog so it runs the new code too. A local untracked
    file at a path the new commit tracks is removed if identical, else moved to
    data/deploy/untracked/<commit>/ (and put back on a rollback).
 7. Watch health for 10 minutes against the old version's last 10 minutes: API up, no crash loop,
    no burst of new errors or switch failures, feeds and sticks no worse. The watchdog leaves
    restarts to this script while the deploy marker is present.
 8. Healthy -> record the commit as last good. Worse -> check out the last good commit, restore its
    config/models snapshot, restart, and verify that the rollback itself came back up.

Exit codes: 0 deployed (or already current), 1 error/refused, 2 deferred (game live),
            3 tests failed (nothing changed), 4 rolled back, 5 rollback FAILED (needs a person).

Every step goes to data/deploy/deploy.jsonl; outcomes also go to the phone (data/agent.jsonl and
ntfy, the same channel the watchdog uses).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVICE_LABEL = "com.redzone.everything"
AGENT_LABEL = "com.redzone.agent"

EXIT_OK, EXIT_ERROR, EXIT_DEFERRED, EXIT_TESTS, EXIT_ROLLED_BACK, EXIT_ROLLBACK_FAILED = 0, 1, 2, 3, 4, 5

# Same log line shape as the app's RotatingFileHandler and agent/watchdog.py.
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (ERROR|WARNING|CRITICAL) (.*)$")
_SWITCH = re.compile(r"flip|tune|switch|re-load|press-through|resume check", re.I)


@dataclass
class Settings:
    branch: str = "integration/release"
    remote: str = "origin"
    api: str = "http://127.0.0.1:8778"
    watch_s: float = 600.0            # how long the new version must stay healthy
    sample_s: float = 30.0            # health sample period
    startup_grace_s: float = 120.0    # the API must answer within this after the restart
    baseline_s: float = 600.0         # old version's log window to compare against
    test_timeout_s: float = 3600.0   # nice 10 on the Mac: ~30 min (Nov 5); --test-timeout MIN overrides
    test_priority: str = "nice"       # "nice": nice -n 10; "background": taskpolicy -b (efficiency cores, ~2x slower)
    test_args: list[str] = field(default_factory=lambda: ["-q", "-x", "-p", "no:cacheprovider"])
    deselect: list[str] = field(default_factory=list)   # known Mac/hardware-only tests to skip
    live_wait_s: float = 0.0          # 0: defer at once if a game is live; else poll this long
    live_poll_s: float = 300.0
    at_break: bool = False            # --at-break: allow live games while the one on screen is at halftime
    soon_s: float = 900.0             # a game starting this soon counts as live
    error_ratio: float = 2.0          # watch errors > baseline*ratio + slack -> worse
    error_slack: int = 5
    new_signature_min: int = 3        # a brand-new error seen this often -> worse
    switch_fail_slack: int = 2        # switch failures > baseline + slack -> worse
    api_fail_streak: int = 3          # consecutive API misses after startup -> worse
    api_fail_ratio: float = 0.2
    min_free_mb: int = 1500           # disk guard: the Mac has ~5 GB free
    keep_releases: int = 5
    managed: list[str] = field(default_factory=lambda: ["data/config.yaml", "agent/model"])
    settle_s: float = 60.0            # errors in the first minute after startup aren't judged
    ignore: list[str] = field(default_factory=list)     # regexes for known, harmless log lines

    @classmethod
    def load(cls, path: Path) -> "Settings":
        s = cls()
        if not path.exists():
            return s
        import yaml
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: must be a mapping")
        for k, v in raw.items():
            if not hasattr(s, k):
                raise ValueError(f"{path}: unknown setting {k!r}")
            cur = getattr(s, k)
            numeric_ok = isinstance(cur, float) and isinstance(v, int)
            if isinstance(v, bool) != isinstance(cur, bool) or not (isinstance(v, type(cur)) or numeric_ok):
                raise ValueError(f"{path}: {k} should be {type(cur).__name__}, got {v!r}")
            setattr(s, k, type(cur)(v) if isinstance(cur, float) else v)
        if s.watch_s < s.sample_s or s.sample_s <= 0:
            raise ValueError(f"{path}: watch_s must be >= sample_s > 0")
        for pat in s.ignore:
            try:
                re.compile(pat)
            except re.error as e:
                raise ValueError(f"{path}: ignore pattern {pat!r}: {e}") from None
        for m in s.managed:
            p = Path(m)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"{path}: managed path {m!r} must be inside the checkout")
        return s


# ---- the outside world (faked in tests) -----------------------------------------------------------

class System:
    """Everything that touches git, launchd, HTTP, the clock or the disk free count."""

    def __init__(self, root: Path = ROOT):
        self.root = root

    def run(self, args: list[str], cwd: Path | None = None, timeout: float = 120.0,
            check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
        r = subprocess.run(args, cwd=cwd or self.root, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **env} if env else None)
        if check and r.returncode != 0:
            raise DeployError(f"{' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r

    def git(self, *args: str, cwd: Path | None = None, timeout: float = 300.0,
            check: bool = True) -> subprocess.CompletedProcess:
        return self.run(["git", *args], cwd=cwd, timeout=timeout, check=check)

    def low_priority(self, args: list[str], mode: str = "nice") -> list[str]:
        """The deploy's test command at low priority. `nice -n 10` by default: the live app (nice 0) still
        wins every core, but the suite is not pinned to the Mac's efficiency cores the way `taskpolicy -b`
        pins it (Nick, Nov 6: 30 min at nice 10, a 60 min timeout under taskpolicy -b)."""
        if mode == "background" and sys.platform == "darwin" and shutil.which("taskpolicy"):
            return ["taskpolicy", "-b", *args]
        return ["nice", "-n", "10", *args] if shutil.which("nice") else args

    def fetch_state(self, api: str, timeout: float = 6.0) -> dict | None:
        try:
            with urllib.request.urlopen(f"{api}/api/state", timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    def service_pid(self, label: str) -> int | None:
        try:
            r = self.run(["launchctl", "print", f"{self.domain()}/{label}"], timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"^\s*pid = (\d+)", r.stdout, re.M)
        return int(m.group(1)) if m else None

    def restart(self, label: str) -> bool:
        try:
            r = self.run(["launchctl", "kickstart", "-k", f"{self.domain()}/{label}"], timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0

    def free_mb(self, path: Path) -> float:
        return shutil.disk_usage(path).free / 1e6

    def now(self) -> float:
        return time.time()

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def push(self, text: str) -> None:
        """Phone: the watchdog's journal (shown in the app) and its ntfy topic, if one is set."""
        try:
            with (self.root / "data/agent.jsonl").open("a") as f:
                f.write(json.dumps({"ts": round(self.now(), 1), "kind": "deploy", "action": "notify",
                                    "source": "deploy", "why": text, "result": "noted for the phone"}) + "\n")
        except OSError:
            pass
        topic = os.environ.get("REDZONE_NTFY_TOPIC") or _ntfy_topic(self.root)
        if topic:
            try:
                req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode()[:1000],
                                             method="POST", headers={"Title": "RedZone deploy"})
                urllib.request.urlopen(req, timeout=8).close()
            except Exception:
                pass


def _ntfy_topic(root: Path) -> str:
    try:
        import yaml
        cfg = yaml.safe_load((root / "data/config.yaml").read_text()) or {}
        return str(((cfg.get("supervisor") or {}).get("ntfy_topic")) or "")
    except Exception:
        return ""


class DeployError(Exception):
    pass


# ---- config + model snapshots -----------------------------------------------------------------------

class Snapshots:
    """Content-addressed copies of the files git doesn't version (saved settings, LoRA adapters).

    data/deploy/store/<sha256>       one copy per distinct file, shared by every snapshot
    data/deploy/releases/<id>.json   {id, commit, created, managed, files: {relpath: sha256}}

    A restore puts every managed path back exactly: files are rewritten atomically, and files
    under a managed path that the snapshot didn't have are removed (a model dir gets no strays).
    """

    def __init__(self, root: Path, managed: list[str], keep: int = 5):
        self.root, self.managed, self.keep = root, managed, keep
        self.base = root / "data/deploy"
        self.store, self.releases = self.base / "store", self.base / "releases"

    def _files(self, rel: str) -> list[Path]:
        p = self.root / rel
        if p.is_symlink():
            raise DeployError(f"{rel} is a symlink; refusing to snapshot through it")
        if p.is_file():
            return [p]
        if p.is_dir():
            return sorted(f for f in p.rglob("*") if f.is_file() and not f.is_symlink())
        return []

    def size_mb(self) -> float:
        return sum(f.stat().st_size for rel in self.managed for f in self._files(rel)) / 1e6

    def save(self, commit: str, now: float, label: str = "") -> str:
        self.store.mkdir(parents=True, exist_ok=True)
        self.releases.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}
        for rel in self.managed:
            for f in self._files(rel):
                h = hashlib.sha256()
                with f.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                digest = h.hexdigest()
                blob = self.store / digest
                if not blob.exists():
                    tmp = blob.with_suffix(".tmp")
                    shutil.copyfile(f, tmp)
                    os.replace(tmp, blob)
                files[f.relative_to(self.root).as_posix()] = digest
        # seq: a per-store counter, so order never depends on wall-clock seconds (two snapshots in the
        # same second used to sort by directory order, and prune could drop the newer one).
        seq = max((r.get("seq", 0) for r in self.list()), default=0) + 1
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
        rid = f"{stamp}-{seq:04d}-{commit[:9]}" + (f"-{label}" if label else "")
        n = 1
        while (self.releases / f"{rid}.json").exists():   # a hand-copied release folder: still unique
            n += 1
            rid = f"{stamp}-{seq:04d}-{commit[:9]}-{label or 'v'}{n}"
        _atomic_json(self.releases / f"{rid}.json",
                     {"id": rid, "seq": seq, "commit": commit, "created": now, "managed": list(self.managed),
                      "files": files})
        return rid

    def get(self, rid: str) -> dict:
        if not re.fullmatch(r"[\w.-]+", rid):
            raise DeployError(f"bad snapshot id {rid!r}")
        p = self.releases / f"{rid}.json"
        if not p.exists():
            raise DeployError(f"no snapshot {rid}")
        return json.loads(p.read_text())

    def list(self) -> list[dict]:
        if not self.releases.exists():
            return []
        rows = [json.loads(p.read_text()) for p in self.releases.glob("*.json")]
        # Oldest first, by seq (snapshots from before seq existed sort first, by time, then id).
        return sorted(rows, key=lambda r: (r.get("seq", 0), r["created"], r["id"]))

    def restore(self, rid: str) -> list[str]:
        snap = self.get(rid)
        missing = [d for d in snap["files"].values() if not (self.store / d).exists()]
        if missing:   # check everything first: never leave config half-restored
            raise DeployError(f"snapshot {rid} is missing {len(missing)} stored file(s)")
        changed = []
        wanted = {rel for rel in snap["files"]}
        for rel in snap["managed"]:
            for f in self._files(rel):
                r = f.relative_to(self.root).as_posix()
                if r not in wanted:
                    f.unlink()
                    changed.append(f"-{r}")
        for rel, digest in snap["files"].items():
            dst = self.root / rel
            if dst.is_file() and _sha256(dst) == digest:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f".{dst.name}.restore")
            shutil.copyfile(self.store / digest, tmp)
            os.replace(tmp, dst)
            changed.append(rel)
        return changed

    def prune(self, protect: set[str]) -> int:
        """Keep the newest `keep` snapshots plus any protected ones; drop blobs nobody uses."""
        rows = self.list()
        drop = [r for r in rows[:-self.keep] if r["id"] not in protect] if self.keep else []
        for r in drop:
            (self.releases / f"{r['id']}.json").unlink(missing_ok=True)
        used = {d for r in self.list() for d in r["files"].values()}
        freed = 0
        if self.store.exists():
            for blob in self.store.iterdir():
                if blob.name not in used:
                    freed += blob.stat().st_size
                    blob.unlink()
        return freed


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


# ---- health ---------------------------------------------------------------------------------------

def log_errors(log: Path, start: float, end: float, max_bytes: int = 4_000_000) -> Counter:
    """Error/warning signatures logged in (start, end]; traceback bodies skipped. Reads rotations
    too, since a 10-minute window can straddle a 5 MiB rollover."""
    c: Counter = Counter()
    for p in [log, *(log.with_name(f"{log.name}.{i}") for i in (1, 2, 3))]:
        try:
            if p != log and p.stat().st_mtime < start:
                break   # rotated before the window opened (and so is every older one)
            with p.open("rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - max_bytes))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _LINE.match(line)
            if not m:
                continue
            try:
                t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                continue
            if not (start < t <= end):
                continue
            body = m.group(3)
            if body.split(": ", 1)[-1].startswith((" ", "File ", "^", "Traceback")):
                continue
            c[_signature(f"{m.group(2)} {body}")] += 1
    return c


def _signature(msg: str) -> str:
    msg = re.sub(r"\b\d+(\.\d+)*\b", "N", msg)
    msg = re.sub(r"0x[0-9a-f]+", "X", msg, flags=re.I)
    return msg[:160]


def switch_failures(errors: Counter) -> int:
    return sum(n for sig, n in errors.items() if _SWITCH.search(sig))


FRESH_S = 120.0   # a source that answered this recently is reachable (engine._source_health uses the same 120 s)


def source_failing(s: dict) -> bool:
    """Is this /api/state source really failing? A BENCHED source (merge.py: its copy regressed against a
    faster feed for some game) still answers, so it isn't. The old engine (RC6.1 and before) reported a
    bench as ok:false; the new one keeps ok = reachable + fresh and reports benched on its own. Both read
    the same here, so a deploy compares like with like across versions (RC6.1 rolled back twice, Nov 4)."""
    if s.get("ok"):
        return False
    age = s.get("age_s")
    return not (s.get("benched") and isinstance(age, (int, float)) and age < FRESH_S)


@dataclass
class Health:
    api_ok: bool
    sources_bad: int = 0
    sticks_offline: int = 0

    @classmethod
    def of(cls, st: dict | None) -> "Health":
        if st is None:
            return cls(api_ok=False)
        h = st.get("health") or {}
        return cls(api_ok=True,
                   sources_bad=sum(1 for s in h.get("sources") or [] if source_failing(s)),
                   sticks_offline=sum(1 for s in h.get("sticks") or [] if s.get("state") == "offline"))


# A long break: halftime, or an NHL intermission. Timeouts and short quarter breaks don't count
# (the ~1-2 min restart must land inside the break).
LONG_BREAK = re.compile(r"halftime|intermission|^end of (the )?(1st|2nd)( period)?$", re.I)


def long_break(g: dict) -> bool:
    """Is this live game in a long break, by its status line ("Halftime", "End of 2nd", ...)?"""
    text = str(g.get("status_text") or "").strip()
    if not LONG_BREAK.search(text):
        return False
    return "end of" not in text.lower() or g.get("league") == "nhl"


def busy(st: dict | None, now: float, soon_s: float, at_break: bool = False) -> str:
    """Why a restart now would hurt, or "" if it wouldn't. A down API means nothing is on screen
    under our control, so it doesn't block (that is when a fix is most wanted).

    at_break (Nick, Nov 4 6:50 PM: RC6 at halftime): live games are allowed while the game on screen
    is in a long break (halftime / intermission); games starting soon don't block. Off screen, a live
    game loses only feed polling for the ~1-2 min restart."""
    if st is None:
        return ""
    if at_break:
        live = [g for g in st.get("games") or [] if g.get("phase") == "live"]
        on = [g for g in live if g.get("on_now")]
        if live and not on:
            return "games are live and none is on screen; can't tell the TV is in a break"
        for g in on:
            if not long_break(g):
                name = g.get("matchup") or g.get("id") or "a game"
                return f"{name} is on screen and not in halftime/intermission ({g.get('status_text') or '?'})"
        return ""
    for g in st.get("games") or []:
        name = g.get("matchup") or g.get("id") or "a game"
        if g.get("phase") == "live":
            return f"{name} is live"
        t = g.get("start_time")
        if g.get("phase") == "pre" and isinstance(t, (int, float)) and now <= t <= now + soon_s:
            return f"{name} starts in {int((t - now) / 60)} min"
    return ""


@dataclass
class Verdict:
    worse: bool
    reasons: list[str]
    summary: dict


def judge(s: Settings, before: Health, base_err: Counter, samples: list[Health], errors: Counter,
          pid_changes: int, came_up: bool) -> Verdict:
    """Is the new version worse than the old one? Each reason is one plain sentence."""
    r: list[str] = []
    after = [h for h in samples if h.api_ok]
    misses = len(samples) - len(after)
    streak = best = 0
    for h in samples:
        streak = 0 if h.api_ok else streak + 1
        best = max(best, streak)
    base_n, new_n = sum(base_err.values()), sum(errors.values())
    new_sigs = {k: n for k, n in errors.items() if k not in base_err and n >= s.new_signature_min}
    base_sw, new_sw = switch_failures(base_err), switch_failures(errors)
    if not came_up:
        r.append(f"the app didn't answer within {int(s.startup_grace_s)} s of the restart")
    if pid_changes:
        r.append(f"the app crashed and launchd restarted it {pid_changes} time(s)")
    if best >= s.api_fail_streak:
        r.append(f"the API stopped answering ({best} checks in a row)")
    elif samples and misses / len(samples) > s.api_fail_ratio:
        r.append(f"the API missed {misses} of {len(samples)} checks")
    if new_n > base_n * s.error_ratio + s.error_slack:
        r.append(f"{new_n} errors/warnings in the watch vs {base_n} before")
    if new_sigs:
        top = max(new_sigs, key=new_sigs.get)
        r.append(f"{len(new_sigs)} new kind(s) of error, e.g. {top[:100]!r} x{new_sigs[top]}")
    if new_sw > base_sw + s.switch_fail_slack:
        r.append(f"{new_sw} switch failures vs {base_sw} before")
    tail = after[-3:]
    if before.api_ok and len(tail) == 3:   # a feed or stick down at the end that was fine before
        if min(h.sources_bad for h in tail) > before.sources_bad:
            r.append(f"{min(h.sources_bad for h in tail)} sports feed(s) failing vs {before.sources_bad} before")
        if min(h.sticks_offline for h in tail) > before.sticks_offline:
            r.append(f"{min(h.sticks_offline for h in tail)} stick(s) offline vs {before.sticks_offline} before")
    return Verdict(bool(r), r, {"samples": len(samples), "api_misses": misses, "errors_before": base_n,
                                "errors_after": new_n, "switch_fail_before": base_sw,
                                "switch_fail_after": new_sw, "pid_changes": pid_changes})


# ---- the deployer ---------------------------------------------------------------------------------

class Deployer:
    def __init__(self, settings: Settings, system: System, root: Path = ROOT, out=print):
        self.s, self.sys, self.root, self.out = settings, system, root, out
        self.dir = root / "data/deploy"
        self.state_path = self.dir / "state.json"
        self.marker = self.dir / "in_progress.json"
        self.snaps = Snapshots(root, settings.managed, settings.keep_releases)

    # state ---------------------------------------------------------------------------------------
    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, st: dict) -> None:
        _atomic_json(self.state_path, st)

    def journal(self, step: str, **kw) -> None:
        row = {"ts": round(self.sys.now(), 1), "step": step, **kw}
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "deploy.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        self.out(f"[deploy] {step}" + (f": {kw.get('msg')}" if kw.get("msg") else ""))

    @contextlib.contextmanager
    def lock(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        fh = (self.dir / "deploy.lock").open("w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise DeployError("another deploy is running")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def _errors(self, start: float, end: float) -> Counter:
        if end <= start:
            return Counter()
        c = log_errors(self.root / "data/service.log", start, end)
        pats = [re.compile(p) for p in self.s.ignore]
        return Counter({k: n for k, n in c.items() if not any(p.search(k) for p in pats)})

    def _protected(self, st: dict) -> set[str]:
        return {x["snapshot"] for x in (st.get("good"), st.get("previous")) if x and x.get("snapshot")}

    def head(self) -> str:
        return self.sys.git("rev-parse", "HEAD").stdout.strip()

    def dirty(self) -> list[str]:
        out = self.sys.git("status", "--porcelain", "--untracked-files=no").stdout
        return [ln[3:] for ln in out.splitlines() if ln.strip()]

    # steps ---------------------------------------------------------------------------------------
    def fetch(self) -> str:
        self.sys.git("fetch", self.s.remote, self.s.branch, timeout=600)
        return self.sys.git("rev-parse", f"{self.s.remote}/{self.s.branch}^{{commit}}").stdout.strip()

    def run_tests(self, commit: str) -> tuple[bool, str]:
        stage = self.dir / "stage"
        self.sys.git("worktree", "prune", check=False)
        if stage.exists():
            self.sys.git("worktree", "remove", "--force", str(stage), check=False)
            shutil.rmtree(stage, ignore_errors=True)
        self.sys.git("worktree", "add", "--detach", str(stage), commit, timeout=600)
        try:
            py = self.root / ".venv/bin/python"
            cmd = [str(py) if py.exists() else sys.executable, "-m", "pytest", *self.s.test_args]
            for d in self.s.deselect:
                cmd += ["--deselect", d]
            self.journal("tests", msg=f"running on {commit[:9]} at low priority ({self.s.test_priority})")
            try:
                # Background priority (efficiency cores) makes wall-clock speed budgets meaningless,
                # so tests skip their speed asserts (tests/conftest.py assert_speed) but keep the rest.
                r = self.sys.run(self.sys.low_priority(cmd, self.s.test_priority), cwd=stage, timeout=self.s.test_timeout_s, check=False,
                                 env={"RZ_TESTS_LOW_PRIORITY": "1"})
            except subprocess.TimeoutExpired:
                return False, (f"tests timed out after {int(self.s.test_timeout_s)} s at {self.s.test_priority} "
                               "priority; rerun with --test-timeout MIN (nothing was touched)")
            tail = "\n".join((r.stdout or r.stderr).strip().splitlines()[-15:])
            return r.returncode == 0, tail
        finally:
            self.sys.git("worktree", "remove", "--force", str(stage), check=False)
            shutil.rmtree(stage, ignore_errors=True)

    def wait_idle(self) -> str:
        """"" when it's safe to restart; otherwise why not (after waiting up to live_wait_s)."""
        deadline = self.sys.now() + self.s.live_wait_s
        while True:
            why = busy(self.sys.fetch_state(self.s.api), self.sys.now(), max(self.s.soon_s, self.s.watch_s),
                       self.s.at_break)
            if not why or self.sys.now() >= deadline:
                return why
            self.journal("waiting", msg=why)
            self.sys.sleep(min(self.s.live_poll_s, max(1.0, deadline - self.sys.now())))

    # Untracked files in the way --------------------------------------------------------------------
    # The Mac's checkout carries local, untracked files (data/, agent/model/, .venv-agent/, docs
    # copied in by hand). Most are never touched. But git refuses to check out a commit that tracks
    # a path the checkout has as an untracked file, even one with the same bytes. So before a
    # switch: an identical copy is removed (the checkout puts the same bytes back); a different one
    # is moved to data/deploy/untracked/<commit>/ and put back if we roll away from that commit.

    def _aside_dir(self, commit: str) -> Path:
        return self.dir / "untracked" / commit[:12]

    def clear_way(self, commit: str) -> list[str]:
        """Make room for `commit`'s files; returns the paths moved aside (different local copies)."""
        incoming = self.sys.git("diff", "--name-only", "--no-renames", "--diff-filter=A", "-z",
                                "HEAD", commit).stdout.split("\0")
        moved: list[str] = []
        for rel in filter(None, incoming):
            p = self.root / rel
            if not (p.is_file() or p.is_symlink()):
                continue
            ours = self.sys.git("hash-object", "--", rel).stdout.strip() if p.is_file() else ""
            theirs = self.sys.git("rev-parse", f"{commit}:{rel}").stdout.strip()
            if ours and ours == theirs:
                p.unlink()
                continue
            dst = self._aside_dir(commit) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dst))
            moved.append(rel)
        if moved:
            mf = self._aside_dir(commit) / "moved.json"
            _atomic_json(mf, sorted(set(moved) | set(json.loads(mf.read_text()) if mf.exists() else [])))
            self.journal("moved_aside", msg=f"{len(moved)} local untracked file(s) differ from {commit[:9]}; "
                                            f"kept in {self._aside_dir(commit).relative_to(self.root)}",
                         files=moved[:50])
        return moved

    def put_back(self, leaving: str) -> list[str]:
        """After rolling away from `leaving`, put back the local files moved aside for it."""
        d = self._aside_dir(leaving)
        mf = d / "moved.json"
        if not mf.exists():
            return []
        back = []
        for rel in json.loads(mf.read_text()):
            src, dst = d / rel, self.root / rel
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                back.append(rel)
        shutil.rmtree(d, ignore_errors=True)
        if back:
            self.journal("put_back", msg=f"{len(back)} local untracked file(s) restored", files=back[:50])
        return back

    def put_back_all(self, but: str) -> None:
        """Rolling back (also after a checkout that failed half-way): every local file moved aside
        for another commit goes back, unless the commit we're on now tracks that path."""
        root = self.dir / "untracked"
        for d in sorted(root.iterdir()) if root.is_dir() else ():
            if d.is_dir() and not but.startswith(d.name):
                self.put_back(d.name)

    def switch_to(self, commit: str) -> bool:
        self.clear_way(commit)
        self.sys.git("checkout", "--quiet", "--detach", commit)
        ok = self.sys.restart(SERVICE_LABEL)
        self.sys.restart(AGENT_LABEL)   # the watchdog should run the same code; fine if not installed
        return ok

    def watch(self, before: Health, base_err: Counter) -> Verdict:
        s = self.s
        t0 = self.sys.now()
        came_up = False
        while self.sys.now() - t0 < s.startup_grace_s:
            if self.sys.fetch_state(s.api) is not None:
                came_up = True
                break
            self.sys.sleep(min(5.0, s.sample_s))
        if not came_up:
            return judge(s, before, base_err, [], Counter(), 0, came_up=False)
        pid0 = self.sys.service_pid(SERVICE_LABEL)
        start = self.sys.now()
        # Every restart logs its own reconnects and warm-up warnings that the long-running old
        # version doesn't; a crash in that minute still shows up as API misses / pid changes.
        judged_from = start + s.settle_s
        samples: list[Health] = []
        pid_changes, last_pid = 0, pid0
        while True:
            self.sys.sleep(s.sample_s)
            samples.append(Health.of(self.sys.fetch_state(s.api)))
            pid = self.sys.service_pid(SERVICE_LABEL)
            if pid is not None and last_pid is not None and pid != last_pid:
                pid_changes += 1
            last_pid = pid if pid is not None else last_pid
            errors = self._errors(judged_from, self.sys.now())
            v = judge(s, before, base_err, samples, errors, pid_changes, came_up=True)
            dead = len(samples) >= s.api_fail_streak and not any(h.api_ok for h in samples[-s.api_fail_streak:])
            hard = pid_changes >= 2 or dead
            if hard:   # a crash loop or a dead API: don't make the user wait 10 minutes for the fix
                return v
            if self.sys.now() - start >= s.watch_s:
                return v

    def rollback_to(self, commit: str, snapshot: str | None, why: str) -> bool:
        """Put the last good code + config/models back and check the app came up again."""
        self.journal("rollback", msg=f"to {commit[:9]}: {why}", commit=commit, snapshot=snapshot)
        try:
            self.sys.git("checkout", "--quiet", "--force", "--detach", commit)
            self.put_back_all(but=commit)
            if snapshot:
                changed = self.snaps.restore(snapshot)
                self.journal("restored", msg=f"{len(changed)} config/model file(s)", files=changed[:50])
        except DeployError as e:
            self.journal("rollback_failed", msg=str(e))
            return False
        self.sys.restart(SERVICE_LABEL)
        self.sys.restart(AGENT_LABEL)
        t0 = self.sys.now()
        while self.sys.now() - t0 < self.s.startup_grace_s:
            if self.sys.fetch_state(self.s.api) is not None:
                return True
            self.sys.sleep(5.0)
        self.journal("rollback_failed", msg="app didn't answer after rolling back")
        return False

    # commands ------------------------------------------------------------------------------------
    def deploy(self, dry_run: bool = False, skip_tests: bool = False, deps_ok: bool = False) -> int:
        with self.lock():
            st = self.state()
            if st.get("pending"):
                p = st["pending"]
                raise DeployError(f"the deploy of {p['commit'][:9]} never finished its health watch; "
                                  "run `deploy.py rollback` or `deploy.py confirm` first")
            free = self.sys.free_mb(self.root)
            if free < self.s.min_free_mb:
                raise DeployError(f"only {int(free)} MB free (need {self.s.min_free_mb}); free space first")
            prev = self.head()
            target = self.fetch()
            if target == prev:
                self.journal("current", msg=f"already on {prev[:9]}")
                return EXIT_OK
            if self.dirty():
                raise DeployError(f"tracked files are edited here: {', '.join(self.dirty()[:5])}; "
                                  "commit or stash them first")
            if not deps_ok and self.sys.git("diff", "--name-only", prev, target, "--",
                                            "requirements.txt").stdout.strip():
                raise DeployError("requirements.txt changed: install the new packages into .venv by hand, "
                                  "then deploy again with --deps-ok")
            if not skip_tests:
                ok, tail = self.run_tests(target)
                self.journal("tests", msg="passed" if ok else "FAILED", tail=tail)
                if not ok:
                    self.sys.push(f"Deploy of {target[:9]} stopped: tests failed. Nothing changed.")
                    self.out(tail)
                    return EXIT_TESTS
            why = self.wait_idle()
            if why:
                self.journal("deferred", msg=f"{why}; try again after the games")
                return EXIT_DEFERRED
            if dry_run:
                self.journal("dry_run", msg=f"would switch {prev[:9]} -> {target[:9]} and watch "
                                            f"{int(self.s.watch_s / 60)} min")
                return EXIT_OK
            return self._switch_and_watch(st, prev, target)

    def _switch_and_watch(self, st: dict, prev: str, target: str) -> int:
        now = self.sys.now()
        snap_mb = self.snaps.size_mb()
        if self.sys.free_mb(self.root) - snap_mb < self.s.min_free_mb / 2:
            raise DeployError(f"not enough disk to snapshot {snap_mb:.0f} MB of config/models")
        # Whatever runs now is the last good version (it was confirmed by the previous deploy, or
        # it's what the user has been running). Its config/models as of right now go with it, so a
        # rollback keeps settings the user changed since the last deploy.
        good = {"commit": prev, "snapshot": self.snaps.save(prev, now, "pre"), "at": now}
        before = Health.of(self.sys.fetch_state(self.s.api))
        base_err = self._errors(now - self.s.baseline_s, now)
        st.update(good=good, pending={"commit": target, "from": prev, "at": now})
        self._save_state(st)
        _atomic_json(self.marker, {"commit": target, "until": now + self.s.watch_s + self.s.startup_grace_s + 300})
        try:
            self.journal("switch", msg=f"{prev[:9]} -> {target[:9]}", commit=target)
            try:
                started = self.switch_to(target)
                v = self.watch(before, base_err) if started else \
                    Verdict(True, ["launchctl couldn't restart the app"], {})
            except Exception as e:   # a failed checkout, a crash in here: never leave it half-done
                v = Verdict(True, [f"deploy step failed: {type(e).__name__}: {str(e)[:200]}"], {})
            self.journal("verdict", msg="worse: " + "; ".join(v.reasons) if v.worse else "healthy",
                         reasons=v.reasons, **v.summary)
            st = self.state()
            st.pop("pending", None)
            hist = st.setdefault("history", [])
            if not v.worse:
                st["previous"] = good
                st["good"] = {"commit": target, "snapshot": self.snaps.save(target, self.sys.now(), "good"),
                              "at": self.sys.now()}
                hist.append({"commit": target, "from": prev, "result": "deployed", "at": now})
                st["history"] = hist[-50:]
                self._save_state(st)
                self.snaps.prune(self._protected(st))
                self.sys.push(f"Deployed {target[:9]}: healthy for {int(self.s.watch_s / 60)} min.")
                return EXIT_OK
            ok = self.rollback_to(good["commit"], good["snapshot"], "; ".join(v.reasons))
            hist.append({"commit": target, "from": prev, "result": "rolled_back" if ok else "rollback_failed",
                         "reasons": v.reasons, "at": now})
            st["history"] = hist[-50:]
            st["good"] = good
            if not ok:   # leave it flagged so the next deploy refuses until a person looks
                st["pending"] = {"commit": target, "from": prev, "at": now, "rollback_failed": True}
            self._save_state(st)
            self.snaps.prune(self._protected(st))
            if ok:
                self.sys.push(f"Deploy of {target[:9]} rolled back to {prev[:9]}: {'; '.join(v.reasons)}")
                return EXIT_ROLLED_BACK
            self.sys.push(f"ROLLBACK FAILED after deploying {target[:9]}. The app may be down. "
                          f"Run scripts/deploy.py status on the Mac.")
            return EXIT_ROLLBACK_FAILED
        finally:
            self.marker.unlink(missing_ok=True)

    def rollback(self, to: str | None = None) -> int:
        with self.lock():
            st = self.state()
            good = st.get("good") or {}
            if good.get("commit") == self.head() and not st.get("pending"):
                good = st.get("previous") or {}   # already on the last good: "undo the last deploy"
            commit, snap = (to, None) if to else (good.get("commit"), good.get("snapshot"))
            if not commit:
                raise DeployError("no last good commit recorded yet; pass --to <commit>")
            commit = self.sys.git("rev-parse", f"{commit}^{{commit}}").stdout.strip()
            if self.dirty():
                raise DeployError(f"tracked files are edited here: {', '.join(self.dirty()[:5])}; "
                                  "a rollback would overwrite them. Commit or stash them first")
            ok = self.rollback_to(commit, snap, "asked by hand")
            if ok:
                st.pop("pending", None)
                if st.get("good", {}).get("commit") != commit:
                    st["good"] = {"commit": commit, "snapshot": snap, "at": self.sys.now()}
                    st.pop("previous", None)
            st.setdefault("history", []).append({"commit": commit, "result": "manual_rollback" if ok else
                                                  "rollback_failed", "at": self.sys.now()})
            self._save_state(st)
            return EXIT_OK if ok else EXIT_ROLLBACK_FAILED

    def confirm(self) -> int:
        """Accept a deploy whose watch was interrupted (e.g. the Mac slept): mark HEAD good."""
        with self.lock():
            st = self.state()
            head = self.head()
            st.pop("pending", None)
            st["good"] = {"commit": head, "snapshot": self.snaps.save(head, self.sys.now(), "good"),
                          "at": self.sys.now()}
            self._save_state(st)
            self.journal("confirmed", msg=f"{head[:9]} marked good by hand")
            return EXIT_OK

    def status(self) -> int:
        st = self.state()
        self.out(f"running commit: {self.head()[:9]}")
        g = st.get("good") or {}
        self.out(f"last good:      {g.get('commit', '-')[:9]}  snapshot {g.get('snapshot', '-')}")
        if st.get("pending"):
            self.out(f"UNFINISHED deploy of {st['pending']['commit'][:9]} (run rollback or confirm)")
        for h in (st.get("history") or [])[-5:]:
            self.out(f"  {datetime.fromtimestamp(h['at']):%m-%d %H:%M} {h['commit'][:9]} {h['result']}"
                     + (f" ({'; '.join(h.get('reasons') or [])})" if h.get("reasons") else ""))
        snaps = self.snaps.list()
        self.out(f"snapshots: {len(snaps)}" + (f" (newest {snaps[-1]['id']})" if snaps else ""))
        return EXIT_OK


def deploy_in_progress(root: Path = ROOT, now: float | None = None) -> bool:
    """For the watchdog: a deploy is restarting/watching the app, so leave restarts to it."""
    try:
        m = json.loads((root / "data/deploy/in_progress.json").read_text())
        return float(m.get("until", 0)) > (now if now is not None else time.time())
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def main(argv: list[str] | None = None, system: System | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy")
    d.add_argument("--dry-run", action="store_true", help="fetch, test and check for live games; change nothing")
    d.add_argument("--branch", help="default from deploy/deploy.yaml, else integration/release")
    d.add_argument("--wait", type=float, metavar="MIN", help="wait up to MIN minutes for live games to end")
    d.add_argument("--at-break", action="store_true",
                   help="switch while games are live if the game on screen is at halftime/intermission")
    d.add_argument("--test-timeout", type=float, metavar="MIN", help="test step timeout in minutes (default 60)")
    d.add_argument("--deps-ok", action="store_true", help="requirements.txt changed and you installed them")
    d.add_argument("--skip-tests", action="store_true", help=argparse.SUPPRESS)
    r = sub.add_parser("rollback")
    r.add_argument("--to", help="a commit instead of the last good one (config/models left as they are)")
    sub.add_parser("confirm")
    sub.add_parser("status")
    sub.add_parser("snapshot")
    sub.add_parser("snapshots")
    rs = sub.add_parser("restore")
    rs.add_argument("id")
    a = ap.parse_args(argv)
    try:
        s = Settings.load(root / "deploy/deploy.yaml")
        if getattr(a, "branch", None):
            s.branch = a.branch
        if getattr(a, "wait", None) is not None:
            s.live_wait_s = max(0.0, a.wait * 60)
        if getattr(a, "test_timeout", None) is not None:
            s.test_timeout_s = max(60.0, a.test_timeout * 60)
        if getattr(a, "at_break", False):
            s.at_break = True
            s.live_poll_s = min(s.live_poll_s, 30.0)   # a halftime is short: look every 30 s
        dep = Deployer(s, system or System(root), root)
        if a.cmd == "deploy":
            return dep.deploy(dry_run=a.dry_run, skip_tests=a.skip_tests, deps_ok=a.deps_ok)
        if a.cmd == "rollback":
            return dep.rollback(a.to)
        if a.cmd == "confirm":
            return dep.confirm()
        if a.cmd == "status":
            return dep.status()
        if a.cmd == "snapshot":
            with dep.lock():
                print(dep.snaps.save(dep.head(), dep.sys.now(), "manual"))
            return EXIT_OK
        if a.cmd == "snapshots":
            for row in dep.snaps.list():
                print(f"{row['id']}  {len(row['files'])} file(s)")
            return EXIT_OK
        if a.cmd == "restore":
            with dep.lock():
                if deploy_in_progress(root):
                    raise DeployError("a deploy is watching the app; wait for it")
                for f in dep.snaps.restore(a.id):
                    print(f)
                print("restored; restart the app to load it (launchctl kickstart -k "
                      f"{dep.sys.domain()}/{SERVICE_LABEL})")
            return EXIT_OK
    except DeployError as e:
        print(f"deploy: {e}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
