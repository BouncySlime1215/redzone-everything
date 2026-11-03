"""RedZone watchdog: a separate, always-on process that keeps the app healthy without a person.

Every CHECK_S it looks at the app from outside (its API, its log, the disk, the sticks the app
reports), decides with agent/policy.py (hard rules first, the local model only for errors nobody
has written a rule for), and acts within a fixed, guarded set:

    none · notify (data/agent.jsonl, shown on the phone; ntfy push if a topic is set)
    restart_service (launchctl kickstart) · reconnect_stick (adb disconnect + connect)
    prune_disk (only caches the app rebuilds: catch-up clips, logo images)
    resume_player (adb MEDIA_PLAY, which never pauses a playing player)
    clear_prompt (adb OK on the "still watching?" button, then MEDIA_PLAY)
    retune_stick · refresh_feed · restart_component (the app's POST /api/agent/fix; when the app
        has no such endpoint, or the fix just ran and didn't help, it becomes a notify)
    safe_mode_off (the app's own Resume, only when safe mode is on and nobody paused it)

Every fix has its own rate limit (FIX_LIMITS) on top of policy.guard().

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
import urllib.error
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
# fix -> (minimum seconds between two on the same target, max per hour on the same target)
FIX_LIMITS = {"resume_player": (120.0, 6), "clear_prompt": (120.0, 6), "retune_stick": (600.0, 3),
              "refresh_feed": (600.0, 3), "restart_component": (600.0, 3), "safe_mode_off": (600.0, 3)}
KEY_OK, KEY_PLAY = "23", "126"   # KEYCODE_DPAD_CENTER, KEYCODE_MEDIA_PLAY (play only, never toggles)
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
        self.fixes: dict[tuple[str, str], list[float]] = {}
        self.safe_since: float | None = None
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
            for s in sticks:
                if s.get("role") in ("on screen", "main") and s.get("host") and not o.on_screen:
                    o.on_screen = s["host"]
                elif s.get("role") == "standby" and s.get("host"):
                    o.standby = s["host"]
            mode = st.get("mode") or {}
            o.safe_mode, o.paused = bool(mode.get("safe")), bool(mode.get("paused"))
            ages = [x.get("age_s") for x in ((st.get("health") or {}).get("sources") or [])
                    if isinstance(x, dict) and not x.get("benched") and isinstance(x.get("age_s"), (int, float))]
            o.feed_stale_s = float(max(ages)) if ages and o.live_games else 0.0
        if o.safe_mode:
            self.safe_since = self.safe_since if self.safe_since is not None else now
            o.safe_min = round((now - self.safe_since) / 60, 1)
        else:
            self.safe_since = None
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
        if v.action in FIX_LIMITS:
            return self.fix(v, o)
        return "unknown action skipped"

    # ---- fixes (v2) ------------------------------------------------------------------------
    def _allowed(self, action: str, target: str) -> bool:
        gap, per_hour = FIX_LIMITS[action]
        now = self._clock()
        done = [t for t in self.fixes.get((action, target), []) if now - t < 3600]
        self.fixes[(action, target)] = done
        return not (done and now - done[-1] < gap) and len(done) < per_hour

    def _tell(self, why: str) -> str:
        return self.act(Verdict("notify", why, source="guard"), Obs())

    def fix(self, v: Verdict, o: Obs) -> str:
        target = v.target or ("feeds" if v.action == "refresh_feed" else "app")
        if not self._allowed(v.action, target):
            return self._tell(f"{v.action} on {target} already tried recently and the problem is back: {v.why}")
        self.fixes.setdefault((v.action, target), []).append(self._clock())
        if v.action == "resume_player":
            return self._adb_keys(target, KEY_PLAY)
        if v.action == "clear_prompt":
            return self._adb_keys(target, KEY_OK, KEY_PLAY)
        if v.action == "safe_mode_off":
            st = fetch_state() or {}
            mode = st.get("mode") or {}
            if not mode.get("safe"):
                return "safe mode already off"
            if mode.get("paused"):
                return "paused by a person: left alone"
            r = _post("/api/action", {"action": "resume"})
            return "left safe mode" if r and r.get("ok") else self._tell(f"couldn't leave safe mode: {v.why}")
        body = {"retune_stick": {"fix": "retune", "target": target},
                "refresh_feed": {"fix": "refresh_feeds", "target": "feeds"},
                "restart_component": {"fix": "restart", "target": target}}[v.action]
        r = _post("/api/agent/fix", body)
        if r and r.get("ok"):
            return str(r.get("message") or f"{v.action} {target}: done")[:160]
        return self._tell(f"{v.why} (couldn't {v.action.replace('_', ' ')} from here: "
                          f"{(r or {}).get('message', 'the app has no fix endpoint')})")

    def _adb_keys(self, host: str, *keys: str) -> str:
        try:
            for i, k in enumerate(keys):
                if i:
                    time.sleep(1.0)
                r = subprocess.run([ADB, "-s", host, "shell", "input", "keyevent", k],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode != 0:
                    return f"adb keyevent {k} on {host} failed: {(r.stderr or r.stdout).strip()[:100]}"
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"no answer from {host} ({type(e).__name__})"
        return f"pressed {'+'.join(keys)} on {host}"

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
        if self._model is not None and o.live_games:
            try:   # keeps the shared local model loaded through a live night (agent/llm_server.py)
                from agent import llm_server
                llm_server.call("/keepalive", {"live_games": o.live_games}, timeout=2)
            except Exception:
                pass
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


def _post(path: str, body: dict, timeout: float = 10.0) -> dict | None:
    """POST to the app's local API; None when it doesn't answer, {"ok": False} on an HTTP error."""
    try:
        req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "message": f"HTTP {e.code}"}
    except Exception:
        return None


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
