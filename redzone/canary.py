"""[Canary, Nick's Nov 7 order item 8] A daily stick self-test before games, on the HIDDEN stick only, that fails loud.

    .venv/bin/python scripts/canary.py --dry-run   # preflight only: API, no game soon, which stick is hidden. No keys.
    .venv/bin/python scripts/canary.py             # the real test (exit 0 pass, 1 FAIL, 2 deferred, 4 WARN)

Never while a game is live or starts within canary.soon_s (90 min): it asks the running app (/api/state) before it
touches anything, and a poller keeps asking every few seconds during the test. If a game goes live, or the stick
stops being the hidden ("standby") one, it stops at once and puts the stick back on live TV. It never touches the
stick on screen, never presses HOME, never stops the app's service.

Checks, in order (each logged; the first failure ends the test, then the stick is put back on live):
  api          the app answers /api/state                         (down -> FAIL: nothing guards the night)
  idle         no game live, none within soon_s                   (-> deferred, exit 2, nothing touched)
  standby      a stick has role "standby" and is connected        (two-stick mode off / stick offline -> FAIL)
  channels     data/channels.json exists and has watch codes for both channels (it is gitignored: a scratch
               worktree has none -> FAIL before any key, also in --dry-run)
  adb          adb connect + shell to that stick                  (adb drop -> FAIL)
  yttv_app     YouTube TV installed on it
  tune_away    tune canary.away_channel: the canary channel's tune is then fresh at the live edge (#391: a
               same-channel deep link does NOT bring a stick that is behind back to live; it read 23 for 90)
  tune         tune canary.channel and confirm playing (prompts such as "Who's watching" / "Are you still
               watching" are cleared by the tuner; one it can't clear fails here, with the tuner's steps)
  join         redzone.tuners.join to canary.target_s (60 -> 3 LEFTs, predicted 56.7 s) with the overlay label
               reader: MEASURED landing required (an unread label = the reader or the overlay changed -> FAIL),
               |measured - predicted| <= canary.tol_s (6 s)
  relink       tune canary.away_channel again (fresh, at live) and confirm playing
  calib        join_calib SHIFT alerts after this row (last 3 joins off the same side by > 5 s) -> WARN

One row per run in data/canary.jsonl (numbers, check names and channel names only: never an address, never frames).
FAIL and WARN go to the phone (data/agent.jsonl, which the app shows, and the ntfy topic if one is set) with the
first failed check and the runbook: docs/memory/runbooks/canary.md. A pass is journalled quietly.

Why: first added in ad542e15 (2026-11-07) as "Join self-calibration per channel/stick (bounded, flag off) + daily hidden-stick canary that fail..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("redzone.canary")

CANARY_DEFAULTS = {"soon_s": 5400.0, "channel": "espn", "away_channel": "fs1", "target_s": 60.0, "tol_s": 6.0,
                   "settle_s": 8.0, "prime": False, "poll_s": 3.0}
EXIT = {"PASS": 0, "FAIL": 1, "DEFERRED": 2, "WARN": 4}
RUNBOOK = "docs/memory/runbooks/canary.md"


def _c(cfg, key: str):
    v = cfg.get(f"canary.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return CANARY_DEFAULTS[key] if v is None else v


def games_busy(st: dict | None, now: float, soon_s: float) -> str:
    """Why the sticks must not be touched now ("" = free). Any live game, or one starting within soon_s."""
    if st is None:
        return "the app's API is down: can't tell whether a game is on"
    for g in st.get("games") or []:
        name = g.get("matchup") or g.get("id") or "a game"
        if g.get("phase") == "live":
            return f"{name} is live"
        t = g.get("start_time")
        if g.get("phase") == "pre" and isinstance(t, (int, float)) and now <= t <= now + soon_s:
            return f"{name} starts in {int((t - now) / 60)} min"
    return ""


def standby_row(st: dict | None) -> dict | None:
    """The hidden stick's row from /api/state (role "standby" = two-stick mode's hidden stick)."""
    rows = ((st or {}).get("health") or {}).get("sticks") or []
    hits = [r for r in rows if r.get("role") == "standby"]
    return hits[0] if len(hits) == 1 else None


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    took_s: float = 0.0
    warn: bool = False


@dataclass
class CanaryResult:
    status: str = "FAIL"              # PASS | FAIL | WARN | DEFERRED
    checks: list[Check] = field(default_factory=list)
    join: dict | None = None          # the join row (numbers only)
    reason: str = ""
    stick: str = ""                   # name only
    took_s: float = 0.0

    @property
    def first_fail(self) -> Check | None:
        return next((c for c in self.checks if not c.ok and not c.warn), None)

    def row(self) -> dict:
        return {"ts": round(time.time(), 1), "status": self.status, "reason": self.reason[:200], "stick": self.stick,
                "took_s": round(self.took_s, 1),
                "checks": [{"name": c.name, "ok": c.ok, "warn": c.warn, "detail": c.detail[:160],
                            "took_s": round(c.took_s, 1)} for c in self.checks],
                "join": self.join}

    def phone_text(self) -> str:
        if self.status == "PASS":
            j = self.join or {}
            return (f"Canary passed on {self.stick}: join {j.get('target_s')} s landed {j.get('measured_s')} s "
                    f"(predicted {j.get('predicted_s')}).")
        if self.status == "DEFERRED":
            return f"Canary deferred: {self.reason}"
        f = self.first_fail
        head = "Canary WARNING" if self.status == "WARN" else "Canary FAILED"
        what = f"{f.name}: {f.detail}" if f else self.reason
        return f"{head} on {self.stick or 'the hidden stick'}: {what}. Runbook: {RUNBOOK}"


def done_today(path: Path, now: float | None = None) -> bool:
    """A PASS or WARN row already in the journal for today (local date)."""
    today = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return False
    for line in reversed(lines[-50:]):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("status") in ("PASS", "WARN") and time.strftime("%Y-%m-%d", time.localtime(r.get("ts", 0))) == today:
            return True
    return False


def _safe(name: str) -> str:
    from redzone.tuners.join_calib import safe_name
    return safe_name(name)


@dataclass
class Env:
    """Everything the canary touches, injectable for tests."""
    fetch_state: Callable[[], Awaitable[dict | None]]
    make_stick: Callable[[str], Any]
    tune: Callable[[Any, str], Awaitable[Any]]          # (stick, channel) -> TuneResult-like (.ok, .error, .steps)
    playing: Callable[[Any], Awaitable[bool]]
    do_join: Callable[..., Awaitable[Any]]              # join.join-like
    make_probe: Callable[[], Any] | None = None          # LabelProbe-like (arm, overlay_reader, __call__)
    # [Nick's rig note, Nov 7] which channels have no YouTube TV watch code ("" problems = all present), or a sentence
    # naming what is missing (data/channels.json is gitignored: a scratch worktree has none)
    missing_codes: Callable[[list[str]], str] | None = None
    push: Callable[[str, str], None] = lambda text, status: None
    journal_path: Path | None = None
    joins_path: Path | None = None
    now: Callable[[], float] = time.time
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


class Canary:
    def __init__(self, cfg, env: Env):
        self.cfg, self.env = cfg, env
        self.res = CanaryResult()
        self.host: str | None = None
        self._hidden_ok = True
        self._why_stop = ""
        self._poller: asyncio.Task | None = None

    # ------------------------------------------------------------------ guards
    def still_hidden(self) -> bool:
        return self._hidden_ok

    async def _poll(self) -> None:
        soon = float(_c(self.cfg, "soon_s"))
        while True:
            await self.env.sleep(float(_c(self.cfg, "poll_s")))
            st = await self.env.fetch_state()
            busy = games_busy(st, self.env.now(), soon)
            row = standby_row(st)
            if busy:
                self._hidden_ok, self._why_stop = False, busy
            elif row is None or row.get("host") != self.host:
                self._hidden_ok, self._why_stop = False, "the stick is no longer the hidden one"
            if not self._hidden_ok:
                return

    def _add(self, name: str, ok: bool, detail: str = "", t0: float | None = None, warn: bool = False) -> bool:
        took = self.env.clock() - t0 if t0 is not None else 0.0
        self.res.checks.append(Check(name, ok, detail, took, warn))
        return ok

    def _guard(self, name: str) -> bool:
        """Stop (as a deferral, not a fault) when a game went live or the stick stopped being hidden."""
        if self._hidden_ok:
            return True
        self._add(name, False, f"stopped: {self._why_stop}")
        self.res.status, self.res.reason = "DEFERRED", self._why_stop
        return False

    # ------------------------------------------------------------------ the test
    async def run(self, dry_run: bool = False) -> CanaryResult:
        t_start = self.env.clock()
        try:
            await self._run(dry_run)
        except Exception as e:   # never leave without a row and a loud message
            log.exception("canary crashed")
            self._add("crash", False, f"{e!r}"[:160])
            self.res.status = "FAIL"
        finally:
            if self._poller is not None:
                self._poller.cancel()
        self.res.took_s = self.env.clock() - t_start
        if self.res.status == "FAIL" and not self.res.reason:
            f = self.res.first_fail
            self.res.reason = f"{f.name}: {f.detail}" if f else "failed"
        self._report(push=not dry_run)
        return self.res

    async def _run(self, dry_run: bool) -> None:
        cfg, env = self.cfg, self.env
        t0 = env.clock()
        st = await env.fetch_state()
        if not self._add("api", st is not None, "" if st is not None else "the app's API did not answer", t0):
            return
        busy = games_busy(st, env.now(), float(_c(cfg, "soon_s")))
        if busy:
            self._add("idle", False, busy)
            self.res.status, self.res.reason = "DEFERRED", busy
            return
        self._add("idle", True, "no game live or within the window")
        row = standby_row(st)
        if not self._add("standby", row is not None and row.get("state") == "connected",
                         "" if row is not None and row.get("state") == "connected" else
                         ("no single standby stick (two-stick mode off, or a stick offline)" if row is None
                          else f"standby stick is {row.get('state')}")):
            return
        self.host = row["host"]
        self.res.stick = _safe(row.get("name") or "")
        if env.missing_codes is not None:
            miss = env.missing_codes([str(_c(cfg, "away_channel")), str(_c(cfg, "channel"))])
            if not self._add("channels", not miss, miss):
                return
        if dry_run:
            self.res.status, self.res.reason = "PASS", "dry run: preflight only, nothing touched"
            return
        self._poller = asyncio.ensure_future(self._poll())

        t0 = env.clock()
        stick = env.make_stick(self.host)
        try:
            up = await stick.connect()
        except Exception as e:
            up = False
            self._add("adb", False, f"{e!r}"[:120], t0)
            return
        if not self._add("adb", bool(up), "" if up else "adb connect / shell failed (adb drop, stick asleep or "
                                                        "rebooting)", t0):
            return
        try:
            await stick.identify()
        except Exception:
            pass
        pkg = (getattr(stick, "packages", None) or {}).get("youtubetv")
        if not self._add("yttv_app", bool(pkg), "" if pkg else "YouTube TV not found on the stick"):
            return
        stuck_on_screen = False
        try:
            ch, away = str(_c(cfg, "channel")), str(_c(cfg, "away_channel"))
            for name, channel in (("tune_away", away), ("tune", ch)):
                if not self._guard(name):
                    return
                t0 = env.clock()
                r = await env.tune(stick, channel)
                ok = bool(getattr(r, "ok", False))
                why = "" if ok else (getattr(r, "error", "") or "; ".join(getattr(r, "steps", [])[-3:]) or "tune failed")
                if not self._add(name, ok, f"{channel}: {why}" if why else channel, t0):
                    return
                gap = float((cfg.get("tuners.relink_gap_s", 0) if hasattr(cfg, "get") else 0) or 0)
                if name == "tune_away" and gap > 0:
                    await env.sleep(gap)   # [Nick Nov 7] back-to-back tunes failed 1/10; a short gap fixed it
            if not self._guard("tune"):
                return
            on = await env.playing(stick)
            if not self._add("playing", on, "" if on else f"{ch} is not playing after the tune (paused, a prompt "
                                                           "the tuner could not clear, or the app crashed?)"):
                return
            stuck_on_screen = True     # from here a failure leaves the stick behind live: relink in finally
            await env.sleep(float(_c(cfg, "settle_s")))
            if not self._guard("join"):
                return
            t0 = env.clock()
            probe = env.make_probe() if env.make_probe else None
            kw: dict = {}
            if probe is not None and probe.arm(stick):
                kw = {"read_overlay": probe.overlay_reader, "read_behind": probe}
            r = await env.do_join(stick, "youtubetv", float(_c(cfg, "target_s")), cfg=cfg, pkg=pkg,
                                  still_hidden=self.still_hidden, log_path=env.joins_path,
                                  label=self.res.stick, channel=ch, source="canary", **kw)
            row = r.row() if hasattr(r, "row") else {}
            self.res.join = {k: row.get(k) for k in ("target_s", "presses", "predicted_s", "measured_s", "error_s",
                                                     "pred_error_s", "bias_used_s", "bias_source", "took_s", "reason")}
            if not self._guard("join"):
                return
            if not r.ok:
                self._add("join", False, f"join failed: {r.reason}", t0)
                return
            if r.measured_s is None:
                self._add("join", False, "landing not measured (overlay label unread: reader, overlay or OCR "
                                         "changed?)", t0)
                return
            tol = float(_c(cfg, "tol_s"))
            pe = r.pred_error_s if r.pred_error_s is not None else 0.0
            if not self._add("join", abs(pe) <= tol, f"landed {r.measured_s} s, predicted {r.plan.predicted_s} s "
                                                     f"(off {pe:+.1f} s, limit {tol:g})", t0):
                return
        finally:
            # back to live TV on the hidden stick whatever happened (a fresh tune of another channel)
            if self._why_stop == "the stick is no longer the hidden one":
                self._add("relink", True, "skipped: the stick is on screen now, the app owns it")
            elif stuck_on_screen or any(c.name.startswith("tune") for c in self.res.checks):
                t0 = env.clock()
                try:
                    rr = await env.tune(stick, str(_c(cfg, "away_channel")))
                    ok = bool(getattr(rr, "ok", False)) and await env.playing(stick)
                except Exception as e:
                    ok = False
                    log.warning("canary relink: %r", e)
                self._add("relink", ok, "" if ok else "could not put the hidden stick back on live TV", t0)
        if self.res.first_fail is not None:
            return
        # calibration drift after this join
        try:
            from redzone.tuners import join_calib
            alerts = join_calib.JoinCalib.load(env.joins_path, cfg).alerts()
        except Exception as e:
            alerts = [f"calibration unreadable: {e!r}"[:120]]
        if alerts:
            self._add("calib", False, alerts[0], warn=True)
            self.res.status, self.res.reason = "WARN", alerts[0]
        else:
            self._add("calib", True)
            self.res.status = "PASS"

    # ------------------------------------------------------------------ out
    def _report(self, push: bool = True) -> None:
        row = self.res.row()
        path = self.env.journal_path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as f:
                    f.write(json.dumps(row) + "\n")
            except OSError as e:
                log.warning("canary journal: %r", e)
        if not push:
            return
        try:
            self.env.push(self.res.phone_text(), self.res.status)
        except Exception as e:
            log.warning("canary push: %r", e)


# ---------------------------------------------------------------------- the real environment (the Mac)

def phone_push(root: Path, cfg) -> Callable[[str, str], None]:
    """FAIL / WARN loud (agent.jsonl as an alert + ntfy with high priority); PASS / DEFERRED journal only."""
    def push(text: str, status: str) -> None:
        loud = status in ("FAIL", "WARN")
        try:
            with (root / "data/agent.jsonl").open("a") as f:
                f.write(json.dumps({"ts": round(time.time(), 1), "kind": "canary", "action": "notify" if loud else "note",
                                    "source": "canary", "why": text, "result": status}) + "\n")
        except OSError:
            pass
        topic = cfg.get("supervisor.ntfy_topic") if hasattr(cfg, "get") else None
        if loud and topic:
            try:
                req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode()[:1000], method="POST",
                                             headers={"Title": f"RedZone canary {status}", "Priority": "high",
                                                      "Tags": "rotating_light"})
                urllib.request.urlopen(req, timeout=8).close()
            except Exception:
                pass
    return push


def real_env(cfg, root: Path, api: str) -> Env:
    from redzone.channels import ChannelMap
    from redzone.tuners import join
    from redzone.tuners.adb import Stick, playback_ready
    from redzone.tuners.tuners import YouTubeTVTuner

    chans = ChannelMap()
    tuner = YouTubeTVTuner()

    async def fetch_state() -> dict | None:
        def get():
            try:
                with urllib.request.urlopen(f"{api}/api/state", timeout=6) as r:
                    return json.loads(r.read())
            except Exception:
                return None
        return await asyncio.to_thread(get)

    async def tune(stick, channel: str):
        code = chans.codes.get(channel)
        if not code:
            from types import SimpleNamespace
            return SimpleNamespace(ok=False, error=f"no YouTube TV watch code for {channel} in channels.json",
                                   steps=[])
        return await tuner.tune(stick, code, f"youtubetv:{channel}")

    async def playing(stick) -> bool:
        try:
            return playback_ready(await stick.playback(stick.packages.get("youtubetv")))
        except Exception:
            return False

    def make_probe():
        from redzone.tuners.yttv_label import LabelProbe
        return LabelProbe(cfg)

    def missing_codes(channels: list[str]) -> str:
        if not chans.path.exists():
            return (f"{chans.path.relative_to(root) if chans.path.is_relative_to(root) else chans.path} is missing "
                    "(gitignored): run from the Mac repo, or symlink its data/ into this checkout")
        gone = [c for c in channels if not chans.codes.get(c)]
        return f"no YouTube TV watch code for {', '.join(gone)} in data/channels.json" if gone else ""

    return Env(fetch_state=fetch_state, make_stick=lambda host: Stick(host=host), tune=tune, playing=playing,
               missing_codes=missing_codes,
               do_join=join.join, make_probe=make_probe, push=phone_push(root, cfg),
               journal_path=root / "data/canary.jsonl", joins_path=root / "data/joins.jsonl")
