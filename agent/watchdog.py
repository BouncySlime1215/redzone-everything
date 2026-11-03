"""RedZone watchdog: a separate, always-on process that keeps the app healthy without a person.

Every CHECK_S it looks at the app from outside (its API, its log, the disk, the sticks the app
reports), decides with agent/policy.py (hard rules first, the local model only for errors nobody
has written a rule for), and acts within a fixed, guarded set:

    none · notify (data/agent.jsonl, shown on the phone; ntfy push if a topic is set)
    restart_service (launchctl kickstart) · reconnect_stick (adb disconnect + connect)
    prune_disk (only caches the app rebuilds: catch-up clips, logo images)

It runs as its own launchd job (com.redzone.agent), so it still works when the app is hung or
crashed. Every observation, verdict and action goes to data/agent.jsonl.

    .venv-agent/bin/python -m agent.watchdog [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from agent.policy import SYSTEM, Obs, Verdict, guard, known, parse_answer, rules

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "service.log"
JOURNAL = DATA / "agent.jsonl"
API = os.environ.get("REDZONE_API", "http://127.0.0.1:8778")
LABEL = "com.redzone.everything"
CHECK_S = 60.0
WINDOW_S = 900.0
NOTIFY_QUIET_S = 3600.0      # the same note at most once an hour
RECONNECT_EVERY_S = 600.0
MODEL_CACHE_S = 1800.0       # the same set of unknown errors is asked about at most every 30 min
ADB = shutil.which("adb") or "/opt/homebrew/bin/adb"

log = logging.getLogger("redzone.agent")
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (ERROR|WARNING|CRITICAL) (.*)$")


def signature(msg: str) -> str:
    """One error, any instance: numbers, hosts and ids folded so repeats count together."""
    s = re.sub(r"\d+(\.\d+)?", "N", msg)
    return re.sub(r"\s+", " ", s).strip()[:200]


def recent_errors(path: Path = LOG, window_s: float = WINDOW_S, now: float | None = None,
                  max_bytes: int = 2_000_000) -> dict[str, int]:
    """Error/warning signatures in the log's last window_s seconds (traceback bodies skipped)."""
    now = now or time.time()
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return {}
    c: Counter[str] = Counter()
    for line in tail:
        m = _LINE.match(line)
        if not m:
            continue
        try:
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if now - t > window_s:
            continue
        body = m.group(3)
        if body.split(": ", 1)[-1].startswith((" ", "File ", "^", "Traceback")):
            continue
        c[signature(f"{m.group(2)} {body}")] += 1
    return dict(c)


def fetch_state(timeout: float = 6.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{API}/api/state", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


class Watchdog:
    def __init__(self, dry_run: bool = False, use_model: bool = True, clock=time.time):
        self.dry_run = dry_run
        self.use_model = use_model
        self._clock = clock
        self.fail_streak = 0
        self.restarts: list[float] = []
        self.offline_since: dict[str, float] = {}
        self.notified: dict[str, float] = {}
        self.reconnected: dict[str, float] = {}
        self.asked: dict[tuple, tuple[float, Verdict]] = {}
        self._model = None

    # ---- seeing ----------------------------------------------------------------------------
    def observe(self) -> Obs:
        now = self._clock()
        st = fetch_state()
        self.fail_streak = 0 if st is not None else self.fail_streak + 1
        o = Obs(api_ok=st is not None, api_fail_streak=self.fail_streak)
        try:
            o.log_age_s = round(now - LOG.stat().st_mtime, 1)
        except OSError:
            o.log_age_s = 0.0
        try:
            o.disk_free_mb = round(shutil.disk_usage(DATA).free / 1e6)
        except OSError:
            pass
        o.restarts_last_hour = sum(1 for t in self.restarts if now - t < 3600)
        o.errors = recent_errors(now=now)
        if st:
            games = st.get("games") or []
            o.live_games = sum(1 for g in games if g.get("phase") == "live")
            cur = st.get("now") or {}
            o.crunch_on_screen = bool(cur and cur.get("phase") == "live" and (cur.get("lock") or cur.get("mine")))
            sticks = ((st.get("health") or {}).get("sticks")) or []
            off = [s.get("host") for s in sticks if s.get("state") == "offline" and s.get("host")]
            for h in off:
                self.offline_since.setdefault(h, now)
            for h in list(self.offline_since):
                if h not in off:
                    del self.offline_since[h]
            o.sticks_offline = sorted(off, key=lambda h: self.offline_since[h])
            o.offline_min = round((now - min((self.offline_since[h] for h in off), default=now)) / 60, 1)
        return o

    # ---- deciding ----------------------------------------------------------------------------
    def model(self):
        if self._model is None:
            from agent.llm import ADAPTER, DEFAULT_MODEL, LocalModel
            self._model = LocalModel(DEFAULT_MODEL, ADAPTER)
        return self._model

    def decide(self, o: Obs) -> Verdict:
        v = rules(o)
        if v is None:
            unknown = tuple(sorted(s for s in o.errors if known(s) is None))
            hit = self.asked.get(unknown)
            if hit and self._clock() - hit[0] < MODEL_CACHE_S:
                v = hit[1]
            elif self.use_model:
                try:
                    ans = self.model().chat([{"role": "system", "content": SYSTEM},
                                             {"role": "user", "content": o.text()}])
                    v = parse_answer(ans) or Verdict("notify", f"new error the model couldn't judge: {unknown[0][:120]}",
                                                     source="guard")
                except Exception as e:   # no model, out of memory...: a person hears about the new error
                    log.warning("agent: model unavailable: %s", e)
                    v = Verdict("notify", f"new error: {unknown[0][:120]}", source="guard")
                self.asked[unknown] = (self._clock(), v)
            else:
                v = Verdict("notify", f"new error: {unknown[0][:120]}", source="guard")
        return guard(v, o)

    # ---- doing ----------------------------------------------------------------------------
    def act(self, v: Verdict, o: Obs) -> str:
        if v.action == "none":
            return "nothing to do"
        if v.action == "notify":
            key = re.sub(r"\d+", "N", v.why)
            if self._clock() - self.notified.get(key, -1e9) < NOTIFY_QUIET_S:
                return "already told you this hour"
            self.notified[key] = self._clock()
            self._push(v.why)
            return "noted for the phone"
        if self.dry_run:
            return f"dry run: would {v.action}"
        if v.action == "restart_service":
            if deploy_in_progress(self._clock()):
                return "deploy in progress: scripts/deploy.py is restarting and watching the app"
            self.restarts.append(self._clock())
            try:
                r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
                                   capture_output=True, text=True, timeout=30)
            except (subprocess.TimeoutExpired, OSError) as e:
                return f"restart failed: {e}"
            self._push(f"restarted the app: {v.why}")
            return "restarted" if r.returncode == 0 else f"restart failed: {r.stderr.strip()[:120]}"
        if v.action == "reconnect_stick" and v.target:
            # The app's supervisor reconnects too: from here, one try per stick per 10 minutes.
            if self._clock() - self.reconnected.get(v.target, -1e9) < RECONNECT_EVERY_S:
                return "reconnected recently; waiting"
            self.reconnected[v.target] = self._clock()
            try:
                subprocess.run([ADB, "disconnect", v.target], capture_output=True, timeout=10)
                r = subprocess.run([ADB, "connect", v.target], capture_output=True, text=True, timeout=15)
                return (r.stdout or r.stderr).strip()[:120]
            except (subprocess.TimeoutExpired, OSError) as e:
                return f"no answer from {v.target} ({type(e).__name__}): likely powered off"
        if v.action == "prune_disk":
            return f"freed {prune_caches()} MB"
        return "unknown action skipped"

    def _push(self, text: str) -> None:
        topic = os.environ.get("REDZONE_NTFY_TOPIC") or _ntfy_topic()
        if topic and not self.dry_run:
            try:
                req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode()[:1000], method="POST",
                                             headers={"Title": "RedZone"})
                urllib.request.urlopen(req, timeout=8).close()
            except Exception as e:
                log.info("agent: push failed: %s", e)

    def tick(self) -> dict:
        o = self.observe()
        v = self.decide(o)
        done = self.act(v, o)
        row = {"ts": round(self._clock(), 1), "kind": "check", "action": v.action, "source": v.source,
               "why": v.why, "target": v.target, "result": done,
               "obs": {"api_ok": o.api_ok, "live": o.live_games, "crunch": o.crunch_on_screen,
                       "log_age_s": o.log_age_s, "disk_mb": o.disk_free_mb, "offline": o.sticks_offline,
                       "errors": len(o.errors)}}
        if v.action != "none" or int(self._clock()) % 900 < CHECK_S:   # quiet checks: one row per 15 min
            _journal(row)
        return row


def deploy_in_progress(now: float) -> bool:
    """scripts/deploy.py writes this marker while it restarts the app and watches its health."""
    try:
        m = json.loads((DATA / "deploy" / "in_progress.json").read_text())
        return float(m.get("until", 0)) > now
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _ntfy_topic() -> str:
    try:
        import yaml  # only if the app's venv is on the path; else no push
        cfg = yaml.safe_load((DATA / "config.yaml").read_text()) or {}
        return str(((cfg.get("supervisor") or {}).get("ntfy_topic")) or "")
    except Exception:
        return ""


def prune_caches(target_mb: float = 2000.0) -> int:
    """Free space from caches the app rebuilds on its own; oldest first. Never recordings or config."""
    freed = 0.0
    for d in (DATA / "reels" / "clips", DATA / "imgcache"):
        try:
            files = sorted((p for p in d.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
        except OSError:
            continue
        for p in files:
            if freed >= target_mb:
                break
            try:
                freed += p.stat().st_size / 1e6
                p.unlink()
            except OSError:
                pass
    return int(freed)


def _journal(row: dict) -> None:
    try:
        with JOURNAL.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-model", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    w = Watchdog(dry_run=a.dry_run, use_model=not a.no_model)
    while True:
        t0 = time.time()
        try:
            row = w.tick()
            log.info("%s (%s): %s -> %s", row["action"], row["source"], row["why"], row["result"])
        except Exception:
            log.exception("agent: check failed")
        if a.once:
            break
        time.sleep(max(5.0, CHECK_S - (time.time() - t0)))


if __name__ == "__main__":
    main()
