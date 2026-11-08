"""RedZone watchdog: a separate, always-on process that keeps the app healthy without a person.

Every CHECK_S it looks at the app from outside (its API, its log, the disk, the sticks the app
reports), decides with agent/policy.py (hard rules first, the local model only for errors nobody
has written a rule for), and acts within a fixed, guarded set:

    none · notify (data/agent.jsonl, shown on the phone; ntfy push if a topic is set)
    restart_service (launchctl kickstart) · reconnect_stick (adb disconnect + connect)
    prune_disk (only caches the app rebuilds: catch-up clips, logo images)
    resume_player · clear_prompt · retune_stick (the stick on screen, only while its screen is dead)
    refresh_feed · restart_component (feeds, fastcast) · safe_mode_off
        (the last six are run by the app: POST /api/agent/fix, off unless hardening.watchdog; when
        the app says no, the watchdog tells a person instead)

Every verdict names one of the 13 fault kinds (agent/policy.py KIND_HELP). Every action that ran is
followed up: if its problem is gone within SETTLE_CHECKS checks it worked, otherwise it failed and
the next rung of the ladder (or a person) gets the problem. Success rates per action go to the
journal ("fix_result" rows and "fixes" on each check row).

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

from agent.policy import (APP_FIXES, LOW_DISK_MB, STALE_FEED_S, STICK_FIXES, SYSTEM, Obs, Verdict, guard, known,
                          parse_answer, rules)
from redzone.governor import Governor, free_mlx_cache, load_options
from redzone.memory import Memory, worked
from redzone.platform.autofix import Filer

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
MODEL_IDLE_S = 900.0         # the local model is dropped after 15 idle minutes (~1 GB back to the Mac)
PRUNE_EVERY_S = 1800.0       # prune_disk at most once per 30 min (it only frees caches the app rebuilds)
MAX_ACTIONS_HOUR = 12        # restarts + reconnects + prunes, all kinds together: past this, notify only
SETTLE_CHECKS = 2            # an action's problem still there this many checks later: it didn't work
FAILED_TTL_S = 1800.0        # a failed fix is skipped on the same problem for this long
# app fix -> (seconds between two on the same target, max per hour on the same target)
FIX_LIMITS = {"resume_player": (120.0, 6), "clear_prompt": (120.0, 6), "retune_stick": (300.0, 3),
              "refresh_feed": (300.0, 4), "restart_component": (600.0, 3), "safe_mode_off": (600.0, 3)}
MEMORY_CHECKS = 3            # the app over its memory cap this many checks in a row: act
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
    # Project memory (redzone/memory.py): lessons before acting, a pending lesson after a fix.
    # Set by main() only, so a watchdog built in a test never writes to data/.
    memory: Memory | None = None

    def __init__(self, dry_run: bool = False, use_model: bool = True, clock=time.time,
                 governor: Governor | None = None):
        self.dry_run = dry_run
        self.use_model = use_model
        self._clock = clock
        self.fail_streak = 0
        self.restarts: list[float] = []
        self.offline_since: dict[str, float] = {}
        self.notified: dict[str, float] = {}
        self.reconnected: dict[str, float] = {}
        self.pruned_at = -1e9
        self.actions: list[float] = []               # when each device/service action ran (the hourly cap)
        self.asked: dict[tuple, tuple[float, Verdict]] = {}
        self._model = None
        self._model_used = 0.0
        self.mem_streak = 0
        self.fix_runs: dict[tuple[str, str], list[float]] = {}   # (fix, target) -> when it ran (FIX_LIMITS)
        self.pending: list[dict] = []                # actions that ran, waiting to see if they worked
        self.failed: dict[str, tuple[float, str]] = {}   # action -> (when, target) it last failed on the current problem
        self.fix_stats: dict[str, list[int]] = {}    # action -> [worked, tried]
        # The resource governor (redzone/governor.py): memory caps, and no model while the Mac is critical.
        self.gov = governor or Governor(load_options(ROOT), root=DATA if DATA.exists() else ROOT, clock=clock)
        # Problems the rules and the local model can't fix go to Claude as a GitHub issue (docs/auto-fix.md).
        # Set by main() only: a watchdog built in a test never files real issues.
        self.state: dict | None = None
        self.autofix: Filer | None = None

    # ---- seeing ----------------------------------------------------------------------------
    def observe(self) -> Obs:
        now = self._clock()
        st = self.state = fetch_state()
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
        if st is None:
            o.launchd_exit = launchd_last_exit()
        self.gov.refresh()
        o.errors = recent_errors(now=now)
        if st:
            games = st.get("games") or []
            o.live_games = sum(1 for g in games if g.get("phase") == "live")
            cur = st.get("now") or {}
            o.crunch_on_screen = bool(cur and cur.get("phase") == "live" and (cur.get("lock") or cur.get("mine")))
            net = ((st.get("health") or {}).get("network")) or {}
            o.away = net.get("where") == "away"
            o.route_issue = (net.get("message") or "")[:300] if net.get("routes_broken") else ""
            sticks = ((st.get("health") or {}).get("sticks")) or []
            off = [s.get("host") for s in sticks if s.get("state") == "offline" and s.get("host")]
            for h in off:
                self.offline_since.setdefault(h, now)
            for h in list(self.offline_since):
                if h not in off:
                    del self.offline_since[h]
            o.sticks_offline = sorted(off, key=lambda h: self.offline_since[h])
            o.offline_min = round((now - min((self.offline_since[h] for h in off), default=now)) / 60, 1)
            ag = ((st.get("health") or {}).get("agent")) or {}
            try:
                o.on_screen = str(ag.get("on_screen") or "")
                o.screen = str(ag.get("screen") or "")
                o.screen_dead_s = float(ag.get("screen_dead_s") or 0.0)
                o.safe_mode = bool(ag.get("safe_mode"))
                o.safe_min = float(ag.get("safe_min") or 0.0)
                o.paused = bool(ag.get("paused"))
                o.feed_stale_s = float(ag.get("feed_stale_s") or 0.0)
                o.net_down = bool(ag.get("net_down"))
                o.components = [str(c) for c in ag.get("components") or []]
                o.person = str(ag.get("person") or "")[:200]
            except (TypeError, ValueError) as e:   # an older or odd app: the rest of the checks still run
                log.info("agent: unreadable agent block: %s", e)
        return o

    # ---- deciding ----------------------------------------------------------------------------
    def model(self):
        if self._model is None:
            from agent.llm import ADAPTER, DEFAULT_MODEL, LocalModel
            self._model = LocalModel(DEFAULT_MODEL, ADAPTER)
        self._model_used = self._clock()
        return self._model

    def drop_model(self, why: str) -> bool:
        """Free the local model's memory (it reloads in a few seconds the next time it's needed)."""
        if self._model is None:
            return False
        self._model = None
        import gc
        gc.collect()
        free_mlx_cache()
        log.info("agent: dropped the local model (%s)", why)
        return True

    def memory_verdict(self, o: Obs) -> Verdict | None:
        """The governor's memory caps. Kept out of policy.rules() on purpose: Obs.text() is the
        model's prompt, and the trained adapter expects its fields as they are."""
        opts = self.gov.opts
        if not opts["enabled"]:
            return None
        cap = float(opts["models_mb"])
        models = self.gov.models_mb()
        if models > cap:
            if self.drop_model(f"model processes at {models:.0f} MB, cap {cap:.0f}"):
                return None   # measured again at the next check
            return Verdict("notify", f"the local model server is using {models:.0f} MB (cap {cap:.0f} MB)",
                           source="governor")
        svc = self.gov.service_mb()
        hard, soft = float(opts["service_hard_mb"]), float(opts["service_soft_mb"])
        self.mem_streak = self.mem_streak + 1 if svc is not None and svc > hard else 0
        if self.mem_streak >= MEMORY_CHECKS:
            return Verdict("restart_service", f"the app is using {svc:.0f} MB of memory (cap {hard:.0f} MB)",
                           source="governor")
        if svc is not None and svc > soft:
            return Verdict("notify", f"the app is using {svc:.0f} MB of memory (warning at {soft:.0f} MB)",
                           source="governor")
        return None

    def decide(self, o: Obs) -> Verdict:
        v = rules(o)
        if v is None or v.action == "none":
            v = self.memory_verdict(o) or v
        if v is None:
            unknown = tuple(sorted(s for s in o.errors if known(s) is None))
            hit = self.asked.get(unknown)
            if hit and self._clock() - hit[0] < MODEL_CACHE_S:
                v = hit[1]
            elif self.use_model and self.gov.allow("llm"):
                msgs = [{"role": "system", "content": SYSTEM}]
                hint = self.memory.hints(unknown) if self.memory else ""
                if hint:   # only when a lesson matches: the usual prompt stays exactly as trained
                    msgs.append({"role": "system", "content": hint})
                try:
                    ans = self.model().chat(msgs + [{"role": "user", "content": o.text()}])
                    v = parse_answer(ans) or Verdict("notify", f"new error the model couldn't judge: {unknown[0][:120]}",
                                                     source="guard")
                except Exception as e:   # no model, out of memory...: a person hears about the new error
                    log.warning("agent: model unavailable: %s", e)
                    v = Verdict("notify", f"new error: {unknown[0][:120]}", source="guard")
                self.asked[unknown] = (self._clock(), v)
            else:   # no model, or the Mac is critical (hot, low battery, out of memory): rules only
                seen = self.memory.titles(unknown) if self.memory else []
                tip = f" (lesson: {seen[0][:60]})" if seen else ""
                v = Verdict("notify", f"new error: {unknown[0][:120]}{tip}", source="guard")
        return guard(v, o)

    # ---- doing ----------------------------------------------------------------------------
    def act(self, v: Verdict, o: Obs) -> str:
        """Do the verdict, rate-limited, and never raise: whatever happens is a result string the
        journal keeps (tick() writes one row for every action)."""
        try:
            return self._act(v, o)
        except Exception as e:   # an action that blew up is logged, never the end of the watchdog
            log.warning("agent: %s failed: %s", v.action, e)
            return f"{v.action} failed: {type(e).__name__}: {str(e)[:100]}"

    def _capped(self) -> bool:
        now = self._clock()
        self.actions = [t for t in self.actions if now - t < 3600]
        return len(self.actions) >= MAX_ACTIONS_HOUR

    def _act(self, v: Verdict, o: Obs) -> str:
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
        if self._capped():
            # Something keeps coming back: a person hears about it once an hour instead of a loop.
            note = Verdict("notify", f"watchdog hit {MAX_ACTIONS_HOUR} actions this hour; holding back "
                           "restarts, reconnects and prunes until the hour clears", source="guard")
            told = self._act(note, o)
            return f"rate-limited ({MAX_ACTIONS_HOUR}/h): {told}"
        if v.action == "prune_disk" and self._clock() - self.pruned_at < PRUNE_EVERY_S:
            return "pruned recently; waiting"
        if v.action == "restart_service":
            if deploy_in_progress(self._clock()):
                return "deploy in progress: scripts/deploy.py is restarting and watching the app"
            self.restarts.append(self._clock())
            self.actions.append(self._clock())
            self.mem_streak = 0
            try:
                r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
                                   capture_output=True, text=True, timeout=30)
            except (subprocess.TimeoutExpired, OSError) as e:
                return f"restart failed: {e}"
            self._started(v)
            self._push(f"restarted the app: {v.why}")
            return "restarted" if r.returncode == 0 else f"restart failed: {r.stderr.strip()[:120]}"
        if v.action == "reconnect_stick" and v.target:
            # The app's supervisor reconnects too: from here, one try per stick per 10 minutes.
            if self._clock() - self.reconnected.get(v.target, -1e9) < RECONNECT_EVERY_S:
                return "reconnected recently; waiting"
            self.reconnected[v.target] = self._clock()
            self.actions.append(self._clock())
            self._started(v)
            try:
                subprocess.run([ADB, "disconnect", v.target], capture_output=True, timeout=10)
                r = subprocess.run([ADB, "connect", v.target], capture_output=True, text=True, timeout=15)
                return (r.stdout or r.stderr).strip()[:120]
            except (subprocess.TimeoutExpired, OSError) as e:
                return f"no answer from {v.target} ({type(e).__name__}): likely powered off"
        if v.action == "prune_disk":
            self.pruned_at = self._clock()
            self.actions.append(self._clock())
            self._started(v)
            return f"freed {prune_caches()} MB"
        if v.action in APP_FIXES:
            return self._app_fix(v, o)
        return "unknown action skipped"

    # ---- app fixes (POST /api/agent/fix) and whether they worked ------------------------------
    def _app_fix(self, v: Verdict, o: Obs) -> str:
        gap, per_hour = FIX_LIMITS[v.action]
        now = self._clock()
        key = (v.action, v.target)
        runs = self.fix_runs[key] = [t for t in self.fix_runs.get(key, []) if now - t < 3600]
        if runs and now - runs[-1] < gap:
            return f"{v.action} ran {int(now - runs[-1])} s ago; waiting"
        if len(runs) >= per_hour:
            told = self._act(Verdict("notify", f"{v.action} ran {per_hour}x this hour on {v.target or 'the app'} "
                                     f"and the problem is back ({v.why})", source="guard", kind=v.kind), o)
            return f"{v.action} limit reached: {told}"
        r = _post("/api/agent/fix", {"fix": v.action, "target": v.target})
        msg = str((r or {}).get("message", ""))[:160]
        if r is None or msg.startswith(("off:", "unknown fix", "rate-limited", "local only")):
            # The app can't or won't (old build, flag off, its own limit): a person hears about it.
            why = msg or "the app has no fix endpoint"
            told = self._act(Verdict("notify", f"wanted to {v.action.replace('_', ' ')} ({v.why}) but {why}",
                                     source="guard", kind=v.kind), o)
            return f"not run ({why}): {told}"
        runs.append(now)
        self.actions.append(now)
        self._started(v)
        if not r.get("ok"):
            self._settled(self.pending.pop(), False, msg)
        return f"{v.action}: {msg or ('done' if r.get('ok') else 'failed')}"

    def _started(self, v: Verdict) -> None:
        self.pending.append({"action": v.action, "target": v.target, "kind": v.kind, "at": self._clock(),
                             "checks": 0})

    @staticmethod
    def _cleared(p: dict, o: Obs) -> bool:
        """Is the problem this action was for gone?"""
        a = p["action"]
        if a == "restart_service":
            return o.api_ok
        if a == "reconnect_stick":
            return p["target"] not in o.sticks_offline
        if a == "prune_disk":
            return o.disk_free_mb >= LOW_DISK_MB
        if a in STICK_FIXES:
            return not o.screen or o.on_screen != p["target"]
        if a == "restart_component" and p["target"] != "feeds":
            return p["target"] not in o.components
        if a in ("refresh_feed", "restart_component"):
            return o.feed_stale_s < STALE_FEED_S or not o.live_games
        if a == "safe_mode_off":
            return not o.safe_mode
        return True

    def _settled(self, p: dict, ok: bool, detail: str = "") -> None:
        st = self.fix_stats.setdefault(p["action"], [0, 0])
        st[1] += 1
        st[0] += int(ok)
        if ok:
            self.failed.pop(p["action"], None)
        else:
            self.failed[p["action"]] = (self._clock(), p["target"])
        if not self.dry_run:
            _journal({"ts": round(self._clock(), 1), "kind": "fix_result", "action": p["action"],
                      "target": p["target"], "fault": p["kind"], "worked": ok,
                      "after_s": round(self._clock() - p["at"], 1), "detail": detail[:160]})

    def settle(self, o: Obs) -> None:
        """Follow up on actions that ran: worked (problem gone) or failed (still there after
        SETTLE_CHECKS checks). Then hand the rules the fixes that already failed on this problem."""
        still = []
        for p in self.pending:
            p["checks"] += 1
            if self._cleared(p, o):
                self._settled(p, True)
            elif p["checks"] >= SETTLE_CHECKS:
                self._settled(p, False, "problem still there")
            else:
                still.append(p)
        self.pending = still
        now = self._clock()
        for a, (t, target) in list(self.failed.items()):
            if now - t > FAILED_TTL_S or self._cleared({"action": a, "target": target, "kind": ""}, o):
                del self.failed[a]
        # The rules move on to the next rung only once a fix failed, and wait while one is being judged.
        o.failed = sorted(self.failed)
        o.trying = sorted({p["action"] for p in self.pending})

    def fix_rates(self) -> dict[str, str]:
        return {a: f"{w}/{n}" for a, (w, n) in sorted(self.fix_stats.items())}

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
        if self._model is not None:
            if self._clock() - self._model_used > MODEL_IDLE_S:
                self.drop_model("idle")
            elif not self.gov.allow("llm"):
                self.drop_model(f"the Mac is {self.gov.level}: {self.gov.why()}")
        o = self.observe()
        self.settle(o)
        v = self.decide(o)
        done = self.act(v, o)
        if self.autofix is not None and not self.dry_run:
            try:
                filed = self.autofix.consider(o, sorted(s for s in o.errors if known(s) is None))
            except Exception as e:   # filing must never stop the watchdog
                log.warning("agent: auto-fix filing failed: %s", e)
                filed = None
            if filed:
                _journal({**filed, "ts": round(self._clock(), 1), "kind": "autofix", "trigger": filed.get("kind")})
        row = {"ts": round(self._clock(), 1), "kind": "check", "action": v.action, "source": v.source,
               "why": v.why, "target": v.target, "result": done, "fault": v.kind, "fixes": self.fix_rates(),
               "obs": {"api_ok": o.api_ok, "live": o.live_games, "crunch": o.crunch_on_screen,
                       "log_age_s": o.log_age_s, "disk_mb": o.disk_free_mb, "offline": o.sticks_offline,
                       "errors": len(o.errors), "governor": self.gov.level,
                       "app_mb": self.gov.service_mb(), "models_mb": round(self.gov.models_mb())}}
        if self.memory is not None and not self.dry_run and worked(v.action, done):
            try:   # a fix that worked becomes a pending lesson; it reaches lessons.md by PR
                sig = max(o.errors, key=o.errors.get) if o.errors else ""
                self.memory.record(sig, v.action, done, why=v.why)
            except Exception as e:   # memory must never stop the watchdog
                log.warning("agent: memory record failed: %s", e)
        if v.action != "none" or int(self._clock()) % 900 < CHECK_S:   # quiet checks: one row per 15 min
            _journal(row)
        return row


def launchd_last_exit(label: str = LABEL) -> int | None:
    """The app's launchd job's last exit code (`launchctl print`), None when unknown (not macOS, no
    job, no answer). Read only while the app's API is down, so a healthy night never runs it."""
    try:
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                           capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"last exit code = (-?\d+)", r.stdout or "")
    return int(m.group(1)) if m else None


# Mirrors scripts/deploy.py ACTIVE (the deploy states that restart or watch the app).
DEPLOY_ACTIVE = frozenset({"switching", "watching", "rolling_back"})


def deploy_in_progress(now: float) -> bool:
    """True while scripts/deploy.py restarts the app or watches its health. Reads the deploy machine in
    state.json first (the one source of truth, redzone/machines/platform.py "deploy.lifecycle"), then the
    older in_progress.json marker. Kept dependency-free: the watchdog runs in its own venv."""
    try:
        st = json.loads((DATA / "deploy" / "state.json").read_text())
        m = st.get("machine") if isinstance(st, dict) else None
        if isinstance(m, dict) and m.get("state"):
            return m["state"] in DEPLOY_ACTIVE and float(m.get("until") or 0) > now
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        m = json.loads((DATA / "deploy" / "in_progress.json").read_text())
        return float(m.get("until", 0)) > now
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _post(path: str, body: dict, timeout: float = 60.0) -> dict | None:
    """POST JSON to the app; None when it isn't there or has no such endpoint (an older build).
    The timeout covers a stick re-tune (a deep link can take ~30 s)."""
    try:
        req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        log.info("agent: POST %s failed: %s", path, e)
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
    Watchdog.memory = Memory(ROOT)
    w = Watchdog(dry_run=a.dry_run, use_model=not a.no_model)
    w.autofix = Filer(ROOT, health=lambda: (w.state or {}).get("health"), notify=w._push)
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
